# -*- coding: utf-8 -*-

import logging
import os
import urllib.parse
from dataclasses import dataclass

import yaml

from . import apis, transfer, utils
from .settings import TransferSettings


@dataclass(frozen=True)
class TransferJob:
    share_url: str
    cookie: str = ""
    config_path: str = "config.yaml"
    password: str = ""
    destination: str = ""
    resume_path: str = ""


def parse_share_url(value):
    url = urllib.parse.urlparse(value.strip())
    share_key = ""
    password = ""
    if url.hostname == "pan.baidu.com":
        if url.path.startswith("/s/1"):
            share_key = url.path.rsplit("/", 1)[-1][1:]
        elif url.path == "/share/init":
            share_key = urllib.parse.parse_qs(url.query).get("surl", [""])[0]
        password = urllib.parse.parse_qs(url.query).get("pwd", [""])[0]
    if not share_key:
        raise ValueError("请输入有效的百度网盘分享链接")
    return share_key, password


def normalize_netdisk_path(value):
    path = (value or "").strip()
    if path and not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/")


def load_cookie(cookie, config_path):
    if cookie and cookie.strip():
        return cookie.strip()
    if config_path and os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as fp:
            config = yaml.safe_load(fp.read()) or {}
        value = config.get("cookie", "")
        if value:
            return str(value).strip()
    raise ValueError("请粘贴 Cookie，或选择包含 cookie 字段的配置文件")


def configure_logging(handler=None):
    utils.logger.setLevel(logging.INFO)
    utils.logger.propagate = False
    if handler is None:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s][%(levelname)s]%(message)s")
    )
    if handler not in utils.logger.handlers:
        utils.logger.addHandler(handler)
    return handler


async def run_transfer(job, settings=None):
    settings = (settings or TransferSettings()).validate()
    share_key, link_password = parse_share_url(job.share_url)
    password = (job.password or link_password).strip()
    cookie = load_cookie(job.cookie, job.config_path)
    destination = normalize_netdisk_path(job.destination)
    resume_path = normalize_netdisk_path(job.resume_path)
    if resume_path and destination and not (
        resume_path == destination or resume_path.startswith(destination + "/")
    ):
        raise ValueError("断点路径必须位于目标目录之下")

    api = apis.BaiduYunPanAPI(cookie, settings=settings)
    task = transfer.BaiduFileTransfer(
        api,
        share_key,
        password or None,
        root_path=destination,
        resume_target_path=resume_path or None,
        max_files_per_request=settings.max_files_per_request,
    )
    try:
        await task.init_share_data()
    except utils.BaiduYunPanResourceNotFoundError:
        raise RuntimeError("分享链接已失效或当前 Cookie 无权访问")
    await task.transfer()

