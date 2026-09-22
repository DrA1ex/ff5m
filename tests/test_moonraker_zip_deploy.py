"""Regression tests for replacing a downloaded Moonraker web client."""

import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile


PACKAGE = "_ff5m_test_zip_deploy"
for suffix in ("", ".components", ".components.update_manager", ".utils"):
    module = types.ModuleType(PACKAGE + suffix)
    module.__path__ = []
    sys.modules[module.__name__] = module

app_deploy = types.ModuleType(PACKAGE + ".components.update_manager.app_deploy")
app_deploy.AppDeploy = type("AppDeploy", (), {})
sys.modules[app_deploy.__name__] = app_deploy
common = types.ModuleType(PACKAGE + ".components.update_manager.common")
common.Channel = type("Channel", (), {})
common.AppType = type("AppType", (), {})
sys.modules[common.__name__] = common
utils = sys.modules[PACKAGE + ".utils"]
utils.source_info = types.ModuleType(PACKAGE + ".utils.source_info")
utils.json_wrapper = types.ModuleType(PACKAGE + ".utils.json_wrapper")

module_path = (pathlib.Path(__file__).parents[1] / ".root" / "moonraker" /
               "components" / "update_manager" / "zip_deploy.py")
spec = importlib.util.spec_from_file_location(
    PACKAGE + ".components.update_manager.zip_deploy", module_path)
zip_deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zip_deploy)


class ZipDeployExtractionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = pathlib.Path(self.temp.name)
        self.install = root / "fluidd"
        self.install.mkdir()
        (self.install / "index.html").write_text("installed")
        self.archive = root / "release.zip"
        self.persist = root / "persistent"
        self.deployer = object.__new__(zip_deploy.ZipDeploy)
        self.deployer.path = self.install
        self.deployer.persistent_files = []

    def test_error_page_does_not_remove_installation(self):
        self.archive.write_text("release not found")
        with self.assertRaises(zipfile.BadZipFile):
            self.deployer._extract_release(self.persist, self.archive)
        self.assertEqual((self.install / "index.html").read_text(), "installed")

    def test_corrupt_archive_does_not_remove_installation(self):
        with zipfile.ZipFile(self.archive, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("index.html", "new version")
        data = self.archive.read_bytes().replace(b"new version", b"bad version")
        self.archive.write_bytes(data)
        with self.assertRaises(zipfile.BadZipFile):
            self.deployer._extract_release(self.persist, self.archive)
        self.assertEqual((self.install / "index.html").read_text(), "installed")

    def test_valid_archive_replaces_installation(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.writestr("index.html", "new version")
        self.deployer._extract_release(self.persist, self.archive)
        self.assertEqual((self.install / "index.html").read_text(), "new version")


if __name__ == "__main__":
    unittest.main()
