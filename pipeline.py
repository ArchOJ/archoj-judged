from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from dto import JudgeResult, Verdict


@dataclass(frozen=True)
class Workspace:
    name: str
    size: int  # in bytes


@dataclass(frozen=True)
class Bind:
    src: str
    dest: str
    read_only: bool = field(default=True)


@dataclass(frozen=True)
class Step:
    name: str
    rootfs: str
    cwd: str
    exec: str
    args: List[str]
    stdin: str = field(default='/dev/null')
    stdout: str = field(default='/dev/null')
    stderr: str = field(default='/dev/null')
    message_file: Optional[str] = field(default=None)
    prerequisites: List[str] = field(default_factory=list)
    bindings: List[Bind] = field(default_factory=list)
    memory_limit: int = 268435456  # in bytes
    time_limit: float = 3.0  # in seconds
    pids_limit: int = 1
    envs: Dict[str, str] = field(default_factory=dict)


class AbstractPipeline(ABC):
    def __init__(self, *, name: str, workspaces: List[Workspace], source_dir: str):
        self.name = name
        self.workspaces: List[Workspace] = workspaces
        self.source_dir: str = source_dir

    @abstractmethod
    def define_steps(self, problem_dir: Path, workspaces: Dict[str, Path]) -> Iterable[Step]:
        raise NotImplementedError

    @abstractmethod
    def summarize(self, verdicts: Mapping[str, Verdict]) -> JudgeResult:
        """Note: if summarization takes a long time, use a new step for computation instead"""
        raise NotImplementedError
