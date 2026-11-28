"""Regression tests for replacing a downloaded Moonraker web client."""

import importlib.util
import asyncio
from unittest.mock import AsyncMock
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
        self.deployer.asset_name = None
        self.deployer.prefix = 'phobos: '
        self.deployer.server = types.SimpleNamespace(error=RuntimeError)

    def test_named_asset_selects_correct_architecture_independent_of_order(self):
        self.deployer.asset_name = 'phobos-arm-linux-musleabihf.zip'
        correct = {'name': 'phobos-arm-linux-musleabihf.zip'}
        release = {'tag_name': 'v0.3.0', 'assets': [
            {'name': 'phobos-v0.3.0-aarch64-linux-musl'}, correct,
            {'name': 'phobos-v0.3.0-arm-linux-musleabihf'}]}
        self.assertIs(self.deployer._select_release_asset(release), correct)

    def test_missing_or_duplicate_named_asset_preserves_installation(self):
        self.deployer.asset_name = 'phobos-arm-linux-musleabihf.zip'
        correct = {'name': 'phobos-arm-linux-musleabihf.zip'}
        for assets in ([], [{'name': 'wrong.zip'}], [correct, correct]):
            with self.assertRaises(RuntimeError):
                self.deployer._select_release_asset({'tag_name': 'v0.3.0', 'assets': assets})
            self.assertEqual((self.install / 'index.html').read_text(), 'installed')

    def test_legacy_clients_still_use_first_asset(self):
        first = {'name': 'fluidd.zip'}
        self.assertIs(self.deployer._select_release_asset({'assets': [first]}), first)
        self.assertEqual(self.deployer._select_release_asset({'assets': []}), {})

    def test_named_remote_version_uses_tag_not_friendly_release_title(self):
        self.deployer.asset_name = 'phobos-arm-linux-musleabihf.zip'
        self.deployer._fetch_github_version = AsyncMock(return_value={
            'tag_name': 'v0.3.0', 'name': 'Phobos release 0.3.0',
            'assets': [{'name': 'wrong-architecture'}, {
                'name': self.deployer.asset_name, 'browser_download_url': 'https://example.invalid/arm.zip',
                'content_type': 'application/zip', 'size': 42}]})
        asyncio.run(self.deployer._get_remote_version())
        self.assertEqual(self.deployer.remote_version, 'v0.3.0')
        self.assertEqual(self.deployer.dl_info, ('https://example.invalid/arm.zip', 'application/zip', 42))

    def test_rollback_uses_same_named_asset_selector(self):
        self.deployer.asset_name = 'phobos-arm-linux-musleabihf.zip'
        self.deployer.rollback_version = 'v0.3.0'
        self.deployer.rollback_repo = 'maci0/phobos'
        self.deployer.version = 'v0.4.0'
        self.deployer._fetch_github_version = AsyncMock(return_value={
            'tag_name': 'v0.3.0', 'assets': [{'name': 'wrong-architecture'}, {
                'name': self.deployer.asset_name, 'browser_download_url': 'https://example.invalid/old-arm.zip',
                'content_type': 'application/zip', 'size': 42}]})
        self.deployer.update = AsyncMock(return_value=True)
        self.assertTrue(asyncio.run(self.deployer.rollback()))
        self.deployer._fetch_github_version.assert_awaited_once_with('maci0/phobos', 'v0.3.0')
        self.deployer.update.assert_awaited_once_with(('https://example.invalid/old-arm.zip', 'application/zip', 42))

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
