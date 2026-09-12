# Feather UI Custom Themes

The **Theme Editor** lets you create a custom Feather UI theme in your browser,
preview the changes, and save a ready-to-use JSON file.

You do not need to edit JSON manually or install extra Python packages.

## 1. Install Python

You need **Python 3.10 or newer**.

If Python is already installed, skip to [Start the Theme Editor](#2-start-the-theme-editor).

### Windows

Download Python from:

<https://www.python.org/downloads/windows/>

During installation, enable **Add python.exe to PATH**. Then open a new PowerShell window and check that Python works:

```powershell
py -3 --version
```

### macOS

The easiest option if you already use [Homebrew](https://brew.sh/) is:

```bash
brew install python
```

Alternatively, download the official macOS installer from:

<https://www.python.org/downloads/macos/>

After installation, open a new terminal and check that Python works:

```bash
python3 --version
```

### Linux

Python 3 is already installed on many distributions. Check first:

```bash
python3 --version
```

If it is missing, install it with your package manager. For example, on Debian / Ubuntu:

```bash
sudo apt update
sudo apt install python3
```

On Fedora:

```bash
sudo dnf install python3
```

On Arch Linux:

```bash
sudo pacman -S python
```

## 2. Start the Theme Editor

The editor is located in:

```text
.py/klipper/plugins/ui/themes/
```

Open a terminal in that directory. You can either navigate there with `cd`, or open a terminal directly from the folder in your file manager. Then run:

### Windows

```powershell
py -3 theme_editor.py
```

### macOS / Linux

```bash
python3 theme_editor.py
```

The editor starts locally on your computer and should open automatically in your default browser. No account or external web service is required.

If it does not, open the address shown in the terminal. The default is:

```text
http://127.0.0.1:8765/
```

Keep the terminal open while using the editor. Press `Ctrl+C` to stop it.

## 3. Create Your Theme

1. Choose an existing theme from **Base theme**.
2. Change the colors you want.
3. Use the live preview to check the result.
4. Enter a unique theme name.
5. Optionally enter a description.
6. Make sure the theme is reported as valid.
7. Open **Save** and click **Download JSON**.

The downloaded JSON file is your custom theme.

When the editor is started from the printer, the **Save** menu also offers
**Apply to printer**. It saves the theme in `mod_data/themes/` and activates it
immediately. Apply is disabled while a print is active or paused.

## 4. Install the Theme

1. Open **Fluidd** or **Mainsail**.
2. Open the printer configuration files.
3. Create this directory if it does not already exist:

```text
mod_data/themes/
```

4. Upload the downloaded JSON file there.

For example:

```text
mod_data/themes/my_custom_theme.json
```

5. Reload or restart the UI if the theme does not appear immediately.

Custom themes belong in `mod_data/themes/`. You do not need to modify Feather UI's internal files.

## Troubleshooting

**Python command not found**  
Install Python 3.10 or newer, then close and reopen the terminal before trying again. On Windows use `py -3`; on macOS and Linux use `python3`.

**Python is installed, but the version is too old**  
Run `py -3 --version` on Windows or `python3 --version` on macOS / Linux. The editor requires Python 3.10 or newer.

**The browser did not open**  
Open the URL printed in the terminal. The default is `http://127.0.0.1:8765/`.

**`mod_data/themes/` does not exist**  
Create it in the Fluidd or Mainsail configuration file browser.

## For Developers

The editor also supports optional JSON Schema validation with `jsonschema`.
Its HTML, CSS, and JavaScript files live in `www/`. The separate
`theme_preview.py` tool can generate static theme previews and requires Pillow.
Neither is needed for normal theme creation.
