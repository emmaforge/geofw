import hashlib
import importlib.machinery
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "tools" / "sync_apnic.py"
sync = importlib.machinery.SourceFileLoader("sync_apnic", str(SOURCE)).load_module()


class SnapshotSyncTests(unittest.TestCase):
    def setUp(self):
        rows = [f"apnic|CN|ipv4|1.0.{i * 2}.0|256|20261101|allocated"
                for i in range(120)]
        rows += [f"apnic|CN|ipv6|240e:0:{i * 2:x}::|48|20261101|allocated"
                 for i in range(120)]
        self.raw = "2|apnic|20261101|240|19830613|20261031|+1000\n" + "\n".join(rows)

    def test_sync_updates_snapshot_and_pinned_hash_together(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "geofw"
            snapshot = Path(directory) / "apnic-cn.txt"
            program.write_text('APNIC_MIRROR_DATE = "20261031"\n'
                               f'APNIC_MIRROR_SHA256 = "{"0" * 64}"\n', encoding="utf-8")
            with mock.patch.object(sync.geofw, "today_cn_date", return_value="20261101"):
                self.assertTrue(sync.update_from_text(self.raw, program, snapshot))
            content = snapshot.read_bytes()
            self.assertTrue(content.startswith(b"# APNIC delegated-apnic-latest CN IPv4/IPv6 snapshot 20261101\n"))
            updated = program.read_text(encoding="utf-8")
            self.assertIn('APNIC_MIRROR_DATE = "20261101"', updated)
            digest = re.search(r'APNIC_MIRROR_SHA256 = "([0-9a-f]{64})"', updated).group(1)
            self.assertEqual(hashlib.sha256(content).hexdigest(), digest)

    def test_sync_waits_for_current_day_source(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "geofw"
            snapshot = Path(directory) / "apnic-cn.txt"
            program.write_text('APNIC_MIRROR_DATE = "20261031"\n'
                               f'APNIC_MIRROR_SHA256 = "{"0" * 64}"\n', encoding="utf-8")
            with mock.patch.object(sync.geofw, "today_cn_date", return_value="20261102"):
                self.assertFalse(sync.update_from_text(self.raw, program, snapshot))
            self.assertFalse(snapshot.exists())
            self.assertIn('APNIC_MIRROR_DATE = "20261031"', program.read_text())


if __name__ == "__main__":
    unittest.main()
