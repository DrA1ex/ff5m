"""Native theme installation must never overwrite personal configuration."""
import base64
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.shell/web_themes.sh'


class WebThemesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.bundle = self.root / 'bundle'
        for name in ('.theme', '.fluidd-theme'):
            (self.bundle / name).mkdir(parents=True)
            (self.bundle / name / 'custom.css').write_text('/* theme */')

    def run_helper(self, action):
        subprocess.run(['sh', str(SCRIPT), action, str(self.bundle), str(self.config)],
                       check=True, capture_output=True)

    def test_managed_links_follow_bundle_updates_and_are_removed(self):
        self.run_helper('install')
        self.run_helper('install')
        for name in ('.theme', '.fluidd-theme'):
            self.assertEqual((self.config / name).readlink(), self.bundle / name)
            (self.bundle / name / 'custom.css').write_text('/* updated */')
            self.assertEqual((self.config / name / 'custom.css').read_text(), '/* updated */')
        self.run_helper('remove')
        self.assertEqual(list(self.config.iterdir()), [])

    def test_personal_directory_is_preserved_during_install_and_remove(self):
        personal = self.config / '.theme'
        personal.mkdir()
        (personal / 'custom.css').write_text('personal')
        self.run_helper('install')
        self.run_helper('remove')
        self.assertEqual((personal / 'custom.css').read_text(), 'personal')

    def test_other_dangling_symlink_is_preserved(self):
        target = self.config / '.theme'
        target.symlink_to(self.root / 'personal-unmounted')
        self.run_helper('install')
        self.run_helper('remove')
        self.assertTrue(target.is_symlink())
        self.assertEqual(target.readlink(), self.root / 'personal-unmounted')

    def test_existing_regular_file_is_preserved(self):
        target = self.config / '.theme'
        target.write_text('personal')
        self.run_helper('install')
        self.run_helper('remove')
        self.assertEqual(target.read_text(), 'personal')

    def test_package_is_native_assets_with_embedded_fonts_and_no_javascript(self):
        package = self.root / 'package'
        shutil.copytree(ROOT / 'web-themes', package, ignore=shutil.ignore_patterns('*.zip', '__pycache__'))
        subprocess.run(['python3', str(package / 'build.py')], check=True, capture_output=True)
        with zipfile.ZipFile(package / 'forge-x-feather-theme.zip') as archive:
            self.assertEqual(len(archive.namelist()), 7)
            self.assertTrue(all(n.startswith(('.theme/', '.fluidd-theme/')) for n in archive.namelist()))
            self.assertFalse(any(n.endswith(('.js', '.html')) for n in archive.namelist()))
            for name in ('.theme/custom.css', '.fluidd-theme/custom.css'):
                css = archive.read(name).decode()
                self.assertNotIn('__FONT_', css)
                self.assertNotIn('__FORGE_X_', css)
                fonts = re.findall(r'data:font/woff2;base64,([A-Za-z0-9+/=]+)', css)
                self.assertEqual(len(fonts), 2)
                for font in fonts:
                    self.assertTrue(base64.b64decode(font).startswith(b'wOF2'))


if __name__ == '__main__':
    unittest.main()
