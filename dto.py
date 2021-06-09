from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ByteSize, Extra, Field


class DTO(BaseModel):
    class Config:
        allow_mutation = False
        # FIXME:
        #  During development, forbidding extra fields makes it easier to discover programming errors.
        #  However, for forward compatibility, extra fields should be allowed in production so that we can upgrade
        #  message producers, which may introduce new fields, without shutting down running judge daemons.
        extra = Extra.forbid
        json_encoders = {datetime: lambda v: v.timestamp()}
        json_decoders = {datetime: lambda v: datetime.fromtimestamp(v)}


class FileEntry(DTO):
    path: str
    content: str


class JudgeRequest(DTO):
    problemId: int
    submissionId: str
    files: List[FileEntry]
    # TODO: extra data, e.g. self-test input, self-test JUnit


class Verdict(Enum):
    ACCEPTED = 'AC'
    ERROR = 'ERR'
    TIME_LIMIT_EXCEEDED = 'TLE'
    MEMORY_LIMIT_EXCEEDED = 'MLE'
    INTERNAL_ERROR = 'IE'


class JudgeStatus(Enum):
    OK = 'OK'
    BAD_SUBMISSION = 'BAD'
    INTERNAL_ERROR = 'IE'


class JudgeStepDetails(DTO):
    wallTime: timedelta
    cpuTime: timedelta
    memory: ByteSize
    exitCode: int
    exitSignal: int


class JudgeProgress(DTO):
    timestamp: datetime = Field(default_factory=datetime.now)
    submissionId: str
    step: str
    verdict: Verdict
    message: Optional[str]  # shown to students
    log: Optional[str]  # not shown to students
    details: Optional[JudgeStepDetails]  # if None, sth goes wrong in this step


class JudgeResult(DTO):  # Final result
    timestamp: datetime = Field(default_factory=datetime.now)
    submissionId: str
    score: float
    ignored: bool
    status: JudgeStatus
    message: str
