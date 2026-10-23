# -*- coding: utf-8 -*-

import argparse
import asyncio

from . import utils
from .runner import TransferJob, configure_logging, run_transfer
from .settings import TransferSettings


async def async_main():
    parser = argparse.ArgumentParser(
        prog="baidutransfer", description="Baidu yunpan free transfer tool"
    )
    parser.add_argument(
        "url",
        help="baidu pan url, e.g. https://pan.baidu.com/s/1yQ7wutp3m1XtEhsigf_X6A or https://pan.baidu.com/share/init?surl=yQ7wutp3m1XtEhsigf_X6A",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="config yaml file path"
    )
    parser.add_argument("-C", "--cookie", help="cookie of pan.baidu.com")
    parser.add_argument("-p", "--password", help="share password")
    parser.add_argument(
        "-d",
        "--destination",
        default="",
        help="destination directory in your netdisk, e.g. /paper-patterns",
    )
    parser.add_argument(
        "--resume-path",
        help="full destination directory at which an interrupted transfer resumes",
    )
    parser.add_argument("--max-files", type=int, default=500)
    parser.add_argument("--request-interval-min", type=int, default=30)
    parser.add_argument("--request-interval-max", type=int, default=60)
    parser.add_argument("--rest-after-min", type=int, default=10)
    parser.add_argument("--rest-after-max", type=int, default=20)
    parser.add_argument("--rest-duration-min", type=int, default=60)
    parser.add_argument("--rest-duration-max", type=int, default=300)
    parser.add_argument("--max-retries", type=int, default=10)
    parser.add_argument("--http-total-timeout", type=int, default=60)
    parser.add_argument("--http-connect-timeout", type=int, default=15)
    parser.add_argument("--http-read-timeout", type=int, default=45)

    args = parser.parse_args()
    configure_logging()
    settings = TransferSettings(
        max_files_per_request=args.max_files,
        request_interval_min=args.request_interval_min,
        request_interval_max=args.request_interval_max,
        rest_after_min_requests=args.rest_after_min,
        rest_after_max_requests=args.rest_after_max,
        rest_duration_min=args.rest_duration_min,
        rest_duration_max=args.rest_duration_max,
        max_retries=args.max_retries,
        http_total_timeout=args.http_total_timeout,
        http_connect_timeout=args.http_connect_timeout,
        http_read_timeout=args.http_read_timeout,
    )
    job = TransferJob(
        share_url=args.url,
        cookie=args.cookie or "",
        config_path=args.config,
        password=args.password or "",
        destination=args.destination,
        resume_path=args.resume_path or "",
    )
    try:
        await run_transfer(job, settings)
    except (ValueError, RuntimeError) as exc:
        utils.logger.error(str(exc))
        return -1


def main():
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main() or 0)
