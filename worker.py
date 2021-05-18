import asyncio
from functools import partial
import logging
from pathlib import Path
from string import Template
from typing import Iterable, List, Optional, Union

import aio_pika

from config import Config
from dto import JudgeRequest, FileEntry, JudgeProgress, JudgeResult, JudgeStatus, Verdict
from pipeline import AbstractPipeline
from sandbox import Sandbox


EXCHANGE_DEAD_LETTER = 'archoj.exchange.dead'
ROUTING_PROGRESS = 'judge.progress'
ROUTING_RESULT = 'judge.result'

LOGGER = logging.getLogger(__name__)


def write_files(base_dir: Union[Path, str], files: Iterable[FileEntry]):
    base_dir = Path(base_dir).resolve()
    existing_dirs = set()
    for file in files:
        if not file.path:  # if empty
            raise ValueError(f'Empty path')
        dest_path = base_dir.joinpath(file.path).resolve()
        if not dest_path.is_relative_to(base_dir):
            raise ValueError(f'Bad path: {file.path}')
        parent_dir = dest_path.parent
        try:
            if parent_dir not in existing_dirs:
                parent_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                existing_dirs.add(parent_dir)
                LOGGER.debug('mkdir -p %s', parent_dir)
            dest_path.write_text(file.content)
        except OSError as e:  # e.g., file path too long, illegal characters in path
            raise ValueError(f'cannot create file: {file.path}') from e
        LOGGER.debug('Created file: %s', dest_path)


class Worker:
    def __init__(self, conf: Config, pipelines: List[AbstractPipeline]):
        self._conf: Config = conf
        self._sandbox: Sandbox = Sandbox(exe=conf.sandbox, workspace_base=conf.workspace_base)
        self._pipelines: List[AbstractPipeline] = pipelines
        self._send_connection: Optional[aio_pika.Connection] = None
        self._recv_connection: Optional[aio_pika.Connection] = None
        self._send_channel: Optional[aio_pika.Channel] = None
        self._recv_channel: Optional[aio_pika.Channel] = None
        self._judge_exchange: Optional[aio_pika.Exchange] = None
        self._dead_exchange: Optional[aio_pika.Exchange] = None

    async def _connect(self):
        mq_conf = self._conf.rabbitmq
        return await aio_pika.connect_robust(
            host=mq_conf.host,
            port=mq_conf.port,
            login=mq_conf.login,
            password=mq_conf.password.get_secret_value(),
            virtualhost=mq_conf.virtual_host
        )

    async def stop(self):
        if self._recv_connection is not None:
            await self._recv_connection.close()
        if self._send_connection is not None:
            await self._send_connection.close()

    async def work(self):
        # Use separate connection for sender and receiver so that TCP back-pressure
        # on the sender side doesn't affects the receiver
        self._recv_connection, self._send_connection = await asyncio.gather(
            self._connect(),
            self._connect()
        )
        self._recv_channel, self._send_channel = await asyncio.gather(
            self._recv_connection.channel(),
            self._send_connection.channel()
        )

        # Declare exchanges
        self._judge_exchange = await self._recv_channel.declare_exchange(
            name='archoj.exchange.judge',
            type=aio_pika.ExchangeType.DIRECT,
            durable=True
        )
        self._dead_exchange: aio_pika.Exchange = await self._recv_channel.declare_exchange(
            name=EXCHANGE_DEAD_LETTER,
            type=aio_pika.ExchangeType.DIRECT,
            durable=True
        )

        # Queues
        progress_queue: aio_pika.Queue = await self._send_channel.declare_queue(
            name='archoj.queue.judge.progress',
            durable=True
        )
        result_queue: aio_pika.Queue = await self._send_channel.declare_queue(
            name='archoj.queue.judge.result',
            durable=True,
            arguments={'x-dead-letter-exchange': EXCHANGE_DEAD_LETTER,
                       'x-dead-letter-routing-key': ROUTING_RESULT}
        )
        dead_result_queue: aio_pika.Queue = await self._send_channel.declare_queue(
            name='archoj.queue.judge.dead-result',
            durable=True
        )

        # Bindings
        await progress_queue.bind(self._judge_exchange, ROUTING_PROGRESS)
        await result_queue.bind(self._judge_exchange, ROUTING_RESULT)
        await dead_result_queue.bind(self._dead_exchange, ROUTING_RESULT)

        await self._recv_channel.set_qos(self._conf.max_concurrency)

        workers = (self._start_pipeline(pipeline) for pipeline in self._pipelines)
        await asyncio.gather(*workers)

    async def _start_pipeline(self, pipeline: AbstractPipeline):
        LOGGER.info('Starting pipeline: %s', pipeline.name)
        request_routing_key = f'judge.request.{pipeline.name}'

        # Queues
        request_queue: aio_pika.Queue = await self._recv_channel.declare_queue(
            name=f'archoj.queue.judge.request.{pipeline.name}.{self._conf.name}',
            durable=True,
            arguments={'x-dead-letter-exchange': EXCHANGE_DEAD_LETTER,
                       'x-dead-letter-routing-key': request_routing_key}
        )
        dead_request_queue: aio_pika.Queue = await self._recv_channel.declare_queue(
            name=f'archoj.queue.judge.dead-request.{pipeline.name}',
            durable=True
        )

        # Bindings
        await request_queue.bind(self._judge_exchange, request_routing_key)
        await dead_request_queue.bind(self._dead_exchange, request_routing_key)

        # Consuming
        # TODO: timeout
        await request_queue.consume(partial(self._on_judge_request, pipeline=pipeline), no_ack=False)
        LOGGER.info('Started pipeline: %s', pipeline.name)

    async def _on_judge_request(self, request: aio_pika.IncomingMessage, *, pipeline: AbstractPipeline):
        # TODO: acquire semaphore for multi-core tasks. Currently each task are only allowed to use one core
        async with request.process(requeue=True, reject_on_redelivered=True, ignore_processed=True):
            judge_request = JudgeRequest.parse_raw(request.body)
            try:
                with self._sandbox.prepare_workspaces(pipeline.workspaces) as workspaces_lookup:
                    source_dir = Template(pipeline.source_dir).substitute(workspaces_lookup)
                    try:
                        write_files(source_dir, judge_request.files)
                    except ValueError as e:
                        LOGGER.warning('Bad submission: %s', judge_request)
                        await self._send_result(JudgeResult(
                            submissionId=judge_request.submissionId, score=0, ignored=False,
                            status=JudgeStatus.BAD_SUBMISSION, message=str(e)))
                        request.reject(requeue=False)
                        return
                    successful_steps = set()
                    verdicts = {}
                    for step in pipeline.define_steps(self._conf.problems_base / str(judge_request.problemId),
                                                      workspaces_lookup):
                        if not set(step.prerequisites).issubset(successful_steps):  # Check prerequisites
                            LOGGER.info('Step skipped: %s, unsatisfied prerequisites: %r',
                                        step.name, step.prerequisites)
                            verdicts[step.name] = None
                            continue  # check next step
                        sandbox_result = await self._sandbox.run(step)  # TODO
                        verdicts[step.name] = sandbox_result.verdict
                        if sandbox_result.verdict == Verdict.ACCEPTED:
                            successful_steps.add(step.name)
                        message = None
                        if step.message_file is not None:
                            try:
                                with open(step.message_file) as f:
                                    message = f.read()
                            except OSError:
                                pass
                        LOGGER.info('Step result: step=%s, verdict=%s', step.name, sandbox_result.verdict)
                        LOGGER.debug('Sandbox log: %s', sandbox_result.log)
                        await self._send_progress(JudgeProgress(
                            submissionId=judge_request.submissionId,
                            step=step.name,
                            verdict=sandbox_result.verdict,
                            message=message,
                            log=sandbox_result.log,
                            details=sandbox_result.details
                        ))
                    summary = pipeline.summarize(verdicts)
                    await self._send_result(JudgeResult(
                        submissionId=judge_request.submissionId, score=summary.score, ignored=summary.ignored,
                        status=JudgeStatus.OK, message=summary.message))
                LOGGER.debug('Submission done: %s', judge_request.submissionId)
            except Exception as e:
                LOGGER.exception('Unknown error')
                if request.redelivered:
                    await self._send_result(JudgeResult(
                        submissionId=judge_request.submissionId, score=0, ignored=True,
                        status=JudgeStatus.SERVER_ERROR, message='Server-side error'))
                raise e

    async def _send_progress(self, progress: JudgeProgress):
        msg = aio_pika.Message(progress.json().encode(), content_type='application/json', content_encoding='utf-8')
        await self._judge_exchange.publish(msg, routing_key=ROUTING_PROGRESS)

    async def _send_result(self, result: JudgeResult):
        msg = aio_pika.Message(result.json().encode(), content_type='application/json', content_encoding='utf-8')
        await self._judge_exchange.publish(msg, routing_key=ROUTING_RESULT)
