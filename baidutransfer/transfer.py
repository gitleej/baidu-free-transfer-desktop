# -*- coding: utf-8 -*-

import logging

import aiohttp

from . import utils


class BaiduFileTransfer(object):
    """Baidu File Transfer"""

    def __init__(
        self,
        api,
        share_key,
        pwd=None,
        root_path=None,
        resume_target_path=None,
        max_files_per_request=500,
    ):
        self._api = api
        self._share_key = share_key
        self._pwd = pwd
        self._root_path = root_path or ""
        self._share_root = ""
        self._user_id = None
        self._share_id = None
        self._dir_list = []
        self._file_list = []
        self._failed_items = []
        self._count_scan_directories = 0
        self._count_scan_files = 0
        self._resume_target_path = (
            resume_target_path.rstrip("/") if resume_target_path else None
        )
        if not 1 <= max_files_per_request <= 500:
            raise ValueError("max_files_per_request must be between 1 and 500")
        self._max_files_per_request = max_files_per_request

    async def init_share_data(self):
        if self._pwd:
            await self._api.update_randsk(self._share_key, self._pwd)
        try:
            share_data = await self._api.get_share_data(self._share_key, self._pwd)
        except utils.BaiduYunPanRedirectError as ex:
            if ex.url.startswith("/share/init"):
                if not self._pwd:
                    raise ValueError("Password not specified")
                else:
                    raise ValueError("Wrong password: %s" % self._pwd)
        self._user_id = share_data["user_id"]
        self._share_id = share_data["share_id"]
        self._api.bdstoken = share_data["bdstoken"]
        self._share_root = share_data["root_path"]
        self._dir_list = share_data["dir_list"]
        self._file_list = share_data["file_list"]

    async def list_share_dir_tree(self, path):
        dir_list, file_list = await self._api.list_share_dir(
            self._user_id, self._share_id, path
        )
        for dir in dir_list:
            dir["dir_list"], dir["file_list"] = await self.list_share_dir_tree(
                path + "/" + dir["name"]
            )
        return dir_list, file_list

    async def transfer_files(self, file_list, target_path):
        if target_path:
            await self._api.create_directory(target_path)
        max_transfer_count = self._max_files_per_request
        offset = 0
        while offset < len(file_list):
            await self._transfer_file_batch(
                file_list[offset : offset + max_transfer_count], target_path
            )
            offset += max_transfer_count
        utils.logger.info(
            "[%s] Transfer %d files under directory %s success"
            % (self.__class__.__name__, len(file_list), target_path)
        )

    async def _count_share_directory_items(self, share_path, limit=None):
        """Count files and directory nodes, stopping above the limit."""
        if limit is None:
            limit = self._max_files_per_request
        dir_list, file_list = await self._api.list_share_dir(
            self._user_id, self._share_id, share_path
        )
        self._count_scan_directories += 1
        self._count_scan_files += len(file_list)
        utils.logger.info(
            "[%s] Counting progress: %d directories, %d files; "
            "current %s (%d subdirectories, %d direct files)"
            % (
                self.__class__.__name__,
                self._count_scan_directories,
                self._count_scan_files,
                share_path,
                len(dir_list),
                len(file_list),
            )
        )
        # Baidu expands directory nodes as well as files when enforcing the
        # transfer limit.  Counting files alone underestimates nested trees.
        item_count = 1 + len(file_list)
        if item_count > limit:
            return item_count
        for directory in dir_list:
            child_share_path = share_path.rstrip("/") + "/" + directory["name"]
            remaining = limit - item_count
            item_count += await self._count_share_directory_items(
                child_share_path, limit=remaining
            )
            if item_count > limit:
                return item_count
        return item_count

    async def _transfer_packed_batch(self, batch, target_path, defer_tail=False):
        """Transfer whole source items and optionally defer a rejected tail."""
        if not batch:
            return []
        try:
            await self._api.transfer(
                self._user_id,
                self._share_id,
                [entry["item"]["id"] for entry in batch],
                target_path,
            )
        except utils.TransferLimitExceededError:
            if len(batch) <= 1:
                entry = batch[0]
                if not entry.get("is_directory") or not entry.get("share_path"):
                    raise

                # The recursive count intentionally stops just above 500, so
                # Baidu can still report that a directory estimated below the
                # limit actually contains more than the account permits.  A
                # single over-limit directory cannot be transferred whole;
                # descend one level and keep each child directory whole.
                child_target_path = (
                    target_path.rstrip("/") + "/" + entry["item"]["name"]
                )
                utils.logger.warning(
                    "[%s] Single directory exceeds the account limit; "
                    "descending depth-first: %s"
                    % (self.__class__.__name__, child_target_path)
                )
                child_dirs, child_files = await self._api.list_share_dir(
                    self._user_id,
                    self._share_id,
                    entry["share_path"],
                )
                await self._sync_contents(
                    entry["share_path"],
                    child_target_path,
                    child_dirs,
                    child_files,
                )
                return []
            midpoint = len(batch) // 2
            action = (
                "deferring the tail for repacking"
                if defer_tail
                else "splitting between items"
            )
            utils.logger.info(
                "[%s] Packed batch of %d source items exceeds the account limit; %s"
                % (self.__class__.__name__, len(batch), action)
            )
            await self._transfer_packed_batch(batch[:midpoint], target_path)
            if defer_tail:
                return batch[midpoint:]
            await self._transfer_packed_batch(batch[midpoint:], target_path)
            return []

        recursive_file_count = sum(entry["file_count"] for entry in batch)
        recursive_item_count = sum(
            entry.get("item_count", entry["file_count"]) for entry in batch
        )
        utils.logger.info(
            "[%s] Transfer packed batch under directory %s success: "
            "%d source items, %d recursive files, %d expanded items"
            % (
                self.__class__.__name__,
                target_path,
                len(batch),
                recursive_file_count,
                recursive_item_count,
            )
        )
        return []

    async def transfer_packed_items(self, entries, target_path):
        """Pack whole files/directories up to about 500 recursive files."""
        if target_path:
            await self._api.create_directory(target_path)

        max_item_count = self._max_files_per_request
        max_source_items = self._max_files_per_request
        batch = []
        batch_item_count = 0

        for entry in entries:
            entry_item_count = entry.get("item_count", entry["file_count"])
            would_overflow = batch and (
                batch_item_count + entry_item_count > max_item_count
                or len(batch) >= max_source_items
            )
            if would_overflow:
                await self._transfer_packed_batch(batch, target_path)
                batch = []
                batch_item_count = 0

            # A directory larger than the expanded-item limit is submitted
            # alone. It is never recursively split merely to fill a batch.
            if entry_item_count > max_item_count:
                await self._transfer_packed_batch([entry], target_path)
                continue

            batch.append(entry)
            batch_item_count += entry_item_count

        if batch:
            await self._transfer_packed_batch(batch, target_path)

    async def _transfer_file_batch(self, file_list, target_path):
        try:
            await self._api.transfer(
                self._user_id,
                self._share_id,
                [item["id"] for item in file_list],
                target_path,
            )
        except utils.TransferLimitExceededError:
            if len(file_list) <= 1:
                raise
            midpoint = len(file_list) // 2
            utils.logger.info(
                "[%s] File batch of %d exceeds the account limit; splitting"
                % (self.__class__.__name__, len(file_list))
            )
            await self._transfer_file_batch(file_list[:midpoint], target_path)
            await self._transfer_file_batch(file_list[midpoint:], target_path)

    async def transfer_dirs(self, dir_list, target_path):
        if target_path:
            await self._api.create_directory(target_path)
        if not dir_list:
            return
        dir_paths = ["%s/%s" % (target_path, it["name"]) for it in dir_list]
        try:
            await self._api.transfer(
                self._user_id,
                self._share_id,
                [dir["id"] for dir in dir_list],
                target_path,
            )
        except utils.TransferLimitExceededError as ex:
            utils.logger.warning(
                "[%s] Directory %s %s"
                % (self.__class__.__name__, ",".join(dir_paths), ex)
            )
            if len(dir_list) >= 2:
                mid = len(dir_list) // 2
                await self.transfer_dirs(dir_list[:mid], target_path)
                await self.transfer_dirs(dir_list[mid:], target_path)
            else:
                # transfer subdirs and files
                dir = dir_list[0]
                dir_path = self._share_root
                if target_path[len(self._root_path) :]:
                    dir_path += target_path[len(self._root_path) :]
                dir_path += "/" + dir["name"]
                dir_list, file_list = await self._api.list_share_dir(
                    self._user_id,
                    self._share_id,
                    dir_path,
                )
                if dir_list:
                    await self.transfer_dirs(dir_list, target_path + "/" + dir["name"])
                if file_list:
                    await self.transfer_files(
                        file_list, target_path + "/" + dir["name"]
                    )
        else:
            for dir_path in dir_paths:
                utils.logger.info(
                    "[%s] Transfer directory %s success"
                    % (self.__class__.__name__, dir_path)
                )

    async def transfer(self):
        await self._sync_contents(
            self._share_root,
            self._root_path,
            self._dir_list,
            self._file_list,
        )

    async def _sync_contents(self, share_path, target_path, dir_list, file_list):
        if target_path:
            await self._api.create_directory(target_path)

        # An explicit resume cursor bypasses every source sibling before the
        # previously failed directory.  At each ancestor, descend directly
        # into the cursor child, then continue with siblings that follow it.
        if self._resume_target_path:
            current_target = target_path.rstrip("/") or "/"
            if current_target == self._resume_target_path:
                utils.logger.info(
                    "[%s] Reached explicit resume position: %s"
                    % (self.__class__.__name__, current_target)
                )
                self._resume_target_path = None
            else:
                child_prefix = current_target.rstrip("/") + "/"
                if not self._resume_target_path.startswith(child_prefix):
                    raise ValueError(
                        "Resume path %s is not under destination %s"
                        % (self._resume_target_path, current_target)
                    )
                next_name = self._resume_target_path[len(child_prefix) :].split(
                    "/", 1
                )[0]
                resume_index = next(
                    (
                        index
                        for index, directory in enumerate(dir_list)
                        if directory["name"] == next_name
                    ),
                    None,
                )
                if resume_index is None:
                    raise RuntimeError(
                        "Resume directory %s was not found under %s"
                        % (next_name, share_path)
                    )
                resume_directory = dir_list[resume_index]
                child_share_path = share_path.rstrip("/") + "/" + next_name
                child_target_path = child_prefix + next_name
                utils.logger.info(
                    "[%s] Bypassing completed siblings; descending to resume path: %s"
                    % (self.__class__.__name__, child_target_path)
                )
                child_dirs, child_files = await self._api.list_share_dir(
                    self._user_id,
                    self._share_id,
                    child_share_path,
                )
                await self._sync_contents(
                    child_share_path,
                    child_target_path,
                    child_dirs,
                    child_files,
                )
                dir_list = dir_list[resume_index + 1 :]
                file_list = []

        existing_items = await self._api.list_dir_all(target_path or "/")
        existing_by_name = {
            item["server_filename"]: item for item in existing_items
        }
        existing_names = set(existing_by_name)

        missing_files = [item for item in file_list if item["name"] not in existing_names]
        missing_dirs = [item for item in dir_list if item["name"] not in existing_names]

        # Resume existing, potentially incomplete directories before starting
        # any later missing siblings.  Directly transferred directories are
        # recognized as complete and skipped without a source-tree scan.
        for directory in dir_list:
            if directory in missing_dirs:
                continue

            child_share_path = share_path.rstrip("/") + "/" + directory["name"]
            child_target_path = target_path.rstrip("/") + "/" + directory["name"]
            existing_directory = existing_by_name.get(directory["name"])
            if existing_directory and existing_directory.get("isdir"):
                source_mtime = existing_directory.get("local_mtime", 0)
                transfer_mtime = existing_directory.get("server_mtime", 0)
                if transfer_mtime - source_mtime > 86400:
                    utils.logger.info(
                        "[%s] Skip verified complete directory %s"
                        % (self.__class__.__name__, child_target_path)
                    )
                    continue

            utils.logger.info(
                "[%s] Resuming existing directory: %s"
                % (self.__class__.__name__, child_target_path)
            )
            child_dirs, child_files = await self._api.list_share_dir(
                self._user_id,
                self._share_id,
                child_share_path,
            )
            await self._sync_contents(
                child_share_path,
                child_target_path,
                child_dirs,
                child_files,
            )

        packed_entries = [
            {
                "item": item,
                "file_count": 1,
                "item_count": 1,
                "is_directory": False,
            }
            for item in missing_files
        ]
        packed_item_count = len(packed_entries)

        # Flush full groups of loose files immediately.  Directory counting is
        # then streamed one directory at a time so a large tree does not need a
        # complete pre-scan before the first transfer can start.
        while len(packed_entries) >= self._max_files_per_request:
            await self._transfer_packed_batch(
                packed_entries[: self._max_files_per_request], target_path
            )
            packed_entries = packed_entries[self._max_files_per_request :]
            packed_item_count = len(packed_entries)

        for directory in missing_dirs:
            child_share_path = share_path.rstrip("/") + "/" + directory["name"]
            self._count_scan_directories = 0
            self._count_scan_files = 0
            utils.logger.info(
                "[%s] Counting directory recursively before packing: %s"
                % (
                    self.__class__.__name__,
                    target_path.rstrip("/") + "/" + directory["name"],
                )
            )
            recursive_item_count = await self._count_share_directory_items(
                child_share_path, limit=self._max_files_per_request
            )
            recursive_file_count = self._count_scan_files
            entry = {
                "item": directory,
                "file_count": recursive_file_count,
                "item_count": recursive_item_count,
                "is_directory": True,
                "share_path": child_share_path,
            }
            utils.logger.info(
                "[%s] Counted directory %s: %d recursive files, "
                "%d expanded items"
                % (
                    self.__class__.__name__,
                    target_path.rstrip("/") + "/" + directory["name"],
                    recursive_file_count,
                    recursive_item_count,
                )
            )

            if recursive_item_count > self._max_files_per_request:
                if packed_entries:
                    await self._transfer_packed_batch(packed_entries, target_path)
                    packed_entries = []
                    packed_item_count = 0
                child_target_path = target_path.rstrip("/") + "/" + directory["name"]
                utils.logger.info(
                    "[%s] Directory exceeds %d expanded items; "
                    "descending depth-first: %s"
                    % (
                        self.__class__.__name__,
                        self._max_files_per_request,
                        child_target_path,
                    )
                )
                child_dirs, child_files = await self._api.list_share_dir(
                    self._user_id,
                    self._share_id,
                    child_share_path,
                )
                await self._sync_contents(
                    child_share_path,
                    child_target_path,
                    child_dirs,
                    child_files,
                )
                continue

            if packed_entries and (
                packed_item_count + recursive_item_count
                > self._max_files_per_request
                or len(packed_entries) >= self._max_files_per_request
            ):
                # If Baidu still rejects a conservatively counted batch, keep
                # the untransferred tail as the seed of the next batch so it
                # can be filled with this and later whole directories.
                packed_entries = await self._transfer_packed_batch(
                    packed_entries, target_path, defer_tail=True
                )
                packed_item_count = sum(
                    pending.get("item_count", pending["file_count"])
                    for pending in packed_entries
                )

                while packed_entries and (
                    packed_item_count + recursive_item_count
                    > self._max_files_per_request
                    or len(packed_entries) >= self._max_files_per_request
                ):
                    pending_before = len(packed_entries)
                    packed_entries = await self._transfer_packed_batch(
                        packed_entries, target_path, defer_tail=True
                    )
                    packed_item_count = sum(
                        pending.get("item_count", pending["file_count"])
                        for pending in packed_entries
                    )
                    if len(packed_entries) == pending_before:
                        raise RuntimeError("Unable to reduce an over-limit packed batch")

            packed_entries.append(entry)
            packed_item_count += recursive_item_count

        if packed_entries:
            await self._transfer_packed_batch(packed_entries, target_path)

        utils.logger.info(
            "[%s] Sync directory %s: %d subdirectories, %d missing files"
            % (
                self.__class__.__name__,
                target_path or "/",
                len(dir_list),
                len(missing_files),
            )
        )

# End of module.

