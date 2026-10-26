import unittest

from baidutransfer.transfer import BaiduFileTransfer


class PackedTransferTests(unittest.IsolatedAsyncioTestCase):
    async def test_recursive_count_includes_directory_nodes_and_stops_after_limit(self):
        class FakeAPI:
            def __init__(self):
                self.calls = []

            async def list_share_dir(self, user_id, share_id, path):
                self.calls.append(path)
                if path == "/root":
                    return ([{"id": 1, "name": "first"}, {"id": 2, "name": "second"}], [])
                if path == "/root/first":
                    return ([], [{"id": index, "name": str(index)} for index in range(501)])
                raise AssertionError("count should stop before scanning the second directory")

        api = FakeAPI()
        transfer = BaiduFileTransfer(api, "share")

        count = await transfer._count_share_directory_items("/root", limit=500)

        self.assertEqual(count, 503)
        self.assertEqual(api.calls, ["/root", "/root/first"])

    async def test_overflowing_directory_seeds_and_fills_the_next_batch(self):
        transfer = BaiduFileTransfer(None, "share", root_path="/target")
        batches = []

        async def record(batch, target_path, defer_tail=False):
            batches.append([entry["item_count"] for entry in batch])
            return []

        transfer._transfer_packed_batch = record
        entries = [
            {
                "item": {"id": index},
                "file_count": count,
                "item_count": count,
                "is_directory": True,
            }
            for index, count in enumerate([380, 180, 210, 95])
        ]

        await transfer.transfer_packed_items(entries, "")

        self.assertEqual(batches, [[380], [180, 210, 95]])

    async def test_api_rejected_batch_defers_tail_for_later_repacking(self):
        from baidutransfer import utils

        class FakeAPI:
            def __init__(self):
                self.calls = []

            async def transfer(self, user_id, share_id, ids, target_path):
                self.calls.append(ids)
                if len(ids) == 4:
                    raise utils.TransferLimitExceededError(500, 520)

        api = FakeAPI()
        transfer = BaiduFileTransfer(api, "share")
        transfer._user_id = 1
        transfer._share_id = 2
        batch = [
            {
                "item": {"id": index},
                "file_count": 100,
                "item_count": 120,
                "is_directory": True,
            }
            for index in range(1, 5)
        ]

        pending = await transfer._transfer_packed_batch(
            batch, "/target", defer_tail=True
        )

        self.assertEqual(api.calls, [[1, 2, 3, 4], [1, 2]])
        self.assertEqual([entry["item"]["id"] for entry in pending], [3, 4])

    async def test_deferred_tail_is_filled_with_later_directories(self):
        class FakeAPI:
            async def create_directory(self, path):
                pass

            async def list_dir_all(self, path):
                return []

        transfer = BaiduFileTransfer(FakeAPI(), "share", root_path="/target")
        transfer._user_id = 1
        transfer._share_id = 2
        counts = {"one": 200, "two": 200, "three": 200, "four": 100}
        transferred = []

        async def count_items(path, limit=500):
            count = counts[path.rsplit("/", 1)[-1]]
            transfer._count_scan_directories = 1
            transfer._count_scan_files = count - 1
            return count

        async def transfer_batch(batch, target_path, defer_tail=False):
            ids = [entry["item"]["id"] for entry in batch]
            if ids == [1, 2] and defer_tail:
                transferred.append([1])
                return batch[1:]
            transferred.append(ids)
            return []

        transfer._count_share_directory_items = count_items
        transfer._transfer_packed_batch = transfer_batch
        directories = [
            {"id": index, "name": name}
            for index, name in enumerate(counts, start=1)
        ]

        await transfer._sync_contents("/source", "/target", directories, [])

        self.assertEqual(transferred, [[1], [2, 3, 4]])

    async def test_directories_are_kept_whole_when_the_batch_reaches_500(self):
        transfer = BaiduFileTransfer(None, "share", root_path="/target")
        batches = []

        async def record(batch, target_path):
            batches.append(([entry["file_count"] for entry in batch], target_path))

        transfer._transfer_packed_batch = record
        entries = [
            {"item": {"id": index}, "file_count": count, "is_directory": True}
            for index, count in enumerate([400, 200, 250, 600, 100])
        ]

        await transfer.transfer_packed_items(entries, "")

        self.assertEqual(
            batches,
            [([400], ""), ([200, 250], ""), ([600], ""), ([100], "")],
        )

    async def test_source_item_limit_is_also_respected(self):
        transfer = BaiduFileTransfer(None, "share", root_path="/target")
        batches = []

        async def record(batch, target_path):
            batches.append(len(batch))

        transfer._transfer_packed_batch = record
        entries = [
            {"item": {"id": index}, "file_count": 0, "is_directory": True}
            for index in range(501)
        ]

        await transfer.transfer_packed_items(entries, "")

        self.assertEqual(batches, [500, 1])

    async def test_single_directory_rejected_by_api_descends_one_level(self):
        class FakeLimitError(Exception):
            pass

        class FakeAPI:
            def __init__(self):
                self.transfer_calls = []
                self.list_calls = []

            async def transfer(self, user_id, share_id, ids, target_path):
                self.transfer_calls.append((ids, target_path))
                from baidutransfer import utils

                raise utils.TransferLimitExceededError(500, 592)

            async def list_share_dir(self, user_id, share_id, path):
                self.list_calls.append(path)
                return ([], [])

        api = FakeAPI()
        transfer = BaiduFileTransfer(api, "share")
        transfer._user_id = 1
        transfer._share_id = 2
        synced = []

        async def record_sync(share_path, target_path, dir_list, file_list):
            synced.append((share_path, target_path, dir_list, file_list))

        transfer._sync_contents = record_sync
        batch = [
            {
                "item": {"id": 9, "name": "large"},
                "file_count": 400,
                "is_directory": True,
                "share_path": "/source/large",
            }
        ]

        await transfer._transfer_packed_batch(batch, "/target")

        self.assertEqual(api.list_calls, ["/source/large"])
        self.assertEqual(
            synced,
            [("/source/large", "/target/large", [], [])],
        )

    async def test_explicit_resume_path_bypasses_earlier_siblings(self):
        class FakeAPI:
            def __init__(self):
                self.share_list_calls = []
                self.transfer_calls = []

            async def create_directory(self, path):
                pass

            async def list_share_dir(self, user_id, share_id, path):
                self.share_list_calls.append(path)
                return ([], [])

            async def list_dir_all(self, path):
                return []

            async def transfer(self, user_id, share_id, ids, target_path):
                self.transfer_calls.append((ids, target_path))

        api = FakeAPI()
        transfer = BaiduFileTransfer(
            api,
            "share",
            root_path="/target",
            resume_target_path="/target/cursor",
        )
        transfer._user_id = 1
        transfer._share_id = 2
        directories = [
            {"id": 1, "name": "before"},
            {"id": 2, "name": "cursor"},
            {"id": 3, "name": "after"},
        ]

        await transfer._sync_contents("/source", "/target", directories, [])

        self.assertNotIn("/source/before", api.share_list_calls)
        self.assertEqual(api.share_list_calls, ["/source/cursor", "/source/after"])
        self.assertEqual(api.transfer_calls, [([3], "/target")])


if __name__ == "__main__":
    unittest.main()

