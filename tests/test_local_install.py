"""Protect the local install's source and existing-data boundaries."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
import zipfile

spec = importlib.util.spec_from_file_location("local_install", Path(__file__).parents[1] / "scripts/local_install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class LocalInstallBoundaries(unittest.TestCase):
    def test_cleanup_eperm_requires_no_live_group_members(self):
        from unittest.mock import patch
        with patch.object(installer.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")), patch.object(installer.subprocess, "check_output", return_value="9833 Z\n42 S\n"):
            self.assertFalse(installer.owned_group_alive(9833))
        with patch.object(installer.os, "killpg", side_effect=PermissionError(1, "Operation not permitted")), patch.object(installer.subprocess, "check_output", return_value="9833 S\n"):
            self.assertTrue(installer.owned_group_alive(9833))

    def test_failed_app_process_cannot_pass_startup_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "app startup exited with 7"):
                installer.check_app([sys.executable, "-c", "raise SystemExit(7)"],
                                    cwd=root, env=dict(os.environ), root=root,
                                    url="http://127.0.0.1:1", runtime_instance_id="proof", timeout=5)
            self.assertFalse((root / "app-receipt.json").exists())

    def test_failed_chat_check_disposes_its_connection(self):
        import io
        import json
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime-receipt.json").write_text(json.dumps({"project_create_readback": {"project_id": "smoke"}}))
            replies = [{"connection_id": "owned", "initialize": {}},
                       {"operation_session_id": "session"}, {"result": {}},
                       {"disconnected": True, "notifications": []}, {"closed": True}]
            with patch.object(installer.urllib.request, "urlopen", side_effect=[io.BytesIO(json.dumps(reply).encode()) for reply in replies]) as opening:
                with self.assertRaisesRegex(RuntimeError, "ACP smoke did not complete"):
                    installer.check_acp("http://localhost:2223", root)
                last_request = opening.call_args.args[0]
                self.assertEqual(last_request.method, "DELETE")
                self.assertTrue(last_request.full_url.endswith("/acp/owned"))
            self.assertFalse((root / "acp-receipt.json").exists())

    def test_missing_media_decoder_fails_before_provision(self):
        with self.assertRaisesRegex(ValueError, "ffmpeg is required for media import"):
            installer.media_tools({"PATH": "/nonexistent-local-install-test"}, {})

    def test_missing_agent_runtime_fails_before_provision(self):
        with self.assertRaisesRegex(ValueError, "bun is required for Astrid chat"):
            installer.agent_tools({"PATH": "/nonexistent-local-install-test"}, {})

    def test_stale_wheel_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "astrid_runtime").mkdir(parents=True)
            (source / "astrid_runtime/bootstrap.py").write_text("scopes = ['projects:read']\n")
            wheel = root / "runtime.whl"
            with zipfile.ZipFile(wheel, "w") as bundle:
                bundle.writestr("astrid_runtime/bootstrap.py", "scopes = []\n")
            with self.assertRaisesRegex(ValueError, "does not match selected source"):
                installer.wheel_parity(wheel, source, ("astrid_runtime/",))

    def test_missing_source_module_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            (source / "runtime_protocol").mkdir(parents=True)
            (source / "runtime_protocol/__init__.py").write_text("")
            (source / "runtime_protocol/daemon.py").write_text("required = True\n")
            wheel = root / "runtime.whl"
            with zipfile.ZipFile(wheel, "w") as bundle:
                bundle.writestr("runtime_protocol/__init__.py", "")
            with self.assertRaisesRegex(ValueError, "omits source modules"):
                installer.wheel_parity(wheel, source, ("runtime_protocol/",))

    def test_existing_install_is_refused_without_running_commands(self):
        from argparse import Namespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            install = root / "install"; install.mkdir()
            sentinel = install / "user-data"; sentinel.write_bytes(b"preserve")
            args = Namespace(root=str(install), astrid_root=str(root / "astrid"),
                             runtime_root=str(root / "runtime"), app_root=str(root / "app"))
            with patch.object(installer.sys, "platform", "darwin"), patch.object(installer.sys, "version_info", (3, 11)), patch.object(installer, "run") as run:
                with self.assertRaisesRegex(ValueError, "absent or empty"):
                    installer.install(args)
                run.assert_not_called()
            self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_dirty_checkout_is_not_silently_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "new-user-change.py").write_text("preserve = True\n")
            with self.assertRaisesRegex(ValueError, "must be clean"):
                installer.source_identity(root, dict(os.environ))


if __name__ == "__main__":
    unittest.main()
