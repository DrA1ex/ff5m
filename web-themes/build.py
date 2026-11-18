#!/usr/bin/env python3
"""Package a native Mainsail .theme; no app build, JavaScript or source patch."""
import base64
import pathlib
import shutil
import zipfile
from urllib.parse import quote

ROOT = pathlib.Path(__file__).resolve().parent
THEME = ROOT / '.theme'
THEME.mkdir(exist_ok=True)
css = (ROOT / 'assets/feather.css').read_text()
for token, filename in [('__FONT_REGULAR__', 'jetbrains-mono-regular.woff2'),
                        ('__FONT_BOLD__', 'jetbrains-mono-bold.woff2')]:
    data = base64.b64encode((ROOT / 'assets' / filename).read_bytes()).decode()
    assert css.count(token) == 1, token
    css = css.replace(token, 'data:font/woff2;base64,' + data)
assert '__FONT_' not in css
assert 'javascript:' not in css.lower()
(THEME / 'custom.css').write_text(css)
fluidd = ROOT / '.fluidd-theme'
fluidd.mkdir(exist_ok=True)
logo = (ROOT / 'assets/sidebar-logo.svg').read_text()
addon = (ROOT / 'assets/fluidd.css').read_text().replace('__FORGE_X_LOGO__', 'data:image/svg+xml,' + quote(logo, safe=''))
(fluidd / 'custom.css').write_text(css + '\n' + addon)
shutil.copyfile(ROOT / 'assets/JetBrainsMono-OFL.txt', fluidd / 'JetBrainsMono-OFL.txt')
for filename in ('sidebar-logo.svg', 'favicon-16x16.png', 'favicon-32x32.png', 'JetBrainsMono-OFL.txt'):
    shutil.copyfile(ROOT / 'assets' / filename, THEME / filename)
with zipfile.ZipFile(ROOT / 'forge-x-feather-theme.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for directory in (THEME, fluidd):
        for path in sorted(directory.iterdir()):
            archive.write(path, directory.name + '/' + path.name)
print('Built forge-x-feather-theme.zip — extract into the Moonraker config directory.')
