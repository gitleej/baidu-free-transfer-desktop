import os
import tempfile
import unittest

from baidutransfer.runner import load_cookie, normalize_netdisk_path, parse_share_url
from baidutransfer.settings import TransferSettings


class SettingsTests(unittest.TestCase):
    def test_default_settings_are_valid(self):
        self.assertIsInstance(TransferSettings().validate(), TransferSettings)

    def test_minimum_cannot_exceed_maximum(self):
        with self.assertRaises(ValueError):
            TransferSettings(request_interval_min=61, request_interval_max=60).validate()

    def test_batch_limit_cannot_exceed_baidu_limit(self):
        with self.assertRaises(ValueError):
            TransferSettings(max_files_per_request=501).validate()

    def test_share_password_is_read_from_url(self):
        key, password = parse_share_url(
            "https://pan.baidu.com/s/1abcdefgh?pwd=Ab12"
        )
        self.assertEqual(key, "abcdefgh")
        self.assertEqual(password, "Ab12")

    def test_paths_are_normalized(self):
        self.assertEqual(normalize_netdisk_path("纸样2000/子目录/"), "/纸样2000/子目录")

    def test_explicit_cookie_takes_priority_without_reading_file(self):
        self.assertEqual(load_cookie("BDUSS=value", "missing.yaml"), "BDUSS=value")

    def test_cookie_can_be_read_from_yaml(self):
        handle, path = tempfile.mkstemp(suffix=".yaml")
        try:
            os.close(handle)
            with open(path, "w", encoding="utf-8") as fp:
                fp.write('cookie: "BDUSS=value"\n')
            self.assertEqual(load_cookie("", path), "BDUSS=value")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
