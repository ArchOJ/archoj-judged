from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ByteSize, Extra, Field


class DTO(BaseModel):
    class Config:
        allow_mutation = False
        extra = Extra.forbid  # FIXME: should allow extra fields for forward compatibility


class FileEntry(DTO):
    path: Path
    content: str


class JudgeRequest(DTO):
    problem_id: int
    submission_id: str
    files: List[FileEntry]
    # TODO: extra data, e.g. self-test input, self-test JUnit


class Verdict(Enum):
    ACCEPTED = 'ac'
    ERROR = 'err'
    TIME_LIMIT_EXCEEDED = 'tle'
    MEMORY_LIMIT_EXCEEDED = 'mle'


class JudgeStatus(Enum):
    OK = 'ok'
    BAD_SUBMISSION = 'bad'
    SERVER_ERROR = 'se'


class JudgeStepDetails(DTO):
    wall_time: timedelta
    cpu_time: timedelta
    memory: ByteSize
    exit_code: int
    exit_signal: int


class JudgeProgress(DTO):
    timestamp: datetime = Field(default_factory=datetime.now)
    submission_id: str
    pipeline: str
    step: str
    verdict: Verdict
    message: Optional[str]  # shown to students
    log: Optional[str]  # not shown to students
    details: Optional[JudgeStepDetails]


class JudgeResult(DTO):  # Final result
    timestamp: datetime = Field(default_factory=datetime.now)
    score: float
    count: bool
    status: JudgeStatus = Field(default=JudgeStatus.OK)
    message: str
