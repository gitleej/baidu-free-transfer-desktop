# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import random
import time
import urllib.parse

import aiohttp

from . import utils
from .settings import TransferSettings


class BaiduYunPanAPI(object):
    """Baidu YunPan API"""

    root_url = "https://pan.baidu.com"

    def __init__(self, cookie, settings=None):
        self.settings = (settings or TransferSettings()).validate()
        self._cookie = self._process_cookie(cookie)
        self._bdstoken = None
        self._randsk = None
        self._share_url = None
        self._last_transfer_request_at = None
        self._transfer_requests_since_rest = 0
        self._transfer_requests_before_rest = random.randint(
            self.settings.rest_after_min_requests,
            self.settings.rest_after_max_requests,
        )

    async def _wait_for_transfer_slot(self):
        if self._transfer_requests_since_rest >= self._transfer_requests_before_rest:
            rest_seconds = random.randint(
                self.settings.rest_duration_min,
                self.settings.rest_duration_max,
            )
            utils.logger.info(
                "[%s] Completed %d transfer requests; resting %d seconds"
                % (
                    self.__class__.__name__,
                    self._transfer_requests_since_rest,
                    rest_seconds,
                )
            )
            await asyncio.sleep(rest_seconds)
            self._transfer_requests_since_rest = 0
            self._transfer_requests_before_rest = random.randint(
                self.settings.rest_after_min_requests,
                self.settings.rest_after_max_requests,
            )
            self._last_transfer_request_at = None

        if self._last_transfer_request_at is not None:
            target_interval = random.randint(
                self.settings.request_interval_min,
                self.settings.request_interval_max,
            )
            elapsed = time.monotonic() - self._last_transfer_request_at
            wait_seconds = max(0, target_interval - elapsed)
            if wait_seconds:
                utils.logger.info(
                    "[%s] Waiting %.1f seconds before the next transfer request"
                    % (self.__class__.__name__, wait_seconds)
                )
                await asyncio.sleep(wait_seconds)

        self._last_transfer_request_at = time.monotonic()
        self._transfer_requests_since_rest += 1

    def _process_cookie(self, cookie):
        pos = cookie.find("BDCLND=")
        if pos < 0:
            return cookie
        pos2 = cookie.find(";", pos)
        if pos2 < 0:
            pos2 = len(cookie)
        return cookie[:pos] + cookie[pos2:]

    async def request(
        self,
        path,
        method="get",
        headers=None,
        params=None,
        data=None,
        check_errno=True,
        referer=None,
        retry_count=0,
    ):
        url = self.root_url + path
        try:
            status_code, headers, body = await utils.http_request(
                url,
                method=method,
                referer=referer or self.root_url,
                cookie=self._cookie,
                headers=headers,
                params=params,
                data=data,
                raise_for_status=True,
                timeout_total=self.settings.http_total_timeout,
                timeout_connect=self.settings.http_connect_timeout,
                timeout_read=self.settings.http_read_timeout,
            )
        except (asyncio.TimeoutError, aiohttp.ClientConnectionError) as exc:
            # Retrying GET is safe.  Do not blindly retry POST here because a
            # transfer may have completed even if its response was lost.
            if method.lower() != "get" or retry_count >= self.settings.max_retries:
                raise
            delay = min(2 ** retry_count, 30)
            utils.logger.warning(
                "[%s] Read request %s failed (%s); retrying in %ss"
                % (self.__class__.__name__, path, type(exc).__name__, delay)
            )
            await asyncio.sleep(delay)
            return await self.request(
                path,
                method=method,
                headers=headers,
                params=params,
                data=data,
                check_errno=check_errno,
                referer=referer,
                retry_count=retry_count + 1,
            )
        if status_code > 300 and status_code < 400:
            raise utils.BaiduYunPanRedirectError(headers["Location"])
        if isinstance(body, str) and "百度网盘-链接不存在" in body:
            raise utils.BaiduYunPanResourceNotFoundError()
        if check_errno and body["errno"]:
            raise utils.BaiduYunPanAPIError(body["errno"], body.get("show_msg"))
        return body

    @property
    def bdstoken(self):
        if not self._bdstoken:
            raise ValueError("bdstoken is None")
        return self._bdstoken

    @bdstoken.setter
    def bdstoken(self, value):
        self._bdstoken = value

    async def get_bdstoken(self):
        path = "/api/gettemplatevariable"
        params = {
            "fields": '["bdstoken"]',
        }
        rsp = await self.request(path, params=params)
        return rsp["result"]["bdstoken"]

    async def create_directory(self, dirpath):
        try:
            await self.list_dir(dirpath)
        except utils.BaiduYunPanAPIError as ex:
            if ex.code != -9:
                raise ex
        else:
            # directory already exist
            return

        path = "/api/create"
        params = {
            "a": "commit",
            "bdstoken": self.bdstoken,
        }
        data = {
            "path": dirpath,
            "isdir": "1",
            "block_list": "[]",
        }
        await self.request(path, "post", params=params, data=data)

    async def get_randsk(self, share_key, pwd):
        path = "/share/verify"
        params = {
            "surl": share_key,
            "bdstoken": self.bdstoken,
        }
        data = {
            "pwd": pwd,
        }
        rsp = await self.request(path, "post", params=params, data=data)
        return rsp["randsk"]

    def _process_dir_file_list(self, dir_file_list):
        dir_list = []
        file_list = []
        for it in dir_file_list:
            if it["isdir"]:
                dir_list.append(
                    {
                        "id": it["fs_id"],
                        "name": it["server_filename"],
                    }
                )
            else:
                file_list.append(
                    {
                        "id": it["fs_id"],
                        "name": it["server_filename"],
                    }
                )
        return dir_list, file_list

    async def get_share_data(self, share_key, pwd=None):
        path = "/s/1%s" % share_key
        body = await self.request(path, check_errno=False)
        pos = body.find("locals.mset(")
        if pos <= 0:
            raise RuntimeError("Invalid body: %s" % body)
        pos2 = body.find("});", pos)
        data = json.loads(body[pos + 12 : pos2 + 1])
        root_path = urllib.parse.unquote(data["file_list"][0]["parent_path"])
        dir_list, file_list = self._process_dir_file_list(data["file_list"])
        return {
            "user_id": data["share_uk"],
            "share_id": data["shareid"],
            "bdstoken": data["bdstoken"],
            "root_path": root_path,
            "dir_list": dir_list,
            "file_list": file_list,
        }

    async def list_share_dir(self, user_id, share_id, dir_path):
        path = "/share/list"
        page = 1
        limit = 100
        dir_file_list = []
        while True:
            params = {
                "uk": user_id,
                "shareid": share_id,
                "page": page,
                "num": limit,
                "dir": dir_path,
            }
            rsp = await self.request(path, params=params)
            dir_file_list.extend(rsp["list"])
            if len(rsp["list"]) < 100:
                break
            page += 1
        return self._process_dir_file_list(dir_file_list)

    async def list_dir(self, dir_path):
        path = "/api/list"
        params = {
            "order": "time",
            "desc": "1",
            "showempty": "0",
            "page": "1",
            "num": "1000",
            "dir": dir_path,
            "bdstoken": self.bdstoken,
        }
        rsp = await self.request(path, params=params)
        return rsp["list"]

    async def list_dir_all(self, dir_path):
        path = "/api/list"
        page = 1
        limit = 1000
        items = []
        while True:
            params = {
                "order": "name",
                "desc": "0",
                "showempty": "0",
                "page": str(page),
                "num": str(limit),
                "dir": dir_path,
                "bdstoken": self.bdstoken,
            }
            rsp = await self.request(path, params=params)
            page_items = rsp["list"]
            items.extend(page_items)
            if len(page_items) < limit:
                return items
            page += 1

    async def transfer(
        self, user_id, share_id, fsid_list, transfer_path, retry_count=0
    ):
        if len(fsid_list) > self.settings.max_files_per_request:
            raise ValueError(
                "A transfer request cannot contain more than %d items"
                % self.settings.max_files_per_request
            )
        await self._wait_for_transfer_slot()
        path = "/share/transfer"
        params = {
            "shareid": share_id,
            "from": user_id,
            "sekey": self._randsk or "",
            "ondup": "newcopy",
            "channel": "chunlei",
            "web": "1",
            "app_id": "250528",
            "clienttype": "0",
            "bdstoken": "",
        }
        data = {
            "fsidlist": "[%s]" % ",".join([str(it) for it in fsid_list]),
            "path": transfer_path or "/",
            "async": "2",
            "type": "0",
            "channel": "chunlei",
            "web": "1",
            "app_id": "250528",
            "clienttype": "0",
        }
        try:
            rsp = await self.request(
                path,
                "post",
                params=params,
                data=data,
                check_errno=False,
                referer=self._share_url or self.root_url,
            )
        except aiohttp.ClientResponseError as exc:
            if exc.status not in (404, 408, 429) and exc.status < 500:
                raise
            max_http_retries = 0 if exc.status == 404 else self.settings.max_retries
            if retry_count >= max_http_retries:
                raise
            delay = min(max(2 ** retry_count, 2), 30)
            utils.logger.warning(
                "[%s] Temporary HTTP %s for %s; retrying in %ss"
                % (self.__class__.__name__, exc.status, transfer_path, delay)
            )
            await asyncio.sleep(delay)
            return await self.transfer(
                user_id,
                share_id,
                fsid_list,
                transfer_path,
                retry_count=retry_count + 1,
            )
        if rsp["errno"] == 2:
            raise utils.BaiduYunPanAPIParameterError(path, data)
        elif rsp["errno"] == 12:
            raise utils.TransferLimitExceededError(
                rsp.get("target_file_nums_limit"),
                rsp.get("target_file_nums"),
            )
        elif rsp["errno"] in (4, 111, 1504):
            if retry_count >= self.settings.max_retries:
                raise utils.BaiduYunPanAPIError(
                    rsp["errno"], rsp.get("show_msg", "temporary transfer failure")
                )
            delay = min(2 ** retry_count, 30)
            if rsp["errno"] == 111:
                delay = max(delay, 5)
            utils.logger.warning(
                "[%s] Temporary transfer error %s for %s; retrying in %ss"
                % (self.__class__.__name__, rsp["errno"], transfer_path, delay)
            )
            await asyncio.sleep(delay)
            return await self.transfer(
                user_id,
                share_id,
                fsid_list,
                transfer_path,
                retry_count=retry_count + 1,
            )
        elif rsp["errno"]:
            raise utils.BaiduYunPanAPIError(rsp["errno"], rsp.get("show_msg"))
        await asyncio.sleep(0.2)

    async def update_randsk(self, share_key, pwd):
        if not self._bdstoken:
            self._bdstoken = await self.get_bdstoken()
        randsk = await self.get_randsk(share_key, pwd)
        self._randsk = urllib.parse.unquote(randsk)
        self._share_url = "%s/s/1%s" % (self.root_url, share_key)
        self._cookie += ";BDCLND=%s" % randsk
