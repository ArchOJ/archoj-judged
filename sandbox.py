import asyncio
from datetime import timedelta
import logging
from math import ceil
from pathlib import Path
from tempfile import mkdtemp
from typing import Dict, List, NamedTuple, Optional

from pydantic import ByteSize

from dto import JudgeStepDetails, Verdict
import linux
from pipeline import Step, Workspace


LOGGER = logging.getLogger(__name__)


class SandboxResult(NamedTuple):
    verdict: Verdict
    details: Optional[JudgeStepDetails]
    log: str


class TmpfsWorkspacesContextManager:
    def __init__(self, workspaces: List[Workspace], prefix: str, base_dir: Path):
        self._prefix = prefix
        self._base_dir = base_dir
        self._workspaces: List[Workspace] = workspaces
        self._tmp_dir: Optional[Path] = None
        self._mount_points: Dict[str, Path] = {}

    def __enter__(self) -> Dict[str, Path]:
        self._tmp_dir = Path(mkdtemp(prefix=self._prefix, dir=self._base_dir))
        LOGGER.debug('Created temporary base dir: %s', self._tmp_dir)
        for workspace in self._workspaces:
            mount_point = self._tmp_dir / workspace.name
            mount_point.mkdir(mode=0o700)
            linux.mount(source='tmpfs', target=mount_point,
                        fs_type='tmpfs', options=f'size={workspace.size}')
            LOGGER.debug('Mounted tmpfs with size=%s on %s', workspace.size, mount_point)
            self._mount_points[workspace.name] = mount_point
        return self._mount_points

    def __exit__(self, exc_type, exc_val, exc_tb):
        for name, mount_point in self._mount_points.items():
            linux.umount(mount_point)
            mount_point.rmdir()
            LOGGER.debug('Unmounted and removed %s', mount_point)
        self._tmp_dir.rmdir()
        LOGGER.debug('Removed tmp dir %s', self._tmp_dir)


class Sandbox:
    def __init__(self, exe: Path, workspace_base: Path = Path('/dev/shm')):
        self._exe = exe
        self._workspace_base = workspace_base

    def prepare_workspaces(self, workspaces: List[Workspace]) -> TmpfsWorkspacesContextManager:
        return TmpfsWorkspacesContextManager(workspaces, prefix=f'archoj.', base_dir=self._workspace_base)

    async def run(self, step: Step) -> SandboxResult:
        def bind(bindings):
            for binding in bindings:
                src = Path(binding.src)
                if not src.exists():
                    src.mkdir(parents=True)
                    LOGGER.debug('mkdir -p %s', src)
                if binding.read_only:
                    yield f'--bindmount_ro={src}:{binding.dest}'
                else:
                    yield f'--bindmount={src}:{binding.dest}'

        stdin = Path(step.stdin)
        stdout = Path(step.stdout)
        stderr = Path(step.stderr)
        for directory in {stdin.parent, stdout.parent, stderr.parent}:  # use set to deduplicate
            if not directory.exists():
                directory.mkdir(parents=True)
                LOGGER.debug('mkdir -p %s', directory)

        proc = await asyncio.create_subprocess_exec(
            self._exe,
            '--user=nobody',
            '--group=nobody',
            '--hostname=jail',
            '--report_fd=1',
            '--log_fd=2',
            f'--stdin={step.stdin}',
            f'--stdout={step.stdout}',
            f'--stderr={step.stderr}',
            '--cgroup_mem_parent=ARCHOJ',
            '--cgroup_pids_parent=ARCHOJ',
            '--cgroup_cpu_parent=ARCHOJ',
            '--cgroup_cpuacct_parent=ARCHOJ',
            f'--cgroup_mem_max={step.memory_limit}',
            f'--cgroup_pids_max={step.pids_limit}',
            '--cgroup_cpu_ms_per_sec=1000',  # limit to single core (Hint: use 2000 to allow using dual cores)
            '--max_cpus=1',  # also set cpu affinity
            f'--time_limit={ceil(step.time_limit) + 1}',
            f'--chroot={step.rootfs}',
            f'--cwd={step.cwd}',
            *(f'--env={name}={value}' for name, value in step.envs.items()),
            *bind(step.bindings),
            '--',
            step.exec,
            *step.args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        report, sandbox_log = await proc.communicate()
        try:
            runtime_detail = self.parse_report(report.decode())
            time_limit = timedelta(seconds=step.time_limit)
            if runtime_detail.memory > step.memory_limit:
                verdict = Verdict.MEMORY_LIMIT_EXCEEDED
            elif runtime_detail.cpu_time > time_limit or (
                runtime_detail.cpu_time <= time_limit and runtime_detail.wall_time > time_limit + timedelta(seconds=1)
            ):
                verdict = Verdict.TIME_LIMIT_EXCEEDED
            elif runtime_detail.exit_code != 0 or runtime_detail.exit_signal != 0:
                verdict = Verdict.ERROR
            else:
                verdict = Verdict.ACCEPTED
        except ValueError:
            verdict = Verdict.ERROR
            runtime_detail = None
        return SandboxResult(verdict, runtime_detail, sandbox_log.decode())

    @staticmethod
    def parse_report(report: str) -> JudgeStepDetails:
        collected = {}
        try:
            for line in report.splitlines():
                pid_ignored, key, value = line.split()
                collected[key] = value
            return JudgeStepDetails(
                wall_time=timedelta(milliseconds=float(collected['alive_time_ms'])),
                cpu_time=timedelta(milliseconds=float(collected['cpu_time_ms'])),
                memory=ByteSize(collected['mem_usage_bytes']),
                exit_code=int(collected['exit_code']),
                exit_signal=int(collected['exit_signal'])
            )
        except Exception as e:
            LOGGER.exception('fail to parse: %s', report)
            raise ValueError from e
