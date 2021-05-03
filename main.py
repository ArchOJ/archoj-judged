#!/usr/bin/env python3

import argparse
import asyncio
import logging
from pathlib import Path
import signal
import sys

import toml
import coloredlogs

from config import Config
import linux
from pipelines_conf import pipelines
from worker import Worker


LOGGER = logging.getLogger(__name__)


def main():
    log_format = '%(asctime)s %(levelname)s %(name)s [%(funcName)s#%(lineno)d]: %(message)s'
    # logging.basicConfig(level=logging.DEBUG, format=log_format)
    coloredlogs.install(level=logging.DEBUG, fmt=log_format)

    arg_parser = argparse.ArgumentParser(description="Arch OJ Judge Daemon")
    arg_parser.add_argument('-c', '--config', dest='config_path', type=Path, help='path of configuration file', required=True)
    args = arg_parser.parse_args()

    try:
        with args.config_path.open() as f:
            conf = Config.parse_obj(toml.load(f))
        LOGGER.debug('Configuration: %r', conf)
    except FileNotFoundError:
        LOGGER.error(f'File {args.config_path} does not exist')
        sys.exit(1)
    except ValueError:
        LOGGER.exception('Configuration file format error')
        sys.exit(1)

    # Transform SIGTERM to SIGINT
    signal.signal(signal.SIGTERM, lambda sig_no, stack_frame: signal.raise_signal(signal.SIGINT))

    linux.want_to_mount_like_root()

    worker = Worker(conf, [cls() for cls in pipelines])
    event_loop = asyncio.get_event_loop()
    event_loop.run_until_complete(worker.work())
    while True:
        LOGGER.info('Starting')
        try:
            event_loop.run_forever()
        except KeyboardInterrupt:
            break
        finally:
            event_loop.run_until_complete(worker.stop())
        LOGGER.warning('Restarting')
    LOGGER.info('Quit')


if __name__ == '__main__':
    main()
