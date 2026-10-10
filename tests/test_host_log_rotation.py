import gzip
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from astrid.core.execution import host_log
from astrid.core.execution.host_log import (
    StdioLogRotator,
    host_log_limit_bytes,
    host_log_section,
    rotate_if_needed,
)

MB = 1024 * 1024


class HostLogLimitTest(unittest.TestCase):
    def test_default_is_fifty_megabytes(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(host_log.HOST_LOG_MB_ENV, None)
            self.assertEqual(host_log_limit_bytes(), 50 * MB)

    def test_env_override_and_disable(self) -> None:
        self.assertEqual(host_log_limit_bytes({"ASTRID_HOST_LOG_MB": "2"}), 2 * MB)
        self.assertEqual(host_log_limit_bytes({"ASTRID_HOST_LOG_MB": "0"}), 0)

    def test_invalid_env_falls_back_to_default(self) -> None:
        self.assertEqual(host_log_limit_bytes({"ASTRID_HOST_LOG_MB": "lots"}), 50 * MB)


class RotateIfNeededTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.log = self.root / "generic-host.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_below_cap_is_untouched(self) -> None:
        self.log.write_bytes(b"x" * 10)
        self.assertFalse(rotate_if_needed(self.log, limit=100))
        self.assertEqual(self.log.read_bytes(), b"x" * 10)
        self.assertFalse((self.root / "generic-host.log.1").exists())

    def test_missing_file_is_a_no_op(self) -> None:
        self.assertFalse(rotate_if_needed(self.log, limit=100))

    def test_over_cap_shifts_backups_and_keeps_three(self) -> None:
        for index, payload in enumerate((b"old1", b"old2", b"old3"), start=1):
            (self.root / f"generic-host.log.{index}").write_bytes(payload)
        self.log.write_bytes(b"live" * 50)
        self.assertTrue(rotate_if_needed(self.log, limit=100, backups=3))
        self.assertFalse(self.log.exists())
        self.assertEqual((self.root / "generic-host.log.1").read_bytes(), b"live" * 50)
        self.assertEqual((self.root / "generic-host.log.2").read_bytes(), b"old1")
        self.assertEqual((self.root / "generic-host.log.3").read_bytes(), b"old2")
        self.assertFalse((self.root / "generic-host.log.4").exists())

    def test_size_stays_bounded_across_many_rotations(self) -> None:
        for _ in range(10):
            self.log.write_bytes(b"y" * 200)
            rotate_if_needed(self.log, limit=100, backups=3)
        names = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(names, ["generic-host.log.1", "generic-host.log.2", "generic-host.log.3"])

    def test_archived_gzip_from_earlier_incident_is_not_touched(self) -> None:
        archive = self.root / "generic-host.log.20261010-0529.gz"
        with gzip.open(archive, "wb") as handle:
            handle.write(b"history")
        self.log.write_bytes(b"z" * 200)
        rotate_if_needed(self.log, limit=100, backups=3)
        self.assertTrue(archive.exists())


class StdioLogRotatorTest(unittest.TestCase):
    """Exercises the live fd-redirect path against the real fd 1."""

    def test_rotates_only_when_fd1_is_the_log_and_redirects_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "generic-host.log"
            saved_stdout = os.dup(1)
            saved_stderr = os.dup(2)
            try:
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                os.dup2(fd, 1)
                os.dup2(fd, 2)
                os.close(fd)
                os.write(1, b"a" * 300)
                rotator = StdioLogRotator(log, limit=100, backups=3)
                self.assertTrue(rotator.check())
                os.write(1, b"fresh\n")
                os.write(2, b"err\n")
            finally:
                os.dup2(saved_stdout, 1)
                os.dup2(saved_stderr, 2)
                os.close(saved_stdout)
                os.close(saved_stderr)
            self.assertEqual((Path(tmp) / "generic-host.log.1").read_bytes(), b"a" * 300)
            self.assertEqual(log.read_bytes(), b"fresh\nerr\n")

    def test_does_not_rotate_when_fd1_is_not_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "generic-host.log"
            other = Path(tmp) / "other.log"
            log.write_bytes(b"b" * 300)
            saved_stdout = os.dup(1)
            try:
                fd = os.open(other, os.O_WRONLY | os.O_CREAT, 0o644)
                os.dup2(fd, 1)
                os.close(fd)
                self.assertFalse(StdioLogRotator(log, limit=100).check())
            finally:
                os.dup2(saved_stdout, 1)
                os.close(saved_stdout)
            self.assertFalse((Path(tmp) / "generic-host.log.1").exists())


class HostLogSectionTest(unittest.TestCase):
    def test_reports_size_and_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "generic-host.log").write_bytes(b"q" * 64)
            with mock.patch.dict(os.environ, {"ASTRID_HOST_LOG_MB": "1"}):
                section = host_log_section(tmp)
            self.assertEqual(section["bytes"], 64)
            self.assertEqual(section["limit_bytes"], MB)
            self.assertFalse(section["over_limit"])

    def test_missing_support_root_reports_error(self) -> None:
        self.assertIn("error", host_log_section(None))


if __name__ == "__main__":
    unittest.main()
