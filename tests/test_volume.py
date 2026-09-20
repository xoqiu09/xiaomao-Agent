from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xiaomao.volume import is_usable, verify_mount, volume_uuid


class VolumeIdentityTests(unittest.TestCase):
    def test_uuid_of_root_volume(self) -> None:
        got, note = volume_uuid("/")
        self.assertIsNotNone(got, f"root volume uuid unavailable: {note}")
        self.assertEqual(len(got or ""), 36)

    def test_missing_path(self) -> None:
        got, note = volume_uuid("/nope-does-not-exist-xiaomao")
        self.assertIsNone(got)
        self.assertIn("exist", note)

    def test_non_mountpoint_is_not_usable(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        report = verify_mount(tmp, None)
        self.assertFalse(report["mounted"])
        self.assertFalse(is_usable(tmp, None))

    def test_mismatched_uuid_is_rejected(self) -> None:
        report = verify_mount("/", "00000000-0000-0000-0000-000000000000")
        self.assertEqual(report["status"], "mismatch")
        self.assertFalse(is_usable("/", "00000000-0000-0000-0000-000000000000"))

    def test_matching_uuid_is_accepted(self) -> None:
        actual, _ = volume_uuid("/")
        self.assertIsNotNone(actual)
        report = verify_mount("/", actual)
        self.assertEqual(report["status"], "match")
        self.assertTrue(is_usable("/", actual))

    def test_no_uuid_configured_is_unverified_not_match(self) -> None:
        report = verify_mount("/", None)
        self.assertEqual(report["status"], "unverified")


class FailClosedTests(unittest.TestCase):
    def _cfg(self, mount: str, expected: str | None):
        from xiaomao.config import default_config

        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        cfg.external.mount = mount
        cfg.external.volume_uuid = expected
        return cfg

    def test_mismatch_raises(self) -> None:
        from xiaomao.config import VolumeIdentityError, require_external_volume

        cfg = self._cfg("/", "00000000-0000-0000-0000-000000000000")
        with self.assertRaises(VolumeIdentityError) as ctx:
            require_external_volume(cfg)
        self.assertIn("身份不符", str(ctx.exception))

    def test_unmounted_raises(self) -> None:
        from xiaomao.config import VolumeIdentityError, require_external_volume

        cfg = self._cfg("/nope-xiaomao-not-mounted", "00000000-0000-0000-0000-000000000000")
        with self.assertRaises(VolumeIdentityError):
            require_external_volume(cfg)

    def test_match_returns_report(self) -> None:
        from xiaomao.config import require_external_volume

        actual, _ = volume_uuid("/")
        cfg = self._cfg("/", actual)
        report = require_external_volume(cfg)
        self.assertEqual(report["status"], "match")

    def test_default_config_carries_verified_uuid(self) -> None:
        from xiaomao.config import default_config
        from xiaomao.paths import DEFAULT_EXTERNAL_UUID

        cfg = default_config(Path(self.enterContext(tempfile.TemporaryDirectory())))
        self.assertEqual(cfg.external.volume_uuid, DEFAULT_EXTERNAL_UUID)
        self.assertTrue(cfg.external.models_dir.endswith("/Xiaomao/ollama"))

    def test_unknown_uuid_raises(self) -> None:
        from xiaomao.config import VolumeIdentityError, require_external_volume
        from xiaomao.volume import volume_uuid as real_uuid

        cfg = self._cfg("/", "44c5480a-388c-475e-a320-a49b226a5953")
        # Force getattrlist failure path by pointing at a file, not a volume.
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory())) / "not-a-volume"
        tmp.write_text("x")
        cfg.external.mount = str(tmp)
        cfg.external.volume_uuid = "44c5480a-388c-475e-a320-a49b226a5953"
        with self.assertRaises(VolumeIdentityError) as ctx:
            require_external_volume(cfg)
        self.assertTrue("未挂载" in str(ctx.exception) or "无法确认" in str(ctx.exception))
        self.assertTrue(real_uuid("/")[0])
