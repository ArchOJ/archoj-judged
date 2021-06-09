import logging
from typing import Dict, Mapping, Optional, Iterable

import toml
from pydantic import BaseModel, ByteSize, ValidationError

from dto import Verdict
from pipeline import AbstractPipeline, Workspace, Bind, Step, Summary
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class ProblemConfig(BaseModel):
    memory_limit: Optional[ByteSize] = None
    time_limit: Optional[float] = None  # in seconds


# Attention: JVM may not be aware of CGroup memory and cpu limits
# https://stackoverflow.com/q/59661653


class JavaOpenJDK11DiffPipeline(AbstractPipeline):
    def __init__(self):
        super(JavaOpenJDK11DiffPipeline, self).__init__(
            name='java_openjdk11_diff',
            workspaces=[
                Workspace(name='work', size=64 * 1024**2),
                Workspace(name='judge', size=4 * 1024**2),
            ],
            source_dir='$work/src'
        )

    def define_steps(self, problem_dir: Path, workspaces: Dict[str, Path]) -> Iterable[Step]:
        try:
            with problem_dir.joinpath('config.toml').open() as f:
                problem_conf = ProblemConfig.parse_obj(toml.load(f)[self.name])
        except (FileNotFoundError, KeyError, ValidationError):
            LOGGER.info('Problem configuration error, ignoring problem configuration')
            problem_conf = ProblemConfig()

        yield Step(
            name='compile',
            prerequisites=[],
            rootfs='/usr/local/archoj/share/rootfs/openjdk-11-jdk',
            bindings=[
                Bind(src=f'{workspaces["work"]}/src', dest='/build/src', read_only=True),
                Bind(src=f'{workspaces["work"]}/bin', dest='/build/bin', read_only=False),
            ],
            cwd='/build/src',
            exec='/usr/local/openjdk-11/bin/javac',
            args=['-J-XX:MaxHeapSize=248M', '-J-XX:ActiveProcessorCount=1', '-d', '/build/bin', 'Main.java'],
            stdin='/dev/null',
            stdout='/dev/null',
            stderr=f'{workspaces["judge"]}/compile.err',
            message_file=f'{workspaces["judge"]}/compile.err',
            memory_limit=256 * 1024**2,
            time_limit=5,
            pids_limit=0,  # no pid limit
        )

        cases = sorted([f.stem for f in problem_dir.iterdir() if f.is_file() and f.suffix == '.in'], key=int)

        for case in cases:
            yield Step(
                name=f'run-{case}',
                prerequisites=['compile'],
                rootfs='/usr/local/archoj/share/rootfs/openjdk-11-jre',
                bindings=[Bind(src=f'{workspaces["work"]}/bin', dest='/app', read_only=True)],
                cwd='/app',
                exec='/usr/local/openjdk-11/bin/java',
                args=['-XX:MaxHeapSize=248M', '-XX:ActiveProcessorCount=1', 'Main'],
                stdin=f'{problem_dir}/{case}.in',
                stdout=f'{workspaces["work"]}/output/{case}.out',
                stderr=f'{workspaces["work"]}/output/{case}.err',
                memory_limit=problem_conf.memory_limit or (256 * 1024**2),
                time_limit=problem_conf.time_limit or 1.0,
                pids_limit=12,
            )
            yield Step(
                name=f'diff-{case}',
                prerequisites=[f'run-{case}'],
                rootfs='/usr/local/archoj/share/rootfs/openjdk-11-jre',
                bindings=[
                    Bind(src=f'{workspaces["work"]}/output/{case}.out', dest='/diff/ans', read_only=True),
                    Bind(src=f'{problem_dir}/{case}.out', dest='/diff/key', read_only=True),
                ],
                cwd='/',
                exec='/usr/bin/diff',
                args=['-qZ', '/diff/ans', '/diff/key'],
                stdin='/dev/null',
                stdout=f'{workspaces["judge"]}/diff-{case}.out',
                stderr=f'{workspaces["judge"]}/diff-{case}.err',
                memory_limit=256 * 1024**2,
                time_limit=1.0,
            )

    def summarize(self, verdicts: Mapping[str, Verdict]) -> Summary:
        if any(verdict == Verdict.INTERNAL_ERROR for verdict in verdicts):
            return Summary(score=0.0, ignored=True, message='Internal Error')
        if verdicts['compile'] == Verdict.ERROR:
            return Summary(score=0.0, ignored=True, message='Compile Error')
        run_verdicts = [verdicts for step_name, verdict in verdicts.items() if step_name.startswith('run-')]
        diff_verdicts = [verdict for step_name, verdict in verdicts.items() if step_name.startswith('diff-')]
        score = diff_verdicts.count(Verdict.ACCEPTED) / len(diff_verdicts) * 100.0
        if score == 100.0:
            message = 'Accepted'
        elif score > 0:
            message = 'Partial points'
        elif all(verdict == Verdict.ERROR for verdict in diff_verdicts):
            message = 'Wrong Answer'
        elif all(verdict == Verdict.TIME_LIMIT_EXCEEDED for verdict in run_verdicts):
            message = 'Time limit exceeded'
        elif all(verdict == Verdict.MEMORY_LIMIT_EXCEEDED for verdict in run_verdicts):
            message = 'Memory limit exceeded'
        elif all(verdict == Verdict.ERROR for verdict in run_verdicts):
            message = 'Runtime error'
        else:
            message = 'Error'
        return Summary(score=score, ignored=False, message=message)
