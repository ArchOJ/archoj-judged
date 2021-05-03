from pathlib import Path

from pydantic import BaseModel, Extra, SecretStr


class BaseConf(BaseModel):
    class Config:
        allow_mutation = False
        extra = Extra.forbid


class RabbitMqConf(BaseConf):
    login: str = 'guest'
    password: SecretStr = 'guest'
    host: str = 'localhost'
    port: int = 5672
    virtual_host: str = '/'


class Config(BaseConf):
    name: str
    max_concurrency: int
    problems_base: Path
    workspace_base: Path = Path('/dev/shm')
    sandbox: Path
    rabbitmq: RabbitMqConf
