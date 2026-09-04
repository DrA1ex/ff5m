"""Behavioral contracts for the early fail-open recovery path.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import hashlib
import importlib.util
import io
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
RECOVERY_PY = ROOT / ".py" / "recovery.py"
RECOVERY_SH = ROOT / ".shell" / "boot" / "recovery.sh"
BOOT_MODE_SH = ROOT / ".shell" / "boot" / "boot_mode.sh"
DEPLOYED_BOOT_MODE_SH = "/opt/config/mod/.shell/boot/boot_mode.sh"
S00_INIT = ROOT / ".shell" / "S00init"
INIT_MAIN = ROOT / ".shell" / "init-main.sh"
FORGE_X_SERVICE = ROOT / ".root" / "forge-x"
ROOT_START = ROOT / ".root" / "start.sh"
SUDO_SHIM = ROOT / ".root" / "sudo-shim"
ZCHECK = ROOT / ".shell" / "commands" / "zcheck.sh"
ZRESET_CONFIG = ROOT / ".shell" / "commands" / "zreset_config.sh"

SPEC = importlib.util.spec_from_file_location("forge_x_boot_recovery", RECOVERY_PY)
RECOVERY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECOVERY)


def firmware_archive_bytes(name="flashforge_init.sh"):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
        payload = b"#!/bin/bash\nexit 0\n"
        member = tarfile.TarInfo(name)
        member.size = len(payload)
        member.mode = 0o755
        archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


class FakeTyperSession:
    def __init__(self, taps=(), start_error=None):
        self.taps = list(taps)
        self.start_error = start_error
        self.frames = []

    def start(self):
        if self.start_error is not None:
            raise self.start_error

    def send(self, commands):
        self.frames.append(tuple(commands))

    def next_tap(self):
        return self.taps.pop(0)


class BootRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.boot_paths = {
            "INIT_FLAG": self.root / "ready",
            "SKIP_MOD_F": self.root / "stock",
            "SKIP_MOD_SOFT_F": self.root / "stock-soft",
            "SKIP_MOD_HARD_F": self.root / "stock-hard",
        }
        boot_replacements = {
            "/opt/config/mod/BOOT_FLAG_FAILURE": str(self.root / "boot-failure"),
            "/opt/config/mod/BOOT_FLAG_SKIP": str(self.root / "boot-skip"),
            "/opt/config/mod/BOOT_FLAG_RECOVERY_FAILURE": str(
                self.root / "recovery-failure"),
            "/tmp/init_finished_f": str(self.boot_paths["INIT_FLAG"]),
            "/tmp/SKIP_MOD_SOFT": str(self.boot_paths["SKIP_MOD_SOFT_F"]),
            "/tmp/SKIP_MOD_HARD": str(self.boot_paths["SKIP_MOD_HARD_F"]),
            "/tmp/SKIP_MOD": str(self.boot_paths["SKIP_MOD_F"]),
            "/tmp/forge_x_boot_reason": str(self.root / "boot-reason"),
        }
        self.boot_mode_sh = self._patched_script(
            BOOT_MODE_SH, "boot_mode.sh", boot_replacements)
        self.s00_init = self._patched_script(
            S00_INIT, "S00init", {
                "/tmp/forge_x_boot_reason": str(self.root / "boot-reason"),
            })
        self.boot_scripts = {}
        for source in (
                RECOVERY_SH,
                ROOT / ".shell" / "S55boot",
                ROOT / ".shell" / "S60dropbear",
                ROOT / ".shell" / "S98camera",
                ROOT / ".shell" / "S98zssh",
                ROOT / ".shell" / "S99root"):
            replacements = {DEPLOYED_BOOT_MODE_SH: str(self.boot_mode_sh)}
            if source == RECOVERY_SH:
                replacements.update({
                    "/opt/config/mod/.shell/common.sh": "/dev/null",
                    "/opt/config/mod/.shell/boot/stock_identity.sh": "/dev/null",
                })
            self.boot_scripts[source.name] = self._patched_script(
                source, source.name, replacements)

    def tearDown(self):
        self.temporary.cleanup()

    def _patched_script(self, source, name, replacements):
        text = source.read_text(encoding="utf-8")
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new)

        destination = self.root / name
        destination.write_text(text, encoding="utf-8")
        destination.chmod(source.stat().st_mode & 0o777)
        return destination

    def test_scripts_are_executable_and_have_valid_syntax(self):
        subprocess.run(["sh", "-n", str(BOOT_MODE_SH)], check=True)
        subprocess.run(["bash", "-n", str(RECOVERY_SH)], check=True)
        subprocess.run(["bash", "-n", str(S00_INIT)], check=True)
        subprocess.run(["bash", "-n", str(INIT_MAIN)], check=True)
        subprocess.run(["bash", "-n", str(ZCHECK)], check=True)
        subprocess.run(["bash", "-n", str(ZRESET_CONFIG)], check=True)
        subprocess.run(["sh", "-n", str(SUDO_SHIM)], check=True)
        subprocess.run(["python3", "-m", "py_compile", str(RECOVERY_PY)], check=True)
        subprocess.run(
            [str(RECOVERY_PY), "--help"], stdout=subprocess.DEVNULL,
            check=True)
        self.assertTrue(os.access(RECOVERY_SH, os.X_OK))
        self.assertTrue(os.access(INIT_MAIN, os.X_OK))
        self.assertTrue(os.access(ZRESET_CONFIG, os.X_OK))
        self.assertTrue(os.access(SUDO_SHIM, os.X_OK))

    def test_sudo_shim_routes_exact_power_actions_to_klipper(self):
        printer = self.root / "printer"
        printer.touch()
        shim = self._patched_script(
            SUDO_SHIM, "sudo-shim", {"/tmp/printer": str(printer)})
        fake_bin = self.root / "sudo-bin"
        fake_bin.mkdir()
        (fake_bin / "ps").write_text(
            "#!/bin/sh\n"
            "[ \"$SUDO_TEST_KLIPPY\" = 1 ] && "
            "printf '%s\\n' '1 root klippy.py'\n",
            encoding="utf-8")
        (fake_bin / "reboot").write_text(
            "#!/bin/sh\nprintf 'passthrough:%s\\n' \"$*\"\n",
            encoding="utf-8")
        for command in ("ps", "reboot"):
            (fake_bin / command).chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        environment["SUDO_TEST_KLIPPY"] = "1"

        reboot = subprocess.run(
            [str(shim), "reboot"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(reboot.returncode, 0, reboot.stdout)
        self.assertEqual(printer.read_text(encoding="utf-8"), "REBOOT\n")

        poweroff = subprocess.run(
            [str(shim), "poweroff"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(poweroff.returncode, 0, poweroff.stdout)
        self.assertEqual(printer.read_text(encoding="utf-8"), "SHUTDOWN\n")

        environment["SUDO_TEST_KLIPPY"] = "0"
        fallback = subprocess.run(
            [str(shim), "reboot"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(fallback.returncode, 0, fallback.stdout)
        self.assertEqual(fallback.stdout, "passthrough:\n")

        passthrough = subprocess.run(
            [str(shim), "reboot", "-f"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(passthrough.returncode, 0, passthrough.stdout)
        self.assertEqual(passthrough.stdout, "passthrough:-f\n")

    def test_root_start_replaces_existing_sudo_file_with_shim_link(self):
        installed_sudo = self.root / "sudo"
        installed_sudo.write_text("legacy sudo\n", encoding="utf-8")
        start = self._patched_script(
            ROOT_START, "root-start-test", {
                "[ -L /usr/bin/ip ] && rm -f /usr/bin/ip": ":",
                "[ -L /usr/bin/tc ] && rm -f /usr/bin/tc": ":",
                "ln -fns /opt/config/mod/.root/sudo-shim /usr/bin/sudo":
                    "ln -fns {} {}\nexit 0".format(
                        SUDO_SHIM, installed_sudo),
            })

        completed = subprocess.run(
            ["bash", str(start)], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)

        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertTrue(installed_sudo.is_symlink())
        self.assertEqual(installed_sudo.resolve(), SUDO_SHIM.resolve())

    def test_boot_mode_resolves_every_flag_combination(self):
        paths = self.boot_paths

        for hard in (False, True):
            for stock in (False, True):
                for soft in (False, True):
                    for ready in (False, True):
                        for path in paths.values():
                            path.unlink(missing_ok=True)
                        for enabled, name in (
                                (hard, "SKIP_MOD_HARD_F"),
                                (stock, "SKIP_MOD_F"),
                                (soft, "SKIP_MOD_SOFT_F"),
                                (ready, "INIT_FLAG")):
                            if enabled:
                                paths[name].touch()

                        result = subprocess.run(
                            ["sh", "-c", '. "$1"; forge_x_boot_mode',
                             "boot-mode-matrix", str(self.boot_mode_sh)],
                            text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            check=False)

                        expected = (
                            "stock-hard" if hard else
                            "stock" if stock else
                            "stock-soft" if soft else
                            "mod" if ready else
                            "incomplete"
                        )
                        self.assertEqual(
                            result.stdout.strip(), expected,
                            "hard={} stock={} soft={} ready={}".format(
                                hard, stock, soft, ready))

    def test_boot_mode_publication_normalizes_temporary_state(self):
        paths = self.boot_paths

        for mode, selected in (
                ("stock", "SKIP_MOD_F"),
                ("stock-soft", "SKIP_MOD_SOFT_F"),
                ("stock-hard", "SKIP_MOD_HARD_F")):
            with self.subTest(mode=mode):
                for path in paths.values():
                    path.touch()
                result = subprocess.run(
                    ["sh", "-c",
                     '. "$1"; forge_x_publish_stock_mode "$2"; forge_x_boot_mode',
                     "boot-mode-publish", str(self.boot_mode_sh), mode],
                    text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=False)

                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual(result.stdout.strip(), mode)
                for name, path in paths.items():
                    self.assertEqual(path.exists(), name == selected)

    def test_mod_readiness_cannot_replace_a_stock_mode(self):
        ready = self.boot_paths["INIT_FLAG"]
        hard = self.boot_paths["SKIP_MOD_HARD_F"]

        published = subprocess.run(
            ["sh", "-c",
             '. "$1"; forge_x_publish_mod_ready; forge_x_boot_mode',
             "boot-mode-ready", str(self.boot_mode_sh)],
            text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        self.assertEqual(published.returncode, 0, published.stdout)
        self.assertEqual(published.stdout.strip(), "mod")

        ready.unlink()
        hard.touch()
        rejected = subprocess.run(
            ["sh", "-c", '. "$1"; forge_x_publish_mod_ready',
             "boot-mode-ready", str(self.boot_mode_sh)],
            text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
        self.assertFalse(ready.exists())
        self.assertTrue(hard.exists())

    def test_release_catalog_includes_prerelease_and_renames_for_pro(self):
        releases = [
            {
                "tag_name": "2.0.0-beta.1",
                "prerelease": True,
                "draft": False,
                "assets": [{
                    "name": "Adventurer5M-ForgeX-2.0.0-beta.1.tgz",
                    "browser_download_url": "https://example.test/image",
                    "size": 123,
                }],
            },
            {
                "tag_name": "ignored",
                "draft": True,
                "assets": [{
                    "name": "Adventurer5M-ForgeX-ignored.tgz",
                    "browser_download_url": "https://example.test/draft",
                    "size": 123,
                }],
            },
        ]

        catalog = RECOVERY.forge_x_catalog("Adventurer5MPro", releases)

        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["version"], "2.0.0-beta.1 beta")
        self.assertEqual(
            catalog[0]["name"],
            "Adventurer5MPro-ForgeX-2.0.0-beta.1.tgz",
        )

    def test_static_pro_catalog_uses_pro_factory_but_shared_recovery_url(self):
        factory = RECOVERY.static_catalog(
            "Adventurer5MPro", RECOVERY.FACTORY_IMAGES)[0]
        recovery = RECOVERY.static_catalog(
            "Adventurer5MPro", RECOVERY.RECOVERY_IMAGES)[0]

        self.assertIn("Adventurer5MPro-2.7.8", factory["url"])
        self.assertEqual(factory["md5"], "5470a03d8dd7d5bc15140b0922b6e4fe")
        self.assertIn("Adventurer5M-3.x.x", recovery["url"])
        self.assertTrue(recovery["name"].startswith("Adventurer5MPro-"))

    def test_release_catalog_request_uses_vendor_curl_and_ca_bundle(self):
        curl = self.root / "curl"
        curl.touch(mode=0o755)
        cacert = self.root / "cacert.pem"
        cacert.touch()
        completed = subprocess.CompletedProcess(
            [], 0, stdout=b'[{"tag_name":"1.0"}]', stderr=b"")

        with mock.patch.object(RECOVERY, "CURL", str(curl)), \
                mock.patch.object(RECOVERY, "CURL_CACERT", str(cacert)), \
                mock.patch.object(
                    RECOVERY.subprocess, "run", return_value=completed) as run:
            releases = RECOVERY.request_json("https://example.test/releases")

        self.assertEqual(releases, [{"tag_name": "1.0"}])
        command = run.call_args.args[0]
        self.assertEqual(command[0], str(curl))
        self.assertEqual(command[1:3], ["--cacert", str(cacert)])
        self.assertIn("https://example.test/releases", command)

    def test_https_error_keeps_the_actionable_curl_line(self):
        stderr = (
            b"curl: (60) SSL certificate problem: certificate is not yet valid\n"
            b"More details here: https://curl.se/docs/sslcerts.html\n"
            b"HTTPS-proxy has similar options --proxy-cacert and --proxy-insecure.\n"
        )

        self.assertEqual(
            RECOVERY.curl_error(stderr),
            "curl: (60) SSL certificate problem: certificate is not yet valid",
        )

    def test_python_start_failure_publishes_no_action(self):
        action_file = self.root / "action"
        session = FakeTyperSession(start_error=RuntimeError("touch failed"))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(action_file), session)

        with self.assertRaisesRegex(RuntimeError, "touch failed"):
            ui.run()

        self.assertFalse(action_file.exists())

    def test_python_does_not_own_marker_and_persists_soft_action(self):
        marker = self.root / "recovery-failure"
        action_file = self.root / "action"
        marker.touch()
        session = FakeTyperSession((
            "1:main.boot", "2:boot.soft", "3:confirm.accept"))
        ui = RECOVERY.RecoveryUI("Adventurer5M", str(action_file), session)

        ui.run()

        self.assertTrue(marker.exists())
        self.assertEqual(action_file.read_text(encoding="utf-8"), "stock-soft\n")
        self.assertGreaterEqual(len(session.frames), 2)
        frames = ["\n".join(frame) for frame in session.frames]
        boot_menu = next(frame for frame in frames if '"STOCK ONLY"' in frame)
        confirmation = next(
            frame for frame in frames if '"START STOCK + SSH"' in frame)
        self.assertIn('"STOCK + SSH"', boot_menu)
        self.assertIn('"STOCK ONLY"', boot_menu)
        self.assertIn('"START STOCK + SSH"', confirmation)

    def test_recovery_frames_use_fonts_shipped_by_typer(self):
        manifest_path = (ROOT / ".py" / "klipper" / "plugins" / "ui" /
                         "font_metrics.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        supported = {entry["name"] for entry in manifest["fonts"]}
        session = FakeTyperSession((
            "2:confirm.cancel",
            "3:message.back",
            "6:choose.back",
        ))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)

        ui.render_main()
        ui.view.confirm("CONFIRM", "Detail", "CONTINUE")
        ui.view.message("MESSAGE", "Detail")
        ui.view.progress("PROGRESS", "Detail", 50)
        ui.view.verification_progress([], 1, 2, 0)
        ui.view.choose("CHOOSE", [{"name": "Entry"}], lambda item: item["name"])

        requested = set()
        for frame in session.frames:
            for command in frame:
                arguments = shlex.split(command)
                if "-f" in arguments:
                    requested.add(arguments[arguments.index("-f") + 1])

        self.assertTrue(requested)
        self.assertEqual(requested - supported, set())

    def test_main_header_keeps_model_visible_and_network_action_stable(self):
        session = FakeTyperSession()
        with mock.patch.dict(os.environ, {"RECOVERY_SSH_ACTIVE": "1"}):
            ui = RECOVERY.RecoveryUI(
                "Adventurer5MPro", str(self.root / "action"), session)
        ui.render_main()

        frame = session.frames[-1]
        model = next(command for command in frame
                     if '"Adventurer5MPro"' in command)
        network = next(command for command in frame
                       if "--id 1:main.network" in command)

        self.assertIn("-p 770 31", model)
        self.assertIn("-ha right -va middle", model)
        self.assertIn('"NETWORK / SSH"', network)
        self.assertNotIn("SSH ACTIVE", network)

    def test_recovery_button_tap_flashes_before_dispatch_without_new_hitbox(self):
        session = FakeTyperSession(("1:main.firmware",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.render_main()

        with mock.patch.object(RECOVERY.time, "sleep") as sleep:
            action = ui.view.wait_action()

        self.assertEqual(action, "main.firmware")
        sleep.assert_called_once_with(0.08)
        self.assertEqual(len(session.frames), 3)
        pressed, restored = session.frames[-2:]
        self.assertTrue(any("243b46" in command for command in pressed))
        self.assertTrue(any("0d222b" in command for command in restored))
        self.assertFalse(any("--id " in command for command in pressed + restored))

    def test_network_header_uses_two_centered_status_lines(self):
        session = FakeTyperSession(("1:network.back",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.network_status = mock.Mock(return_value={
            "mode": "WIFI", "state": "CONNECTED", "progress": "ONLINE",
            "ssid": "Workshop", "ip": "192.168.2.229",
        })

        ui.network_menu()

        frame = session.frames[0]
        state = next(command for command in frame if '"CONNECTED"' in command)
        detail = next(command for command in frame
                      if '"Workshop / 192.168.2.229"' in command)
        self.assertIn("-p 770 18", state)
        self.assertIn("-p 770 46", detail)
        self.assertIn("-va middle", state)
        self.assertIn("-va middle", detail)

    def test_shared_download_omits_the_default_http_port(self):
        session = FakeTyperSession(("1:share.stop",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.network_status = mock.Mock(return_value={"ip": "192.0.2.10"})
        ui.file_server.start_download = mock.Mock()
        ui.file_server.stop = mock.Mock()
        path = self.root / "backup.tar.gz"

        ui.share_download(str(path), "BACKUP READY")

        frame = "\n".join(session.frames[0])
        self.assertIn("http://192.0.2.10/backup.tar.gz", frame)
        self.assertNotIn("192.0.2.10:80", frame)
        buttons = [
            command for command in session.frames[0]
            if command.startswith("--batch button") and "--id " in command]
        self.assertEqual(len(buttons), 1)
        self.assertIn('"STOP SHARING"', buttons[0])
        self.assertIn("--id 1:share.stop", buttons[0])
        self.assertNotIn("share.back", frame)
        ui.file_server.start_download.assert_called_once_with(str(path))
        ui.file_server.stop.assert_called_once_with()

    def test_shared_download_keeps_a_nondefault_http_port(self):
        session = FakeTyperSession(("1:share.stop",))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)
        ui.network_status = mock.Mock(return_value={"ip": "192.0.2.10"})
        ui.file_server = mock.Mock(port=8080)
        path = self.root / "debug.tar.gz"

        ui.share_download(str(path), "DIAGNOSTICS READY")

        frame = "\n".join(session.frames[0])
        self.assertIn("http://192.0.2.10:8080/debug.tar.gz", frame)

    def test_network_connection_observes_netd_until_requested_mode_is_online(self):
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.netd_send = mock.Mock()
        ui.view.network_progress = mock.Mock()
        ui.view.message = mock.Mock()
        ui.network_status = mock.Mock(side_effect=[
            {"mode": "ETHERNET", "state": "CONNECTED", "ip": "192.168.2.124"},
            {"mode": "WIFI", "state": "CONNECTING", "progress": "PREPARING",
             "ssid": "Workshop"},
            {"mode": "WIFI", "state": "CONNECTING", "progress": "HANDSHAKE",
             "attempt": "2/5", "ssid": "Workshop"},
            {"mode": "WIFI", "state": "CONNECTED", "progress": "ONLINE",
             "ssid": "Workshop", "ip": "192.168.2.229"},
        ])

        with mock.patch.object(
                RECOVERY.time, "monotonic", side_effect=(0, 1, 2, 3, 4)), \
                mock.patch.object(RECOVERY.time, "sleep"):
            ui.connect_network("CONNECT_WIFI ssid=encoded", "Workshop")

        ui.netd_send.assert_called_once_with("CONNECT_WIFI ssid=encoded", 5.0)
        self.assertEqual(ui.network_status.call_count, 4)
        observed = [call.args[1].get("progress")
                    for call in ui.view.network_progress.call_args_list]
        self.assertIn("PREPARING", observed)
        self.assertIn("HANDSHAKE", observed)
        ui.view.message.assert_called_once_with(
            "NETWORK CONNECTED", "Workshop / 192.168.2.229")

    def test_network_progress_shows_stage_and_attempt_without_commentary(self):
        session = FakeTyperSession()
        view = RECOVERY.RecoveryView(session)

        view.network_progress("Workshop", {
            "mode": "WIFI", "state": "CONNECTING", "progress": "HANDSHAKE",
            "attempt": "2/5", "ssid": "Workshop",
        })

        frame = session.frames[-1]
        title = next(command for command in frame
                     if '"NETWORK CONNECTION"' in command)
        self.assertTrue(any('"AUTHENTICATING"' in command for command in frame))
        self.assertTrue(any('"NETWORK: Workshop"' in command for command in frame))
        self.assertTrue(any('"ATTEMPT: 2 / 5"' in command for command in frame))
        self.assertFalse(any("secure Wi-Fi handshake" in command for command in frame))
        self.assertIn('-f "JetBrainsMono Bold 12pt"', title)

    def test_verification_progress_shows_counts_and_red_errors(self):
        session = FakeTyperSession()
        view = RECOVERY.RecoveryView(session)

        view.verification_progress([
            ("Scanning files...", False),
            ("Checksum FAILED: /sbin/tool", True),
        ], checked=17, total=50, failures=1)

        frame = session.frames[-1]
        stats = next(command for command in frame if "CHECKED:" in command)
        failure = next(command for command in frame if "Checksum FAILED" in command)
        self.assertIn("CHECKED: 17 / 50   ERRORS: 1", stats)
        self.assertIn("-c ff6b6b", stats)
        self.assertIn("-c ff6b6b", failure)

    def test_pages_without_status_give_long_titles_the_full_header_width(self):
        session = FakeTyperSession()
        view = RECOVERY.RecoveryView(session)

        view.verification_progress([], checked=0, total=0, failures=0)
        view.progress("DOWNLOADING IMAGE", "Connecting...")

        for title, frame in zip(
                ("CHECKING SYSTEM FILES", "DOWNLOADING IMAGE"),
                session.frames):
            command = next(
                item for item in frame if RECOVERY.quote(title) in item)
            self.assertIn("--max-width 740", command)

    def test_verification_logs_every_line_and_renders_at_most_once_per_second(self):
        verification_log = self.root / "verification.log"
        lines = [
            "// Processed 1 / 5; errors: 0.\n",
            "// Processed 2 / 5; errors: 0.\n",
            "// Processed 3 / 5; errors: 0.\n",
            "// Processed 4 / 5; errors: 0.\n",
            "// Processed 5 / 5; errors: 0.\n",
        ]
        process = mock.Mock()
        process.stdout = io.StringIO("".join(lines))
        process.poll.return_value = 0
        process.wait.return_value = 0
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.view.verification_progress = mock.Mock()
        ui.view.message = mock.Mock()

        with mock.patch.object(
                RECOVERY, "VERIFICATION_LOG", str(verification_log)), \
                mock.patch.object(RECOVERY.subprocess, "Popen",
                                  return_value=process), \
                mock.patch.object(
                    RECOVERY.time, "monotonic",
                    side_effect=(0.0, 0.1, 0.4, 1.0, 1.2, 1.9, 1.9)), \
                mock.patch.object(RECOVERY.time, "sleep") as sleep:
            ui.verify_system()

        self.assertEqual(
            verification_log.read_text(encoding="utf-8").splitlines(),
            [line.strip() for line in lines])
        self.assertEqual(
            [call.args[1]
             for call in ui.view.verification_progress.call_args_list],
            [0, 3, 5])
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args.args[0], 0.1)
        ui.view.message.assert_called_once_with(
            "SYSTEM FILES OK", "Processed 5 / 5; errors: 0.")

    def _fake_curl(self, payload):
        source = self.root / "curl-payload"
        source.write_bytes(payload)
        cacert = self.root / "cacert.pem"
        cacert.touch()
        curl = self.root / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            "output=\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = --output ]; then shift; output=$1; fi\n"
            "  shift\n"
            "done\n"
            "cp \"$CURL_TEST_SOURCE\" \"$output\"\n",
            encoding="utf-8")
        curl.chmod(0o755)
        return curl, cacert, source

    def test_download_is_atomic_and_validates_published_md5(self):
        payload = firmware_archive_bytes()
        curl, cacert, source = self._fake_curl(payload)
        download_dir = self.root / "downloads"
        action_file = self.root / "action"
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(action_file), session=mock.Mock())
        ui.view.progress = mock.Mock()
        entry = {
            "name": "Adventurer5M-test.tgz",
            "url": "https://example.test/image",
            "size": len(payload),
            "md5": hashlib.md5(payload).hexdigest(),
        }

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)), \
                mock.patch.object(RECOVERY, "CURL", str(curl)), \
                mock.patch.object(RECOVERY, "CURL_CACERT", str(cacert)), \
                mock.patch.dict(os.environ, {"CURL_TEST_SOURCE": str(source)}):
            path = pathlib.Path(ui.download(entry))

        self.assertEqual(path.read_bytes(), payload)
        self.assertFalse(path.with_name(path.name + ".part").exists())
        ui.view.progress.assert_any_call(
            "DOWNLOADING IMAGE", "Connecting to the download server...")

    def test_download_removes_partial_file_after_checksum_failure(self):
        payload = firmware_archive_bytes()
        curl, cacert, source = self._fake_curl(payload)
        download_dir = self.root / "downloads"
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session=mock.Mock())
        ui.view.progress = mock.Mock()
        entry = {
            "name": "Adventurer5M-test.tgz",
            "url": "https://example.test/image",
            "size": len(payload),
            "md5": "0" * 32,
        }

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)), \
                mock.patch.object(RECOVERY, "CURL", str(curl)), \
                mock.patch.object(RECOVERY, "CURL_CACERT", str(cacert)), \
                mock.patch.dict(os.environ, {"CURL_TEST_SOURCE": str(source)}):
            with self.assertRaisesRegex(RuntimeError, "MD5"):
                ui.download(entry)

        self.assertFalse((download_dir / entry["name"]).exists())
        self.assertFalse((download_dir / (entry["name"] + ".part")).exists())

    def test_download_failure_stops_the_writer_before_removing_partial_file(self):
        download_dir = self.root / "downloads"
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session=mock.Mock())
        ui.view.progress = mock.Mock()
        entry = {
            "name": "Adventurer5M-test.tgz",
            "url": "https://example.test/image",
        }
        process = mock.Mock()
        process.poll.side_effect = (None, None)
        process.stderr = io.BytesIO()
        process.wait.return_value = 0

        def start_writer(*args, **kwargs):
            del args, kwargs
            download_dir.mkdir(exist_ok=True)
            (download_dir / (entry["name"] + ".part")).write_bytes(b"x")
            return process

        usage = mock.Mock(free=RECOVERY.RESERVE_BYTES)
        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)), \
                mock.patch.object(RECOVERY, "curl_command", return_value=["curl"]), \
                mock.patch.object(RECOVERY.shutil, "disk_usage", return_value=usage), \
                mock.patch.object(
                    RECOVERY.subprocess, "Popen", side_effect=start_writer):
            with self.assertRaisesRegex(RuntimeError, "Not enough /data space"):
                ui.download(entry)

        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=1.0)
        self.assertTrue(process.stderr.closed)
        self.assertFalse(
            (download_dir / (entry["name"] + ".part")).exists())

    def test_flash_handoff_is_rendered_by_recovery_before_services_stop(self):
        action = self.root / "action"
        image = self.root / "Adventurer5M-test.tgz"
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(action), session=mock.Mock())
        ui.view.confirm = mock.Mock(side_effect=(True, True))
        ui.view.progress = mock.Mock()

        with self.assertRaises(SystemExit):
            ui.offer_flash(str(image))

        ui.view.progress.assert_called_once_with(
            "PREPARING FIRMWARE", "Stopping recovery services...")
        self.assertEqual(action.read_text(encoding="utf-8"),
                         "flash:{}\n".format(image))

    def test_download_selection_immediately_shows_preparation(self):
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        entry = {"name": "Adventurer5M-test.tgz", "url": "https://example.test"}
        events = []
        ui.view.progress = lambda *args: events.append(("progress", args))
        ui.download = lambda selected: events.append(("download", selected)) or "/image"
        ui.offer_flash = lambda path: events.append(("offer", path))

        ui.download_and_offer(entry)

        self.assertEqual(events[0], (
            "progress", ("PREPARING IMAGE", "Checking local image and download...")))
        self.assertEqual(events[1], ("download", entry))
        self.assertEqual(events[2], ("offer", "/image"))

    def test_diagnostics_uses_a_stable_download_name(self):
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.view.progress = mock.Mock()
        ui.share_download = mock.Mock()
        destination = self.root / "debug.tar.gz"

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(self.root)), \
                mock.patch.object(RECOVERY, "_run_recovery_archive") as run:
            ui.create_diagnostics()

        run.assert_called_once_with(
            "--tar-debug-to", str(destination), 90)
        ui.share_download.assert_called_once_with(
            str(destination), "DIAGNOSTICS READY")

    def test_firmware_menu_moves_cleanup_under_downloaded_images(self):
        session = FakeTyperSession((
            "1:firmware.downloaded", "2:downloaded.back", "3:firmware.back"))
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), session)

        ui.firmware_menu()

        firmware = session.frames[0]
        downloaded = session.frames[3]
        self.assertFalse(any("firmware.clear" in command for command in firmware))
        self.assertTrue(any(
            "--id 2:downloaded.clear" in command
            and '"CLEAR DOWNLOADS"' in command
            for command in downloaded))

    def test_bulk_cleanup_requires_confirmation_and_deletes_regular_files(self):
        download_dir = self.root / "downloads"
        download_dir.mkdir()
        image = download_dir / "Adventurer5M-test.tgz"
        partial = download_dir / "Adventurer5M-unfinished.tgz.part"
        image.touch()
        partial.touch()
        (download_dir / "keep-directory").mkdir()
        symlink = download_dir / "keep-symlink"
        symlink.symlink_to(image)
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.view.confirm = mock.Mock(return_value=True)
        ui.view.message = mock.Mock()

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)):
            ui.clear_downloaded_files()

        ui.view.confirm.assert_called_once_with(
            "CLEAR DOWNLOADS",
            "Delete all 2 downloaded firmware files?",
            "CLEAR DOWNLOADS",
        )
        self.assertFalse(image.exists())
        self.assertFalse(partial.exists())
        self.assertTrue((download_dir / "keep-directory").is_dir())
        self.assertTrue(symlink.is_symlink())
        ui.view.message.assert_called_once_with(
            "DOWNLOADS CLEARED", "Deleted 2 downloaded files.")

    def test_bulk_cleanup_cancel_preserves_downloads(self):
        download_dir = self.root / "downloads"
        download_dir.mkdir()
        image = download_dir / "Adventurer5M-test.tgz"
        image.touch()
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.view.confirm = mock.Mock(return_value=False)
        ui.view.message = mock.Mock()

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)):
            ui.clear_downloaded_files()

        self.assertTrue(image.exists())
        ui.view.message.assert_not_called()

    def test_bulk_cleanup_reports_empty_and_partial_results(self):
        download_dir = self.root / "downloads"
        ui = RECOVERY.RecoveryUI(
            "Adventurer5M", str(self.root / "action"), mock.Mock())
        ui.view.confirm = mock.Mock(return_value=True)
        ui.view.message = mock.Mock()

        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)):
            ui.clear_downloaded_files()

        ui.view.confirm.assert_not_called()
        ui.view.message.assert_called_once_with(
            "STORAGE IS EMPTY", "There are no downloaded files to delete.")

        download_dir.mkdir()
        removable = download_dir / "Adventurer5M-removable.tgz"
        blocked = download_dir / "Adventurer5M-blocked.tgz"
        removable.touch()
        blocked.touch()
        original_unlink = os.unlink

        def unlink_with_failure(path):
            if os.path.basename(path) == blocked.name:
                raise PermissionError("read-only")
            original_unlink(path)

        ui.view.confirm.reset_mock()
        ui.view.message.reset_mock()
        with mock.patch.object(RECOVERY, "DOWNLOAD_DIR", str(download_dir)), \
                mock.patch.object(RECOVERY.os, "unlink",
                                  side_effect=unlink_with_failure):
            ui.clear_downloaded_files()

        self.assertFalse(removable.exists())
        self.assertTrue(blocked.exists())
        ui.view.message.assert_called_once_with(
            "CLEANUP INCOMPLETE",
            "Deleted 1 of 2 files. 1 file could not be removed.",
        )

    def test_tar_xz_validation_streams_through_external_xz(self):
        archive_path = self.root / "Adventurer5M-test.tar.xz.part"
        payload = b"#!/bin/sh\nexit 0\n"
        with tarfile.open(archive_path, mode="w:xz") as archive:
            member = tarfile.TarInfo("forge-x-init.sh")
            member.size = len(payload)
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(payload))

        xz = shutil.which("xz")
        self.assertIsNotNone(xz)
        with mock.patch.object(RECOVERY, "XZ", xz):
            RECOVERY.validate_archive(archive_path)

    def _checksum_archive(self, expected, relative_path):
        archive = self.root / "checksums.tar.gz"
        payload = "{}  ./{}\n".format(expected, relative_path).encode("utf-8")
        with tarfile.open(archive, mode="w:gz") as output:
            member = tarfile.TarInfo("md5sum.list")
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
        return archive

    def _run_check(self, expected, relative_path):
        archive = self._checksum_archive(expected, relative_path)
        script = self._patched_script(
            ZCHECK, "zcheck-test.sh", {
                "/opt/config/mod/.shell/common.sh": "/dev/null",
                "CHECKSUM_ARCHIVE=/opt/config/mod/md5sum.tar.gz":
                    "CHECKSUM_ARCHIVE=%s" % archive,
                "CHECKSUM_TMP_DIR=/data/.tmp":
                    "CHECKSUM_TMP_DIR=%s" % (self.root / "checksum-tmp"),
                "CHECKSUM_ROOT=/":
                    "CHECKSUM_ROOT=%s" % (self.root / "system"),
            })
        return subprocess.run(
            ["bash", str(script), "verify-plain"],
            text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, check=False)

    def test_checksum_contract_reports_success_and_corruption(self):
        system_file = self.root / "system" / "sbin" / "tool"
        system_file.parent.mkdir(parents=True)
        system_file.write_bytes(b"intact")
        expected = hashlib.md5(b"intact").hexdigest()

        passed = self._run_check(expected, "sbin/tool")
        system_file.write_bytes(b"changed")
        failed = self._run_check(expected, "sbin/tool")

        self.assertEqual(passed.returncode, 0, passed.stdout)
        self.assertIn("Processed 1 / 1; errors: 0", passed.stdout)
        self.assertIn("Verification passed: 1 files checked", passed.stdout)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn("Processed 1 / 1; errors: 1", failed.stdout)
        self.assertIn("Verification failed: 1 of 1", failed.stdout)

    def test_checksum_uses_stock_backup_for_managed_klipper_patch(self):
        relative = "opt/klipper/klippy/mcu.py"
        active = self.root / "system" / relative
        active.parent.mkdir(parents=True)
        active.symlink_to("/opt/config/mod/.py/klipper/patches/mcu.py")
        backup = active.with_name(active.name + ".bak")
        backup.write_bytes(b"stock klipper module")
        expected = hashlib.md5(b"stock klipper module").hexdigest()

        passed = self._run_check(expected, relative)
        backup.write_bytes(b"corrupted stock backup")
        failed = self._run_check(expected, relative)
        backup.unlink()
        missing = self._run_check(expected, relative)

        self.assertEqual(passed.returncode, 0, passed.stdout)
        self.assertIn("Verification passed: 1 files checked", passed.stdout)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertIn(str(backup), failed.stdout)
        self.assertNotEqual(missing.returncode, 0, missing.stdout)
        self.assertIn("File {} missing".format(backup), missing.stdout)

    def test_checksum_does_not_trust_backup_for_unmanaged_klipper_link(self):
        relative = "opt/klipper/klippy/mcu.py"
        active = self.root / "system" / relative
        active.parent.mkdir(parents=True)
        active.symlink_to("/unmanaged/mcu.py")
        backup = active.with_name(active.name + ".bak")
        backup.write_bytes(b"stock klipper module")
        expected = hashlib.md5(b"stock klipper module").hexdigest()

        result = self._run_check(expected, relative)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(str(active), result.stdout)
        self.assertNotIn(str(backup) + " missing", result.stdout)

    def test_recovery_mounts_and_releases_devpts_only_when_needed(self):
        cases = (
            ("new", False, False),
            ("existing", True, False),
            ("ssh-active", False, True),
        )
        for name, preexisting, ssh_active in cases:
            with self.subTest(name=name):
                run_root = self.root / ("devpts-" + name)
                run_root.mkdir()
                lifecycle = run_root / "lifecycle"
                existing = "1" if preexisting else "0"
                active = "1" if ssh_active else "0"
                command = r'''
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_ACTION="$2/action"
LIFECYCLE="$2/lifecycle"
DEVPTS_EXISTING="$3"
RECOVERY_SSH_ACTIVE="$4"
mount() {
    if [ "$#" -eq 0 ]; then
        [ "$DEVPTS_EXISTING" = 1 ] && echo 'devpts on /dev/pts type devpts (rw)'
        return 0
    fi
    printf 'mount:%s\n' "$*" >> "$LIFECYCLE"
}
umount() { printf 'umount:%s\n' "$*" >> "$LIFECYCLE"; }
mkdir() { :; }
mount_recovery_devpts
cleanup_recovery_runtime
'''
                result = subprocess.run(
                    ["bash", "-c", command, "devpts-harness",
                     str(self.boot_scripts["recovery.sh"]), str(run_root),
                     existing, active],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=False)
                events = lifecycle.read_text(encoding="utf-8") \
                    if lifecycle.exists() else ""

                self.assertEqual(result.returncode, 0, result.stdout)
                if preexisting:
                    self.assertEqual(events, "")
                else:
                    self.assertIn("mount:-t devpts devpts /dev/pts", events)
                    self.assertEqual("umount:/dev/pts" in events, not ssh_active)

    def test_recovery_clock_load_start_and_owned_stop_share_one_lifecycle(self):
        lifecycle = self.root / "clock-lifecycle"
        command = r'''
source "$1"
trap - EXIT HUP INT TERM
MOD="$2/mod"
RECOVERY_ACTION="$2/action"
LIFECYCLE="$2/clock-lifecycle"
chroot() { printf '%s\n' "$*" >> "$LIFECYCLE"; }
start_recovery_clock
cleanup_recovery_runtime
'''
        result = subprocess.run(
            ["bash", "-c", command, "clock-harness",
             str(self.boot_scripts["recovery.sh"]), str(self.root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        events = lifecycle.read_text(encoding="utf-8").splitlines()
        self.assertEqual(events, [
            "{}/mod /usr/sbin/fake-hwclock load".format(self.root),
            "{}/mod /opt/config/mod/.root/S45ntpd start".format(self.root),
            "{}/mod /opt/config/mod/.root/S45ntpd stop".format(self.root),
        ])

    def test_recovery_log_rotates_once_and_captures_runtime_failure(self):
        run_root = self.root / "recovery-log"
        run_root.mkdir()
        recovery_log = run_root / "recovery.log"
        recovery_log.write_text("previous attempt\n", encoding="utf-8")
        recovery_log.with_name("recovery.log.1").write_text(
            "older attempt\n", encoding="utf-8")
        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_LOG="$2/recovery.log"
RECOVERY_ACTION="$2/action"
mount_data_partition() { :; }
run_recovery_lifecycle() {
    echo '// Recovery UI started.'
    echo 'Recovery failed: resource temporarily unavailable' >&2
    return 7
}
main
status=$?
printf 'result=%s\n' "$status"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-log-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, "result=7\n")
        self.assertEqual(
            recovery_log.read_text(encoding="utf-8").splitlines(), [
                "// Recovery UI started.",
                "Recovery failed: resource temporarily unavailable",
            ])
        self.assertEqual(
            recovery_log.with_name("recovery.log.1").read_text(
                encoding="utf-8"),
            "previous attempt\n")
        self.assertEqual(
            recovery_log.with_name("recovery.log.2").read_text(
                encoding="utf-8"),
            "older attempt\n")

    def test_recovery_log_failure_does_not_replace_lifecycle_result(self):
        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_ACTION="$2/action"
mount_data_partition() { :; }
prepare_recovery_log() { return 1; }
run_recovery_lifecycle() {
    echo '// Recovery continued without its log.'
    return 6
}
main
status=$?
printf 'result=%s\n' "$status"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-log-failure-harness",
             str(self.boot_scripts["recovery.sh"]), str(self.root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("Unable to prepare the persistent recovery log",
                      result.stdout)
        self.assertIn("Recovery continued without its log", result.stdout)
        self.assertIn("result=6", result.stdout)

    def _recovery_wrapper_harness(self, action):
        run_root = self.root / action.replace(":", "-")
        run_root.mkdir()

        marker_path = run_root / "recovery-failure"
        action_path = run_root / "recovery-action"
        legacy_path = run_root / "skip-legacy"
        soft_path = run_root / "skip-soft"
        hard_path = run_root / "skip-hard"
        mod_path = run_root / "mod"
        scripts_path = run_root / "scripts"
        (mod_path / "bin").mkdir(parents=True)
        (mod_path / "bin" / "python3").touch(mode=0o755)
        scripts_path.mkdir()
        (scripts_path / "screen.sh").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
        (scripts_path / "screen.sh").chmod(0o755)
        marker_path.touch()
        legacy_path.touch()
        if action == "stock-soft":
            hard_path.touch()
        elif action == "stock-hard":
            soft_path.touch()

        command = r'''
source "$1"
RECOVERY_ACTION="$3"
SKIP_MOD_F="${2%/*}/skip-legacy"
SKIP_MOD_SOFT_F="$4"
SKIP_MOD_HARD_F="$5"
MOD="$6"
MOD_DATA="$6/data"
SCRIPTS="$7"
TEST_ACTION="$8"
mount_data_partition() { :; }
find_stock_python() { printf '%s\n' "$MOD/bin/python3"; }
load_stock_printer_identity() { :; }
mount_recovery_devpts() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() {
    printf '%s\n' "$TEST_ACTION" > "$RECOVERY_ACTION"
}
main
printf 'result=%s\n' "$?"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-wrapper-harness",
             str(self.boot_scripts["recovery.sh"]),
             str(marker_path), str(action_path), str(soft_path),
             str(hard_path), str(mod_path), str(scripts_path), action],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        return result, marker_path, action_path, soft_path, hard_path

    def test_recovery_wrapper_owns_stock_bypass_actions(self):
        for action, expected_soft, expected_hard in (
                ("stock-soft", True, False),
                ("stock-hard", False, True)):
            with self.subTest(action=action):
                result, marker, action_file, soft, hard = \
                    self._recovery_wrapper_harness(action)

                self.assertIn("result=0", result.stdout)
                self.assertTrue(marker.exists())
                self.assertFalse(action_file.exists())
                self.assertFalse((marker.parent / "skip-legacy").exists())
                self.assertEqual(soft.exists(), expected_soft)
                self.assertEqual(hard.exists(), expected_hard)

    def test_recovery_reboot_disarms_recovery_and_starts_normal_boot_next(self):
        run_root = self.root / "normal-reboot"
        run_root.mkdir()
        for name in (
                "boot-skip", "recovery-failure", "boot-failure",
                "skip-legacy", "skip-soft", "skip-hard", "ready"):
            (run_root / name).touch()
        scripts = run_root / "scripts"
        scripts.mkdir()
        screen = scripts / "screen.sh"
        screen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        screen.chmod(0o755)

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RUN_ROOT="$2"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_FLAG="$2/ready"
RECOVERY_ACTION="$2/action"
SCRIPTS="$2/scripts"
FIRMWARE_MACHINE=Adventurer5M
mount_data_partition() { :; }
find_stock_python() { echo /bin/true; }
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { :; }
mount_recovery_devpts() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() { echo reboot > "$RECOVERY_ACTION"; }
cleanup_recovery_runtime() { echo cleanup >> "$RUN_ROOT/lifecycle"; }
sync() { echo sync >> "$RUN_ROOT/lifecycle"; }
reboot() { echo "$*" > "$RUN_ROOT/rebooted"; }
main
printf 'result=%s\n' "$?"
'''
        result = subprocess.run(
            ["bash", "-c", command, "normal-reboot-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("result=1", result.stdout)
        self.assertEqual((run_root / "rebooted").read_text(encoding="utf-8"), "-f\n")
        self.assertFalse(any(
            (run_root / name).exists() for name in (
                "boot-skip", "recovery-failure", "boot-failure",
                "skip-legacy", "skip-soft", "skip-hard", "ready")))

    def test_recovery_uninstall_handoff_cleans_runtime_before_canonical_script(self):
        run_root = self.root / "uninstall-handoff"
        run_root.mkdir()
        scripts = run_root / "scripts"
        scripts.mkdir()
        screen = scripts / "screen.sh"
        screen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        screen.chmod(0o755)
        uninstall = run_root / "uninstall.sh"
        uninstall.write_text(
            "#!/bin/bash\n"
            "[ -f \"$TEST_RECOVERY_CLEANED\" ] || exit 9\n"
            "printf 'uninstall\\n' > \"$TEST_UNINSTALL_MARKER\"\n",
            encoding="utf-8")
        uninstall.chmod(0o755)

        command = r"""
source "$1"
trap - EXIT HUP INT TERM
RUN_ROOT="$2"
RECOVERY_ACTION="$2/action"
RECOVERY_UNINSTALL="$2/uninstall.sh"
SCRIPTS="$2/scripts"
MOD="$2/mod"
MOD_DATA="$2/mod-data"
FIRMWARE_MACHINE=Adventurer5M
TEST_RECOVERY_CLEANED="$2/cleaned"
TEST_UNINSTALL_MARKER="$2/uninstalled"
export TEST_RECOVERY_CLEANED TEST_UNINSTALL_MARKER
find_stock_python() { echo /bin/true; }
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { :; }
mount_recovery_devpts() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() { echo uninstall > "$RECOVERY_ACTION"; }
stop_recovery_ssh() { echo ssh-stopped >> "$RUN_ROOT/lifecycle"; }
cleanup_recovery_runtime() {
    echo cleanup >> "$RUN_ROOT/lifecycle"
    touch "$TEST_RECOVERY_CLEANED"
    rm -f "$RECOVERY_ACTION"
}
sync() { echo sync >> "$RUN_ROOT/lifecycle"; }
run_recovery_lifecycle
status=$?
rm -f /tmp/forge-x-uninstall.sh
printf 'result=%s\\n' "$status"
"""
        result = subprocess.run(
            ["bash", "-c", command, "recovery-uninstall-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("result=0", result.stdout)
        self.assertEqual(
            (run_root / "lifecycle").read_text(encoding="utf-8").splitlines(),
            ["ssh-stopped", "cleanup", "sync"])
        self.assertEqual(
            (run_root / "uninstalled").read_text(encoding="utf-8"),
            "uninstall\n")
        self.assertFalse((run_root / "action").exists())

    def test_recovery_firmware_handoff_clears_screen_after_runtime_cleanup(self):
        run_root = self.root / "firmware-handoff"
        run_root.mkdir()
        image = run_root / "Adventurer5M-test.tgz"
        image.touch()

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RUN_ROOT="$2"
RECOVERY_ACTION="$2/action"
FIRMWARE_MACHINE=Adventurer5M
find_stock_python() { echo /bin/true; }
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { :; }
mount_recovery_devpts() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() { echo "flash:$RUN_ROOT/Adventurer5M-test.tgz" > "$RECOVERY_ACTION"; }
valid_firmware_path() { return 0; }
stop_recovery_ssh() { echo ssh-stopped >> "$RUN_ROOT/lifecycle"; }
cleanup_recovery_runtime() {
    echo cleanup >> "$RUN_ROOT/lifecycle"
    rm -f "$RECOVERY_ACTION"
}
screen_typer() { echo "screen:$*" >> "$RUN_ROOT/lifecycle"; }
sync() { echo sync >> "$RUN_ROOT/lifecycle"; }
run_firmware_installer() {
    echo "installer:$1" >> "$RUN_ROOT/lifecycle"
    return 0
}
run_recovery_lifecycle
printf 'result=%s\n' "$?"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-firmware-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("result=0", result.stdout)
        self.assertEqual(
            (run_root / "lifecycle").read_text(encoding="utf-8").splitlines(),
            [
                "ssh-stopped",
                "cleanup",
                "screen:fill -p 0 0 -s 800 480 -c 0",
                "sync",
                "installer:{}".format(image),
            ],
        )
        self.assertFalse((run_root / "action").exists())

    def test_recovery_reset_uses_detected_model_and_returns_with_notice(self):
        run_root = self.root / "reset-handoff"
        run_root.mkdir()
        reset = run_root / "reset.sh"
        reset.write_text(
            "#!/bin/bash\n"
            "printf '%s\\n' \"$1\" > \"$TEST_RESET_MODEL\"\n",
            encoding="utf-8")
        reset.chmod(0o755)

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RUN_ROOT="$2"
RECOVERY_ACTION="$2/action"
RECOVERY_RESET_CONFIG="$2/reset.sh"
FIRMWARE_MACHINE=Adventurer5MPro
TEST_RESET_MODEL="$2/reset-model"
export TEST_RESET_MODEL
find_stock_python() { echo /bin/true; }
find_vendor_curl() { return 1; }
find_vendor_cacert() { return 1; }
load_stock_printer_identity() { :; }
mount_recovery_devpts() { :; }
mount_recovery_chroot() { :; }
start_recovery_touch() { :; }
start_recovery_netd() { :; }
start_recovery_clock() { :; }
run_recovery_ui() {
    if [ ! -f "$RUN_ROOT/ui-ran" ]; then
        touch "$RUN_ROOT/ui-ran"
        echo reset-config > "$RECOVERY_ACTION"
    else
        printf '%s\n' "$RECOVERY_NOTICE" > "$RUN_ROOT/notice"
        echo stock-soft > "$RECOVERY_ACTION"
    fi
}
forge_x_publish_stock_mode() { :; }
run_recovery_lifecycle
printf 'result=%s\n' "$?"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-reset-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("result=0", result.stdout)
        self.assertEqual(
            (run_root / "reset-model").read_text(encoding="utf-8"),
            "Adventurer5MPro\n")
        self.assertIn(
            "Configuration reset complete",
            (run_root / "notice").read_text(encoding="utf-8"))

    def test_recovery_wrapper_rejects_unknown_action(self):
        result, _marker, action_file, soft, hard = \
            self._recovery_wrapper_harness("unknown")

        self.assertIn("result=1", result.stdout)
        self.assertFalse(action_file.exists())
        self.assertFalse(soft.exists())
        self.assertFalse(hard.exists())

    def test_recovery_ui_runs_with_the_selected_stock_python(self):
        run_root = self.root / "stock-python"
        run_root.mkdir()
        python = run_root / "python3.7"
        calls = run_root / "calls"
        action = run_root / "action"
        python.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$RECOVERY_TEST_CALLS\"\n",
            encoding="utf-8")
        python.chmod(0o755)

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_PYTHON="$2"
RECOVERY_ACTION="$3"
FIRMWARE_MACHINE=Adventurer5M
RECOVERY_TEST_CALLS="$4"
export RECOVERY_TEST_CALLS
run_recovery_ui
'''
        result = subprocess.run(
            ["bash", "-c", command, "stock-python-harness",
             str(self.boot_scripts["recovery.sh"]), str(python), str(action),
             str(calls)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        arguments = calls.read_text(encoding="utf-8").split()
        self.assertEqual(
            arguments[0:2], ["-u", "/opt/config/mod/.py/recovery.py"])
        self.assertEqual(arguments[2:4], ["--machine", "Adventurer5M"])
        self.assertEqual(arguments[4:6], ["--action-file", str(action)])

    def test_recovery_firmware_handoff_routes_progress_to_screen_logger(self):
        run_root = self.root / "firmware-screen-logger"
        run_root.mkdir()
        installer = run_root / "install-image.sh"
        installer.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" > \"$RECOVERY_TEST_INSTALLER_ARGS\"\n"
            "echo '// Preparing firmware image'\n"
            "echo '//% Extracting firmware: 55%'\n"
            "exit 7\n",
            encoding="utf-8")
        installer.chmod(0o755)
        recovery_log = run_root / "recovery.log"
        recovery_log.touch()
        command = r'''
source "$1"
trap - EXIT HUP INT TERM
RECOVERY_INSTALL_IMAGE="$2/install-image.sh"
RECOVERY_LOG="$2/recovery.log"
RECOVERY_TEST_INSTALLER_ARGS="$2/installer-args"
export RECOVERY_TEST_INSTALLER_ARGS
LOGGED_ARGS="$2/logged-args"
LOGGED_INPUT="$2/logged-input"
logged() {
    printf '%s\n' "$*" > "$LOGGED_ARGS"
    cat > "$LOGGED_INPUT"
}
run_firmware_installer "$2/Adventurer5M-test.tgz"
status=$?
printf 'result=%s\n' "$status"
'''
        result = subprocess.run(
            ["bash", "-c", command, "firmware-screen-logger-harness",
             str(self.boot_scripts["recovery.sh"]), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.stdout, "result=7\n")
        self.assertEqual(
            (run_root / "installer-args").read_text(encoding="utf-8").strip(),
            str(run_root / "Adventurer5M-test.tgz"),
        )
        self.assertEqual(
            (run_root / "logged-args").read_text(encoding="utf-8").split(),
            [str(recovery_log), "--send-to-screen", "--screen-no-followup"])
        self.assertEqual(
            (run_root / "logged-input").read_text(
                encoding="utf-8").splitlines(), [
                    "// Preparing firmware image",
                    "//% Extracting firmware: 55%",
                ])

    def test_partial_chroot_setup_unmounts_only_owned_mounts(self):
        run_root = self.root / "partial-chroot"
        run_root.mkdir()
        unmount_log = run_root / "unmount.log"

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
MOD="$2/mod"
RECOVERY_ACTION="$2/recovery-action"
UNMOUNT_LOG="$2/unmount.log"
mount() {
    target="${*: -1}"
    [ "$target" != "$MOD/sys" ]
}
umount() { printf '%s\n' "${*: -1}" >> "$UNMOUNT_LOG"; }
mount_recovery_chroot
status=$?
cleanup_recovery_runtime
printf 'result=%s\n' "$status"
'''
        result = subprocess.run(
            ["bash", "-c", command, "partial-chroot-harness",
             str(self.boot_scripts["recovery.sh"]),
             str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertIn("result=1", result.stdout)
        self.assertEqual(
            unmount_log.read_text(encoding="utf-8").splitlines(),
            [str(run_root / "mod" / "proc")],
        )

    def test_partial_touch_setup_is_cleaned_after_missing_device(self):
        run_root = self.root / "partial-touch"
        run_root.mkdir()
        missing_device = run_root / "missing-touch-device"
        lifecycle_log = run_root / "lifecycle.log"

        recovery = self._patched_script(
            self.boot_scripts["recovery.sh"],
            "recovery-partial-touch.sh",
            {"RECOVERY_TOUCH_DEVICE=/dev/input/guppy":
             "RECOVERY_TOUCH_DEVICE=%s" % missing_device},
        )

        command = r'''
source "$1"
trap - EXIT HUP INT TERM
MOD="$2/mod"
mkdir -p "$MOD/var/run"
RECOVERY_ACTION="$2/recovery-action"
TEST_STARTED="$2/tslib-started"
LIFECYCLE_LOG="$2/lifecycle.log"
chroot() {
    printf 'chroot:%s\n' "$*" >> "$LIFECYCLE_LOG"
    case "$*" in *"S35tslib start"*) touch "$TEST_STARTED" ;; esac
}
mount() {
    [ -f "$TEST_STARTED" ] \
        && echo 'tmpfs on /tmp/parent_root type tmpfs (rw)'
    return 0
}
umount() { printf 'umount:%s\n' "${*: -1}" >> "$LIFECYCLE_LOG"; }
rmdir() { :; }
start_recovery_touch
status=$?
cleanup_recovery_runtime
printf 'result=%s\n' "$status"
'''
        result = subprocess.run(
            ["bash", "-c", command, "partial-touch-harness",
             str(recovery), str(run_root)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        lifecycle = lifecycle_log.read_text(encoding="utf-8")

        self.assertIn("result=1", result.stdout)
        self.assertIn("S35tslib stop", lifecycle)
        self.assertIn("umount:/tmp/parent_root", lifecycle)

    def _s00_harness(self, case_name, recovery_script, boot_skip=False,
                     recovery_failure=False, boot_failure=False,
                     preexisting_soft=False):
        run_root = self.root / case_name
        run_root.mkdir()
        boot_skip_path = run_root / "boot-skip"
        failure_path = run_root / "recovery-failure"
        boot_failure_path = run_root / "boot-failure"
        soft_path = run_root / "skip-soft"
        hard_path = run_root / "skip-hard"
        legacy_path = run_root / "skip-legacy"
        if boot_skip:
            boot_skip_path.touch()
        if recovery_failure:
            failure_path.touch()
        if boot_failure:
            boot_failure_path.touch()
        if preexisting_soft:
            soft_path.touch()
            legacy_path.touch()

        command = r'''
source "$1"
BOOT_SKIP_F="$2"
BOOT_RECOVERY_FAILURE_F="$3"
BOOT_FAILURE_F="$4"
BOOT_REASON_F="${2%/*}/boot-reason"
SKIP_MOD_F="${2%/*}/skip-legacy"
SKIP_MOD_SOFT_F="$5"
SKIP_MOD_HARD_F="$6"
RECOVERY_SCRIPT="$7"
sync() { :; }
handle_boot_options
printf 'result=%s\n' "$?"
'''
        result = subprocess.run(
            ["bash", "-c", command, "recovery-harness", str(self.s00_init),
             str(boot_skip_path), str(failure_path), str(boot_failure_path),
             str(soft_path), str(hard_path), str(recovery_script)],
            env={**os.environ, "RECOVERY_TEST_HARD": str(hard_path)},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        paths = {
            "skip": boot_skip_path,
            "recovery": failure_path,
            "failure": boot_failure_path,
            "legacy": legacy_path,
            "soft": soft_path,
            "hard": hard_path,
            "reason": run_root / "boot-reason",
        }
        return result, paths

    def test_recovery_start_failure_falls_back_and_disarms_crash_marker(self):
        recovery = self.root / "recovery-fails"
        recovery.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        recovery.chmod(0o755)

        result, paths = self._s00_harness(
            "recovery-failure", recovery, boot_skip=True,
            preexisting_soft=True)

        self.assertIn("result=0", result.stdout)
        self.assertIn("Starting stock firmware", result.stdout)
        self.assertFalse(paths["recovery"].exists())
        self.assertFalse(paths["legacy"].exists())
        self.assertFalse(paths["soft"].exists())
        self.assertTrue(paths["hard"].exists())
        self.assertEqual(
            paths["reason"].read_text(encoding="utf-8"),
            "?? Recovery exited with an error.\n")

    def test_broken_recovery_variants_disarm_marker_and_use_hard_fallback(self):
        variants = {}

        missing = self.root / "missing-recovery"
        variants["missing"] = missing

        non_executable = self.root / "non-executable-recovery"
        non_executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        variants["non-executable"] = non_executable

        syntax_error = self.root / "syntax-error-recovery"
        syntax_error.write_text("#!/bin/bash\nif ; then\n", encoding="utf-8")
        syntax_error.chmod(0o755)
        variants["syntax-error"] = syntax_error

        for name, recovery in variants.items():
            with self.subTest(name=name):
                result, paths = self._s00_harness(
                    "broken-" + name, recovery, boot_skip=True)

                self.assertIn("result=0", result.stdout)
                self.assertFalse(paths["recovery"].exists())
                self.assertTrue(paths["hard"].exists())
                self.assertFalse(paths["soft"].exists())

    def test_persisted_recovery_marker_bypasses_recovery_once(self):
        called = self.root / "recovery-called"
        recovery = self.root / "recovery-must-not-run"
        recovery.write_text(
            "#!/bin/sh\ntouch {}\n".format(called), encoding="utf-8")
        recovery.chmod(0o755)

        result, paths = self._s00_harness(
            "persisted-recovery", recovery, boot_skip=True,
            recovery_failure=True)

        self.assertIn("result=0", result.stdout)
        self.assertFalse(called.exists())
        self.assertFalse(paths["recovery"].exists())
        self.assertFalse(paths["soft"].exists())
        self.assertTrue(paths["hard"].exists())

    def test_returned_recovery_disarms_crash_marker_and_keeps_hard_mode(self):
        recovery = self.root / "recovery-success"
        recovery.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        recovery.chmod(0o755)

        result, paths = self._s00_harness(
            "recovery-success-case", recovery, boot_skip=True)

        self.assertIn("result=0", result.stdout)
        self.assertFalse(paths["recovery"].exists())
        self.assertFalse(paths["soft"].exists())
        self.assertTrue(paths["hard"].exists())
        self.assertFalse(paths["reason"].exists())

    def test_hard_bypass_is_published_before_recovery_starts(self):
        violation = self.root / "recovery-started-without-hard-bypass"
        recovery = self.root / "recovery-checks-hard-bypass"
        recovery.write_text(
            "#!/bin/sh\n"
            "[ -f \"$RECOVERY_TEST_HARD\" ] || touch {}\n".format(violation)
            + "exit 0\n",
            encoding="utf-8")
        recovery.chmod(0o755)

        result, paths = self._s00_harness(
            "recovery-order", recovery, boot_skip=True)

        self.assertIn("result=0", result.stdout)
        self.assertFalse(violation.exists())
        self.assertTrue(paths["hard"].exists())

    def test_all_persistent_flag_combinations_have_one_policy(self):
        for recovery_failure in (False, True):
            for boot_skip in (False, True):
                for boot_failure in (False, True):
                    bits = "{}{}{}".format(
                        int(recovery_failure), int(boot_skip), int(boot_failure))
                    called = self.root / ("called-" + bits)
                    recovery = self.root / ("recovery-" + bits)
                    recovery.write_text(
                        "#!/bin/sh\ntouch {}\n".format(called), encoding="utf-8")
                    recovery.chmod(0o755)

                    result, paths = self._s00_harness(
                        "flags-" + bits, recovery,
                        boot_skip=boot_skip,
                        recovery_failure=recovery_failure,
                        boot_failure=boot_failure,
                    )

                    if recovery_failure:
                        self.assertIn("result=0", result.stdout)
                        self.assertFalse(called.exists())
                        self.assertTrue(paths["hard"].exists())
                        self.assertFalse(paths["soft"].exists())
                        self.assertFalse(any(
                            paths[name].exists()
                            for name in ("skip", "recovery", "failure")))
                    elif boot_skip:
                        self.assertIn("result=0", result.stdout)
                        self.assertTrue(called.exists())
                        self.assertFalse(paths["recovery"].exists())
                        self.assertTrue(paths["hard"].exists())
                        self.assertFalse(paths["skip"].exists())
                        self.assertFalse(paths["failure"].exists())
                    elif boot_failure:
                        self.assertIn("result=0", result.stdout)
                        self.assertFalse(called.exists())
                        self.assertTrue(paths["hard"].exists())
                        self.assertFalse(paths["failure"].exists())
                    else:
                        self.assertIn("result=1", result.stdout)
                        self.assertFalse(called.exists())

    def _normal_start_harness(self, child_status, publish_ready=True):
        suffix = "ready" if publish_ready else "no-ready"
        run_root = self.root / ("normal-{}-{}".format(child_status, suffix))
        run_root.mkdir()
        child = run_root / "init-main"
        if child_status == 0 and publish_ready:
            child_body = "touch {}/init-finished\nexit 0\n".format(run_root)
        else:
            child_body = "exit {}\n".format(child_status)
        child.write_text("#!/bin/sh\n" + child_body, encoding="utf-8")
        child.chmod(0o755)

        command = r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_MAIN_SCRIPT="$3"
SKIP_IMAGE="$2/skip.img.xz"
BOOT_IMAGE="$2/boot.img"
FRAMEBUFFER="$2/framebuffer"
TRANSITION_VIOLATION="$2/transition-violation"
sync() { :; }
sleep() { :; }
xzcat() { :; }
cat() { :; }
commit_boot_guard() { :; }
rm() {
    if [ "$*" = "-f $BOOT_SKIP_F" ] && [ ! -f "$BOOT_FAILURE_F" ]; then
        command touch "$TRANSITION_VIOLATION"
    fi
    command rm "$@"
}
start_normal_boot
'''
        result = subprocess.run(
            ["bash", "-c", command, "normal-start-harness", str(self.s00_init),
             str(run_root), str(child)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)
        return result, run_root

    def test_normal_init_publishes_ready_only_after_child_success(self):
        successful, success_root = self._normal_start_harness(0)
        failed, failed_root = self._normal_start_harness(7)
        special, special_root = self._normal_start_harness(10)

        self.assertEqual(successful.returncode, 0, successful.stdout)
        self.assertTrue((success_root / "init-finished").exists())
        self.assertTrue((success_root / "boot-failure").exists())
        self.assertFalse((success_root / "skip-hard").exists())

        self.assertEqual(failed.returncode, 0, failed.stdout)
        self.assertFalse((failed_root / "init-finished").exists())
        self.assertFalse((failed_root / "boot-failure").exists())
        self.assertTrue((failed_root / "skip-hard").exists())

        self.assertEqual(special.returncode, 0, special.stdout)
        self.assertFalse((special_root / "init-finished").exists())
        self.assertFalse((special_root / "boot-failure").exists())
        self.assertFalse((special_root / "skip-hard").exists())

    def test_normal_transition_arms_failure_before_removing_skip(self):
        result, run_root = self._normal_start_harness(0)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((run_root / "transition-violation").exists())
        self.assertFalse((run_root / "boot-skip").exists())
        self.assertTrue((run_root / "boot-failure").exists())

    def test_noop_normal_initializer_cannot_publish_ready(self):
        result, run_root = self._normal_start_harness(0, publish_ready=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse((run_root / "init-finished").exists())
        self.assertTrue((run_root / "skip-hard").exists())
        self.assertFalse((run_root / "boot-failure").exists())

    def test_persistent_failure_precedes_stale_readiness(self):
        run_root = self.root / "persistent-before-ready"
        run_root.mkdir()
        (run_root / "init-finished").touch()
        (run_root / "recovery-failure").touch()
        init_called = run_root / "init-called"
        init_main = run_root / "init-main"
        init_main.write_text(
            "#!/bin/sh\ntouch {}\n".format(init_called), encoding="utf-8")
        init_main.chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_MAIN_SCRIPT="$3"
sync() { :; }
start_normal_boot
''', "persistent-before-ready-harness", str(self.s00_init), str(run_root),
             str(init_main)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(init_called.exists())
        self.assertFalse((run_root / "init-finished").exists())
        self.assertFalse((run_root / "recovery-failure").exists())
        self.assertTrue((run_root / "skip-hard").exists())

    def test_existing_temporary_stock_mode_stops_reentry(self):
        run_root = self.root / "temporary-stock-reentry"
        run_root.mkdir()
        (run_root / "skip-soft").touch()
        init_called = run_root / "init-called"
        init_main = run_root / "init-main"
        init_main.write_text(
            "#!/bin/sh\ntouch {}\n".format(init_called), encoding="utf-8")
        init_main.chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_MAIN_SCRIPT="$3"
sync() { :; }
start_normal_boot
''', "temporary-stock-reentry-harness", str(self.s00_init), str(run_root),
             str(init_main)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(init_called.exists())
        self.assertFalse((run_root / "boot-skip").exists())
        self.assertTrue((run_root / "skip-soft").exists())

    def test_normal_boot_does_not_depend_on_kernel_boot_id(self):
        run_root = self.root / "missing-kernel-boot-id"
        run_root.mkdir()
        init_called = run_root / "init-called"
        init_main = run_root / "init-main"
        init_main.write_text(
            "#!/bin/sh\ntouch {0}\ntouch {1}/init-finished\n".format(
                init_called, run_root),
            encoding="utf-8")
        init_main.chmod(0o755)

        result = subprocess.run(
            ["bash", "-c", r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_MAIN_SCRIPT="$3"
SKIP_IMAGE="$2/skip.img.xz"
BOOT_IMAGE="$2/boot.img"
FRAMEBUFFER="$2/framebuffer"
sync() { :; }
sleep() { :; }
xzcat() { :; }
cat() { :; }
commit_boot_guard() { :; }
start_normal_boot
''', "missing-boot-id-harness", str(self.s00_init), str(run_root),
             str(init_main)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(init_called.exists())
        self.assertTrue((run_root / "init-finished").exists())
        self.assertFalse((run_root / "skip-hard").exists())

    def test_guard_commit_replaces_symlink_and_removes_stale_candidates(self):
        installed = self.root / "installed-S00init"
        installed.symlink_to(S00_INIT)
        stale_candidates = [self.root / ".S00init.101", self.root / ".S00init.102"]
        for candidate in stale_candidates:
            candidate.touch()
        unrelated = self.root / ".S00other.101"
        unrelated.touch()
        invalid = self.root / "invalid-guard"
        invalid.write_text("#!/bin/bash\nif ; then\n", encoding="utf-8")

        command = r'''
source "$1"
GUARD_SOURCE="$2"
INSTALLED_GUARD="$3"
commit_boot_guard
'''
        valid_result = subprocess.run(
            ["bash", "-c", command, "guard-commit-harness", str(self.s00_init),
             str(S00_INIT), str(installed)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(valid_result.returncode, 0, valid_result.stdout)
        self.assertFalse(installed.is_symlink())
        self.assertEqual(installed.read_bytes(), S00_INIT.read_bytes())
        self.assertTrue(os.access(installed, os.X_OK))
        self.assertFalse(any(candidate.exists() for candidate in stale_candidates))
        self.assertTrue(unrelated.exists())

        stale_noop_candidate = self.root / ".S00init.103"
        stale_noop_candidate.touch()
        noop_result = subprocess.run(
            ["bash", "-c", command, "guard-commit-harness", str(self.s00_init),
             str(S00_INIT), str(installed)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(noop_result.returncode, 0, noop_result.stdout)
        self.assertFalse(stale_noop_candidate.exists())

        previous = installed.read_bytes()
        invalid_result = subprocess.run(
            ["bash", "-c", command, "guard-commit-harness", str(self.s00_init),
             str(invalid), str(installed)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertNotEqual(invalid_result.returncode, 0, invalid_result.stdout)
        self.assertEqual(installed.read_bytes(), previous)

    def test_guard_exposes_only_the_early_start_operation(self):
        result = subprocess.run(
            ["bash", str(self.s00_init), "reload"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Usage:", result.stdout)

    def test_service_restart_preserves_the_installed_guard(self):
        stock_root = self.root / "stock-root"
        init_dir = stock_root / "etc" / "init.d"
        init_dir.mkdir(parents=True)
        installed = init_dir / "S00init"
        installed.write_text("last-known-good\n", encoding="utf-8")
        obsolete = init_dir / "S00fix"
        obsolete.touch()

        logs = self.root / "logs"
        logs.mkdir()
        skip_reboot = self.root / "skip-reboot"
        skip_reboot.touch()
        service = self._patched_script(
            FORGE_X_SERVICE, "forge-x-service", {
                "/tmp/mod_skip_reboot": str(skip_reboot),
                "/_root": str(stock_root),
                "/data/logFiles": str(logs),
                "/dev/mmcblk0p6": str(self.root / "root-device"),
            })

        fake_bin = self.root / "service-bin"
        fake_bin.mkdir()
        for name in ("mount", "umount"):
            command = fake_bin / name
            command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            command.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

        result = subprocess.run(
            ["bash", str(service), "restart"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(installed.is_symlink())
        self.assertEqual(installed.read_text(encoding="utf-8"),
                         "last-known-good\n")
        self.assertFalse(obsolete.exists())

    def test_late_services_fail_closed_without_init_ready(self):
        for name in ("S55boot", "S60dropbear", "S98camera", "S98zssh", "S99root"):
            with self.subTest(name=name):
                result = subprocess.run(
                    ["bash", str(self.boot_scripts[name]), "start"],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    check=False)

                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("incomplete mode", result.stdout)

    def _run_hard_stock_preparation(self, restore_status, hangs=False):
        run_root = self.root / ("hard-stock-{}-{}".format(
            restore_status, "hang" if hangs else "done"))
        run_root.mkdir()
        mod = run_root / "mod"
        (mod / "opt" / "config").mkdir(parents=True)
        commands = run_root / "commands"
        commands.mkdir()
        calls = run_root / "calls"
        skip_log = run_root / "skip.log"
        screen_log = run_root / "screen.log"
        restore_pid = run_root / "restore.pid"
        config_tmp = run_root / "printer.cfg.tmp"

        common = run_root / "common.sh"
        common.write_text(
            "MOD={mod}\n"
            "CMDS={commands}\n"
            "NOT_FIRST_LAUNCH_F={root}/not-first\n"
            "mount_data_partition() {{ echo mount-data >> {calls}; }}\n"
            "init_chroot() {{ echo init-chroot >> {calls}; }}\n"
            "dispose_chroot() {{ echo dispose-chroot >> {calls}; }}\n"
            "logged() {{ echo logged >> {screen}; cat >/dev/null; return 7; }}\n".format(
                mod=mod, commands=commands, root=run_root, calls=calls,
                screen=screen_log),
            encoding="utf-8")

        zdisplay = commands / "zdisplay.sh"
        zdisplay.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$STOCK_TEST_CALLS\"\n"
            "case \"$1\" in\n"
            "  stock)\n"
            "    if [ \"$STOCK_TEST_HANGS\" = 1 ]; then\n"
            "      touch \"$STOCK_TEST_CONFIG_TMP\"\n"
            "      trap '' TERM\n"
            "      sleep 30 &\n"
            "      echo \"$$ $!\" > \"$STOCK_TEST_PID\"\n"
            "      wait\n"
            "    fi\n"
            "    exit \"$STOCK_TEST_STATUS\"\n"
            "  ;;\n"
            "esac\n",
            encoding="utf-8")
        zdisplay.chmod(0o755)

        s55 = self._patched_script(
            self.boot_scripts["S55boot"],
            "S55boot-hard-stock-{}".format(restore_status), {
                "/opt/config/mod/.shell/common.sh": str(common),
                "/opt/config/mod_data/log/skip.log": str(skip_log),
                "/opt/config/printer.cfg.tmp": str(config_tmp),
                "STOCK_RESTORE_TIMEOUT_SECONDS=15":
                    "STOCK_RESTORE_TIMEOUT_SECONDS=1",
            })

        fake_bin = run_root / "bin"
        fake_bin.mkdir()
        for name in ("mount", "umount"):
            command = fake_bin / name
            command.write_text(
                "#!/bin/sh\n"
                "printf '%s %s\\n' \"${0##*/}\" \"$*\" >> \"$STOCK_TEST_CALLS\"\n"
                "exit 0\n",
                encoding="utf-8")
            command.chmod(0o755)

        for path in self.boot_paths.values():
            path.unlink(missing_ok=True)
        self.boot_paths["SKIP_MOD_HARD_F"].touch()
        (self.root / "boot-reason").write_text(
            "?? Recovery exited with an error.\n", encoding="utf-8")
        environment = dict(os.environ)
        environment.update({
            "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
            "STOCK_TEST_CALLS": str(calls),
            "STOCK_TEST_CONFIG_TMP": str(config_tmp),
            "STOCK_TEST_HANGS": "1" if hangs else "0",
            "STOCK_TEST_PID": str(restore_pid),
            "STOCK_TEST_STATUS": str(restore_status),
        })
        result = subprocess.run(
            ["bash", str(s55), "start"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            timeout=6)
        return result, calls, skip_log, restore_pid

    def test_hard_stock_restores_config_and_logs_without_weakening_mode(self):
        result, calls, skip_log, _restore_pid = \
            self._run_hard_stock_preparation(0)

        self.assertEqual(result.returncode, 0, result.stdout)
        call_log = calls.read_text(encoding="utf-8")
        self.assertIn("stock --skip-reboot\n", call_log)
        self.assertIn("Stock display configuration is ready",
                      skip_log.read_text(encoding="utf-8"))
        self.assertIn("Recovery exited with an error",
                      skip_log.read_text(encoding="utf-8"))
        self.assertTrue(self.boot_paths["SKIP_MOD_HARD_F"].exists())
        self.assertFalse(self.boot_paths["SKIP_MOD_SOFT_F"].exists())

    def test_failed_hard_stock_restore_keeps_hard_mode_and_continues(self):
        result, calls, skip_log, _restore_pid = \
            self._run_hard_stock_preparation(7)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("stock --skip-reboot", calls.read_text(encoding="utf-8"))
        self.assertIn("Stock screen setup failed",
                      skip_log.read_text(encoding="utf-8"))
        self.assertTrue(self.boot_paths["SKIP_MOD_HARD_F"].exists())
        self.assertFalse(self.boot_paths["SKIP_MOD_SOFT_F"].exists())

    def test_timed_out_hard_stock_restore_is_stopped_and_boot_continues(self):
        result, calls, skip_log, restore_pid = \
            self._run_hard_stock_preparation(0, hangs=True)

        self.assertEqual(result.returncode, 0, result.stdout)
        log = skip_log.read_text(encoding="utf-8")
        self.assertIn("Stock screen setup timed out", log)
        self.assertIn("Limit: 1 seconds", log)
        self.assertIn("Stock screen setup failed", log)
        call_log = calls.read_text(encoding="utf-8")
        self.assertIn("dispose-chroot", call_log)
        self.assertIn("umount /data", call_log)
        pids = restore_pid.read_text(encoding="utf-8").split()
        for pid in pids:
            proc_stat = pathlib.Path("/proc") / pid / "stat"
            deadline = time.monotonic() + 2
            while True:
                try:
                    stat = proc_stat.read_text(encoding="utf-8")
                except FileNotFoundError:
                    break

                _identity, separator, process_fields = stat.rpartition(") ")
                self.assertTrue(separator, stat)
                if process_fields[0] == "Z":
                    break
                if time.monotonic() >= deadline:
                    self.fail("restore process is still running: {}".format(stat))
                time.sleep(0.05)
        self.assertFalse((restore_pid.parent / "printer.cfg.tmp").exists())
        self.assertTrue(self.boot_paths["SKIP_MOD_HARD_F"].exists())
        self.assertFalse(self.boot_paths["SKIP_MOD_SOFT_F"].exists())

    def test_s99_clears_boot_failure_only_after_successful_chroot_start(self):
        common = self.root / "late-common.sh"
        scripts = self.root / "late-scripts"
        scripts.mkdir()
        screen = scripts / "screen.sh"
        screen.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        screen.chmod(0o755)
        migrate = self.root / "migrate-db"
        migrate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        migrate.chmod(0o755)
        boot_failure = self.root / "boot-failure"
        self.boot_paths["INIT_FLAG"].touch()

        common.write_text(
            "MOD={root}/mod\n"
            "NOT_FIRST_LAUNCH_F={root}/not-first\n"
            "CUSTOM_BOOT_F={root}/custom-boot\n"
            "SCRIPTS={scripts}\n"
            "SCREEN_FOLLOW_UP_LOG={root}/screen-log\n"
            "BOOT_FAILURE_F={root}/boot-failure\n"
            "FORGE_X_SCREEN_BUSY_F={root}/screen-busy\n"
            "logged() {{ printf '%s\\n' \"$*\" > {root}/logged-args; cat; }}\n".format(
                root=self.root, scripts=scripts),
            encoding="utf-8")
        s99 = self._patched_script(
            self.boot_scripts["S99root"], "S99root-late-test", {
                "/opt/config/mod/.shell/common.sh": str(common),
                "/opt/config/mod/.shell/migrate_db.sh": str(migrate),
            })

        fake_bin = self.root / "late-bin"
        fake_bin.mkdir()
        chroot = fake_bin / "chroot"
        chroot.write_text(
            "#!/bin/sh\nexit \"${CHROOT_TEST_STATUS:-0}\"\n",
            encoding="utf-8")
        chroot.chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

        boot_failure.touch()
        environment["CHROOT_TEST_STATUS"] = "7"
        failed = subprocess.run(
            ["bash", str(s99), "start"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertNotEqual(failed.returncode, 0, failed.stdout)
        self.assertTrue(boot_failure.exists())

        environment["CHROOT_TEST_STATUS"] = "0"
        succeeded = subprocess.run(
            ["bash", str(s99), "start"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(succeeded.returncode, 0, succeeded.stdout)
        self.assertFalse(boot_failure.exists())
        logged_args = self.root / "logged-args"
        self.assertEqual(
            logged_args.read_text(encoding="utf-8").strip(),
            "/data/logFiles/boot.log --send-to-screen --screen-no-followup")

        repeated = subprocess.run(
            ["bash", str(s99), "start"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(repeated.returncode, 0, repeated.stdout)
        self.assertEqual(
            logged_args.read_text(encoding="utf-8").strip(),
            "/data/logFiles/boot.log --screen-no-followup")

    def test_s99_distinguishes_system_shutdown_from_service_stop(self):
        self.boot_paths["INIT_FLAG"].touch()
        common = self.root / "power-common.sh"
        mod = self.root / "mod"
        mod.mkdir()
        printer = self.root / "printer"
        printer.touch()
        caller = self.root / "caller-cmdline"
        common.write_text(
            "MOD={mod}\n"
            "NOT_FIRST_LAUNCH_F={root}/not-first\n"
            "CUSTOM_BOOT_F={root}/custom-boot\n"
            "SCREEN_FOLLOW_UP_LOG={root}/screen-log\n"
            "BOOT_FAILURE_F={root}/boot-failure\n"
            "FORGE_X_SCREEN_BUSY_F={root}/screen-busy\n"
            "logged() {{ cat; }}\n"
            "printer_command() {{ printf '%s\\n' \"$1\" > {printer}; }}\n".format(
                mod=mod, root=self.root, printer=printer),
            encoding="utf-8")
        s99 = self._patched_script(
            self.boot_scripts["S99root"], "S99root-power-test", {
                "/opt/config/mod/.shell/common.sh": str(common),
                "/proc/$S99ROOT_CALLER_PID/cmdline": str(caller),
                "/tmp/printer": str(printer),
            })
        fake_bin = self.root / "power-bin"
        fake_bin.mkdir()
        (fake_bin / "ps").write_text(
            "#!/bin/sh\nprintf '%s\\n' '1 root klippy.py'\n",
            encoding="utf-8")
        (fake_bin / "chroot").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
        (fake_bin / "sleep").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> {}/sleeps\n".format(
                self.root), encoding="utf-8")
        for command in ("ps", "chroot", "sleep"):
            (fake_bin / command).chmod(0o755)
        environment = dict(os.environ)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

        caller.write_bytes(b"/bin/bash\0/etc/init.d/rcK\0")
        system_stop = subprocess.run(
            ["bash", str(s99), "stop"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

        self.assertEqual(system_stop.returncode, 0, system_stop.stdout)
        self.assertEqual(
            printer.read_text(encoding="utf-8"),
            "action:forge_x_shutting_down\n")
        self.assertEqual(
            (self.root / "sleeps").read_text(encoding="utf-8"), "1\n")

        printer.write_text("untouched\n", encoding="utf-8")
        caller.write_bytes(b"/bin/bash\0/etc/init.d/S99root\0stop\0")
        service_stop = subprocess.run(
            ["bash", str(s99), "stop"], env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

        self.assertEqual(service_stop.returncode, 0, service_stop.stdout)
        self.assertEqual(
            printer.read_text(encoding="utf-8"), "action:forge_x_redraw\n")
        self.assertEqual(
            (self.root / "sleeps").read_text(encoding="utf-8"), "1\n")

    def test_dropbear_dispatch_is_explicit_for_mod_soft_and_recovery(self):
        fake_bin = self.root / "dropbear-bin"
        fake_bin.mkdir()
        daemon_log = self.root / "dropbear-daemon.log"
        daemon = fake_bin / "start-stop-daemon"
        daemon.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$DROPBEAR_TEST_LOG\"\n"
            "exit \"${DROPBEAR_TEST_STATUS:-0}\"\n",
            encoding="utf-8")
        daemon.chmod(0o755)

        paths = self.boot_paths
        environment = dict(os.environ)
        environment.update({
            "DROPBEAR_TEST_LOG": str(daemon_log),
            "PATH": str(fake_bin) + os.pathsep + environment["PATH"],
        })

        def run(action):
            daemon_log.unlink(missing_ok=True)
            return subprocess.run(
                ["sh", str(self.boot_scripts["S60dropbear"]), action],
                env=environment, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                check=False)

        incomplete = run("start")
        self.assertIn("disabled in incomplete mode", incomplete.stdout)
        self.assertFalse(daemon_log.exists())

        paths["SKIP_MOD_SOFT_F"].touch()
        soft = run("start")
        self.assertEqual(soft.returncode, 0, soft.stdout)
        self.assertIn("-Sbm", daemon_log.read_text(encoding="utf-8"))

        paths["SKIP_MOD_HARD_F"].touch()
        hard = run("start")
        self.assertIn("disabled in stock-hard mode", hard.stdout)
        self.assertFalse(daemon_log.exists())

        for path in paths.values():
            path.unlink(missing_ok=True)
        recovery = run("recovery-start")
        self.assertEqual(recovery.returncode, 0, recovery.stdout)
        self.assertIn("-Sbm", daemon_log.read_text(encoding="utf-8"))

        environment["DROPBEAR_TEST_STATUS"] = "7"
        failed_recovery = run("recovery-start")
        self.assertEqual(failed_recovery.returncode, 7, failed_recovery.stdout)
        self.assertIn("FAIL", failed_recovery.stdout)
        environment.pop("DROPBEAR_TEST_STATUS")

        stopped = run("stop")
        self.assertEqual(stopped.returncode, 0, stopped.stdout)
        self.assertIn("-K", daemon_log.read_text(encoding="utf-8"))

    def test_full_start_disarms_crash_marker_after_recovery_returns(self):
        boot_flags = self.root / "boot-flags"
        boot_flags.mkdir()
        boot_skip = boot_flags / "BOOT_FLAG_SKIP"
        crash_marker = boot_flags / "BOOT_FLAG_RECOVERY_FAILURE"
        boot_failure = boot_flags / "BOOT_FLAG_FAILURE"
        soft = self.root / "skip-soft"
        hard = self.root / "skip-hard"
        recovery = self.root / "recovery-fails"
        recovery.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        recovery.chmod(0o755)
        boot_skip.touch()

        command = r'''
source "$1"
INIT_FLAG="$2"
BOOT_SKIP_F="$3"
BOOT_RECOVERY_FAILURE_F="$4"
BOOT_FAILURE_F="$5"
SKIP_MOD_F="${3%/*}/skip-legacy"
SKIP_MOD_SOFT_F="$6"
SKIP_MOD_HARD_F="$7"
RECOVERY_SCRIPT="$8"
sync() { :; }
start_normal_boot
'''
        result = subprocess.run(
            ["bash", "-c", command, "full-start-harness", str(self.s00_init),
             str(self.root / "init-finished"), str(boot_skip),
             str(crash_marker), str(boot_failure), str(soft), str(hard),
             str(recovery)],
            text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(soft.exists())
        self.assertTrue(hard.exists())
        self.assertFalse(crash_marker.exists())
        self.assertFalse(boot_skip.exists())

    def test_skip_prompt_is_armed_before_the_visible_wait_starts(self):
        run_root = self.root / "skip-window-power-loss"
        run_root.mkdir()
        init_main = run_root / "init-main"
        init_main.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        init_main.chmod(0o755)
        events = run_root / "events"

        result = subprocess.run(
            ["bash", "-c", r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
INIT_MAIN_SCRIPT="$3"
SKIP_IMAGE="$2/skip.img.xz"
FRAMEBUFFER="$2/framebuffer"
EVENTS="$2/events"
sync() { echo sync >> "$EVENTS"; }
xzcat() { echo prompt >> "$EVENTS"; }
sleep() { exit 77; }
start_normal_boot
''', "skip-window-harness", str(self.s00_init), str(run_root),
             str(init_main)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 77, result.stdout)
        self.assertEqual(events.read_text(encoding="utf-8"),
                         "sync\nprompt\n")
        self.assertTrue((run_root / "boot-skip").exists())
        self.assertFalse((run_root / "boot-failure").exists())
        self.assertFalse((run_root / "skip-hard").exists())

    def test_failed_recovery_return_is_not_repeated_next_boot(self):
        run_root = self.root / "completed-recovery-next-boot"
        run_root.mkdir()
        recovery_called = run_root / "recovery-called"
        init_called = run_root / "init-called"
        recovery = run_root / "recovery"
        recovery.write_text(
            "#!/bin/sh\ntouch {}\nexit 7\n".format(recovery_called),
            encoding="utf-8")
        recovery.chmod(0o755)
        init_main = run_root / "init-main"
        init_main.write_text(
            "#!/bin/sh\ntouch {0}\ntouch {1}/init-finished\nexit 0\n".format(
                init_called, run_root),
            encoding="utf-8")
        init_main.chmod(0o755)
        (run_root / "boot-skip").touch()

        result = subprocess.run(
            ["bash", "-c", r'''
source "$1"
INIT_FLAG="$2/init-finished"
BOOT_SKIP_F="$2/boot-skip"
BOOT_RECOVERY_FAILURE_F="$2/recovery-failure"
BOOT_FAILURE_F="$2/boot-failure"
SKIP_MOD_F="$2/skip-legacy"
SKIP_MOD_SOFT_F="$2/skip-soft"
SKIP_MOD_HARD_F="$2/skip-hard"
RECOVERY_SCRIPT="$3"
INIT_MAIN_SCRIPT="$4"
SKIP_IMAGE="$2/skip.img.xz"
BOOT_IMAGE="$2/boot.img"
FRAMEBUFFER="$2/framebuffer"
sync() { :; }
sleep() { :; }
xzcat() { :; }
cat() { :; }
commit_boot_guard() { :; }

start_normal_boot
rm -f "$INIT_FLAG" "$SKIP_MOD_F" "$SKIP_MOD_SOFT_F" "$SKIP_MOD_HARD_F"
start_normal_boot
''', "completed-recovery-harness", str(self.s00_init), str(run_root),
             str(recovery), str(init_main)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            check=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(recovery_called.exists())
        self.assertTrue(init_called.exists())
        self.assertTrue((run_root / "init-finished").exists())
        self.assertFalse((run_root / "recovery-failure").exists())
        self.assertFalse((run_root / "skip-hard").exists())


if __name__ == "__main__":
    unittest.main()
