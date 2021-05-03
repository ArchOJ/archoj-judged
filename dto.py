from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, ByteSize, Extra, Field


class DTO(BaseModel):
    class Config:
        allow_mutation = False
        extra = Extra.forbid  # FIXME: should allow extra fields for forward compatibility
        json_encoders = {datetime: lambda v: v.timestamp()}
        json_decoders = {datetime: lambda v: datetime.fromtimestamp(v)}


class FileEntry(DTO):
    path: Path
    content: str


class JudgeRequest(DTO):
    problem_id: int
    submission_id: str
    files: List[FileEntry]
    # TODO: extra data, e.g. self-test input, self-test JUnit


class Verdict(Enum):
    ACCEPTED = 'AC'
    ERROR = 'ERR'
    TIME_LIMIT_EXCEEDED = 'TLE'
    MEMORY_LIMIT_EXCEEDED = 'MLE'


class JudgeStatus(Enum):
    OK = 'OK'
    BAD_SUBMISSION = 'BAD'
    SERVER_ERROR = 'SE'


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
    details: Optional[JudgeStepDetails]  # if None, sth goes wrong in this step


class JudgeResult(DTO):  # Final result
    timestamp: datetime = Field(default_factory=datetime.now)
    submission_id: str
    pipeline: str
    score: float
    count: bool
    status: JudgeStatus
    message: str
