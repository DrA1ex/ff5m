# Forge-X Binary Components License

Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>

**In short:** the Forge-X binary components are **free to use**, including for business and commercial use of a printer, as part of Forge-X. You can pass on unchanged copies together with Forge-X. You cannot modify them, take them apart, or put them into other products or projects without permission. Their source code is not published.

## Which files this covers

The native programs that Forge-X ships in [`.bin/exec/`](.bin/exec/), built from the author's own source code:

- `boot_mcu`
- `demo`
- `logged`
- `netd`
- `netd-cli`
- `splash`
- `typer`

These are called the **Components**. Everything else in this repository is **not** covered by this license, see [Other files](#other-files).

## What you may do

1. **Use** the Components free of charge, for any purpose, including commercial use (for example a print farm, a workshop, or any business that runs a printer), as part of Forge-X on a Flashforge Adventurer 5M or Adventurer 5M Pro.
2. **Copy and pass on unchanged** Components, free of charge, as part of an unchanged Forge-X release or an unchanged copy or fork of the Forge-X repository <https://github.com/DrA1ex/ff5m>, together with this license and the copyright notices.

## What you may not do without written permission

1. **Modify, adapt or translate** the Components, or create anything derived from them.
2. **Reverse engineer, decompile or disassemble** the Components, except where the law does not allow this to be forbidden.
3. **Use the Components in or with other products, projects, firmware or services**, or on other devices or platforms, whether you charge for it or not. Using Forge-X as a whole (as above) is fine.
4. **Sell, rent, lease or license** the Components, or charge for access to them or for passing them on, apart from Forge-X.
5. **Remove or change** copyright notices or this license.

To ask for permission, contact the author through the Forge-X [Telegram support group](https://t.me/+ihE2Ry8kBNkwYzhi) or the [Discord server](https://discord.gg/K7MH4hAfeX).

## Source code

The source code of the Components is not published, and this license does not give you a right to it.

## No warranty

THE COMPONENTS ARE PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NON-INFRINGEMENT. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY ARISING FROM THE USE OF THE COMPONENTS. Changing the firmware of a printer carries a risk of damage, and you do it at your own risk.

## Ending of the license

If you break these terms, your rights under this license end until the author gives them back.

## Earlier releases

Copies of the Components that were released under the GNU GPLv3 before this license was introduced stay under the GPLv3 for the people who received them.

## Third-party material inside the Components

The Components contain material from other projects, which stays under its own license:

- **argparse** (<https://github.com/p-ranav/argparse>), MIT License, Copyright (c) 2019-2022 Pranav Srinivas Kumar and other contributors. Used by `splash`, `logged` and `typer`.

  Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

- **JetBrains Mono**, SIL Open Font License 1.1 (<https://openfontlicense.org>), Copyright The JetBrains Mono Project Authors (<https://github.com/JetBrains/JetBrainsMono>). Bitmap renderings of the font are built into the Components.
- **Roboto**, Apache License 2.0 (<https://www.apache.org/licenses/LICENSE-2.0>), Copyright The Roboto Project Authors / Google. Bitmap renderings of the font are built into the Components.

The Components also use the C++ runtime library `libstdc++` (GNU GPLv3 with the GCC Runtime Library Exception), which ships next to them in [`.bin/runtime/`](.bin/runtime/).

## Other files

This license does not cover the following parts of Forge-X. They keep their own licenses:

| Part | License |
| --- | --- |
| Forge-X itself: scripts, Python code, macros, configuration, documentation, themes | GNU GPLv3, see [`LICENSE`](LICENSE) |
| Klipper modules and their patches in `.py/klipper/`, including the `c_helper.so` helper | GNU GPLv3 (derived from Klipper); the source is in [DrA1ex/klipper-ad5m](https://github.com/DrA1ex/klipper-ad5m) |
| forge-x-streamer, the camera service in `.bin/exec/` | GNU GPL-2.0-or-later, see [Camera](docs/CAMERA.md) |
| Linux kernel modules `zram.ko` and `zsmalloc.ko`, and other third-party components | their own licenses (GPL for the kernel modules) |
