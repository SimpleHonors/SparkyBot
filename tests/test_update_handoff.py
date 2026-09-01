import tempfile
import unittest
import re
from pathlib import Path
from unittest import mock


def test_installer_version_matches_runtime_version():
    from core.version import VERSION

    script = (
        Path(__file__).parents[1] / "build" / "sparkybot.iss"
    ).read_text(encoding="utf-8")
    match = re.search(r'^#define MyAppVersion "([^"]+)"$', script, re.MULTILINE)

    assert match is not None
    assert match.group(1) == VERSION


def test_clean_windows_build_creates_writable_gw2ei_folder():
    script = (
        Path(__file__).parents[1] / "build" / "build_windows.bat"
    ).read_text(encoding="utf-8")

    assert "if exist GW2EI (" in script
    assert "mkdir dist\\SparkyBot\\GW2EI" in script


def test_windows_build_bundles_trait_evidence_catalog():
    spec = (
        Path(__file__).parents[1] / "build" / "sparkybot.spec"
    ).read_text(encoding="utf-8")

    assert (
        "(os.path.join(_repo_root, 'core', 'trait_evidence_catalog.json'), 'core')"
        in spec
    )


class UpdateHelperTests(unittest.TestCase):
    def test_apply_pending_tree_replaces_runtime_and_preserves_user_data(self):
        from core.update_helper import apply_pending_tree

        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "SparkyBot"
            pending = app_dir / ".update_pending"

            (app_dir / "_internal" / "PySide6").mkdir(parents=True)
            (pending / "_internal" / "PySide6").mkdir(parents=True)
            (app_dir / "GW2EI").mkdir()
            (pending / "GW2EI").mkdir()

            (app_dir / "SparkyBot.exe").write_bytes(b"old-exe")
            (app_dir / "_internal" / "PySide6" / "QtWidgets.pyd").write_bytes(
                b"old-qt"
            )
            (app_dir / "config.properties").write_text("user-config", encoding="utf-8")
            (app_dir / "GW2EI" / "settings.json").write_text("user-ei", encoding="utf-8")

            (pending / "SparkyBot.exe").write_bytes(b"new-exe")
            (pending / "_internal" / "PySide6" / "QtWidgets.pyd").write_bytes(
                b"new-qt"
            )
            (pending / "config.properties").write_text("release-config", encoding="utf-8")
            (pending / "GW2EI" / "settings.json").write_text("release-ei", encoding="utf-8")

            applied = apply_pending_tree(app_dir, pending, attempts=2, retry_delay=0)

            self.assertEqual(applied, 2)
            self.assertEqual((app_dir / "SparkyBot.exe").read_bytes(), b"new-exe")
            self.assertEqual(
                (app_dir / "_internal" / "PySide6" / "QtWidgets.pyd").read_bytes(),
                b"new-qt",
            )
            self.assertEqual(
                (app_dir / "config.properties").read_text(encoding="utf-8"),
                "user-config",
            )
            self.assertEqual(
                (app_dir / "GW2EI" / "settings.json").read_text(encoding="utf-8"),
                "user-ei",
            )
            self.assertFalse(pending.exists())

    @mock.patch("subprocess.Popen")
    def test_frozen_handoff_runs_dedicated_updater_copy(self, popen):
        from core.update_handoff import launch_frozen_update_helper

        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "SparkyBot"
            temp_dir = Path(tmp) / "temp"
            app_dir.mkdir()
            temp_dir.mkdir()
            (app_dir / "SparkyBotUpdater.exe").write_bytes(b"helper")

            launched = launch_frozen_update_helper(
                app_dir=app_dir,
                executable=app_dir / "SparkyBot.exe",
                process_id=4321,
                app_args=["--start-minimized"],
                temp_dir=temp_dir,
            )

            self.assertTrue(launched)
            command = popen.call_args.args[0]
            self.assertEqual(Path(command[0]), temp_dir / "SparkyBotUpdater.exe")
            self.assertNotEqual(Path(command[0]), app_dir / "SparkyBot.exe")
            self.assertIn("--wait-pid", command)
            self.assertIn("4321", command)
            self.assertIn("--start-minimized", command)


if __name__ == "__main__":
    unittest.main()
