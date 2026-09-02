"""Behavioral contracts for early firmware image installation.

Copyright (C) 2026, Alexander K <https://github.com/drA1ex>

This file may be distributed under the terms of the GNU GPLv3 license.
"""

import io
import os
import pathlib
import subprocess
import tarfile
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).parents[1]
INSTALL_IMAGE = ROOT / ".shell" / "boot" / "install-image.sh"
INSTALL_IMAGE_RUNNER = ROOT / ".shell" / "boot" / "install-image-runner.sh"
STOCK_IDENTITY = ROOT / ".shell" / "boot" / "stock_identity.sh"


class FirmwareImageInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name)
        self.staging = self.root / ".firmware"
        self.runner_staging = self.root / ".firmware-runner"
        self.result = self.root / "entrypoint-result"
        self.typer_log = self.root / "typer.log"
        self.typer = self.root / "typer"
        self.typer.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$*\" >> \"$TYPER_LOG\"\n",
            encoding="utf-8",
        )
        self.typer.chmod(0o755)
        self.version_file = self.root / "version"
        self.software_dir = self.root / "software"
        self.version_file.write_text("3.1.5\n", encoding="utf-8")
        self._set_stock_identity("Adventurer5M", "0023")
        self.stock_identity = self.root / "stock_identity.sh"
        stock_identity = STOCK_IDENTITY.read_text(encoding="utf-8")
        stock_identity = stock_identity.replace(
            "/root/version", str(self.version_file))
        stock_identity = stock_identity.replace(
            "/opt/PROGRAM/software", str(self.software_dir))
        self.stock_identity.write_text(stock_identity, encoding="utf-8")
        self.install_image = self._installer(
            "install-image.sh", suppress_parent_kill=True)
        self.install_image_library = self._installer(
            "install-image-library.sh", library_only=True)
        self.environment = dict(os.environ)
        self.environment.update({
            "RESULT_PATH": str(self.result),
            "TYPER_LOG": str(self.typer_log),
            "PATH": str(self.root) + os.pathsep + self.environment["PATH"],
        })

    def _installer(self, name, *, suppress_parent_kill=False,
                   library_only=False, extra_replacements=None):
        source = INSTALL_IMAGE.read_text(encoding="utf-8")
        replacements = {
            "/opt/config/mod/.shell/boot/stock_identity.sh":
                str(self.stock_identity),
            "/opt/config/mod/.shell/common.sh": "/dev/null",
            "FIRMWARE_INSTALL_STAGING_DIR=/data/.firmware":
                "FIRMWARE_INSTALL_STAGING_DIR=%s" % self.staging,
            "FIRMWARE_INSTALL_RUNNER_DIR=/data/.firmware-runner":
                "FIRMWARE_INSTALL_RUNNER_DIR=%s" % self.runner_staging,
            "FIRMWARE_INSTALL_RESERVE_KB=16384":
                "FIRMWARE_INSTALL_RESERVE_KB=0",
            "FIRMWARE_INSTALL_ERROR_DELAY_SECONDS=30":
                "FIRMWARE_INSTALL_ERROR_DELAY_SECONDS=0",
            "/opt/config/mod/.shell/boot/install-image-runner.sh":
                str(INSTALL_IMAGE_RUNNER),
            "    mount_data_partition\n": "    :\n",
        }
        if suppress_parent_kill:
            replacements["stop_firmware_parent() {\n"] = (
                "stop_firmware_parent() {\n    return 0\n")
        if library_only:
            marker = 'if [ "$#" -eq 1 ] && [ "$1" = "cleanup" ]; then\n'
            replacements[marker] = "return 0 2>/dev/null || exit 0\n\n" + marker
        if extra_replacements:
            replacements.update(extra_replacements)

        for old, replacement in replacements.items():
            self.assertEqual(source.count(old), 1, old)
            source = source.replace(old, replacement, 1)

        script = self.root / name
        script.write_text(source, encoding="utf-8")
        script.chmod(INSTALL_IMAGE.stat().st_mode & 0o777)
        return script

    def tearDown(self):
        self.temporary.cleanup()

    def _set_stock_identity(self, machine, product_id):
        launcher = self.software_dir / "3.1.5" / "auto_run.sh"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text(
            "#!/bin/sh\nMACHINE=%s\nPID=%s\n" % (machine, product_id),
            encoding="utf-8",
        )

    def _archive(self, name, files, mode=None):
        path = self.root / name
        mode = mode or ("w:xz" if name.endswith(".tar.xz") else "w")
        with tarfile.open(path, mode=mode, format=tarfile.PAX_FORMAT) as archive:
            for member_name, content, member_mode in files:
                payload = content if isinstance(content, bytes) else content.encode("utf-8")
                member = tarfile.TarInfo(member_name)
                member.size = len(payload)
                member.mode = member_mode
                archive.addfile(member, io.BytesIO(payload))
        return path

    def _run(self, image, installer=None):
        return subprocess.run(
            ["bash", str(installer or self.install_image), str(image)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def _wait_for(self, predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def _write_runner(self):
        self.runner_staging.mkdir(exist_ok=True)
        source = INSTALL_IMAGE_RUNNER.read_text(encoding="utf-8")
        runner = self.runner_staging / "runner.sh"
        runner.write_text(source, encoding="utf-8")
        runner.chmod(0o755)
        return runner

    def test_scripts_have_valid_bash_syntax_and_installer_is_executable(self):
        subprocess.run(["bash", "-n", str(INSTALL_IMAGE)], check=True)
        subprocess.run(["bash", "-n", str(INSTALL_IMAGE_RUNNER)], check=True)
        subprocess.run(["bash", "-n", str(STOCK_IDENTITY)], check=True)
        self.assertTrue(os.access(INSTALL_IMAGE, os.X_OK))
        self.assertTrue(os.access(INSTALL_IMAGE_RUNNER, os.X_OK))

    def test_test_command_detects_tar_xz_image(self):
        image = self.root / "Adventurer5M-test.tar.xz"
        image.touch()

        result = subprocess.run(
            ["bash", str(self.install_image), "test", str(self.root)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), str(image))

    def test_plain_tgz_runs_flashforge_script_with_stock_arguments(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("flashforge_init.sh", """#!/bin/bash
printf '%s|%s|%s\\n' "$1" "$2" "$PWD" > "$RESULT_PATH"
""", 0o755),
        ])

        result = self._run(image)

        self.assertEqual(result.returncode, 0, result.stdout)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertEqual(
            self.result.read_text(encoding="utf-8").strip(),
            "Adventurer5M|0023|%s" % self.staging,
        )
        self.assertIn(
            "//% Extracting firmware: 100%",
            result.stdout,
        )
        self.assertRegex(
            result.stdout, r"// Space: \d+ MB needed, \d+ MB free")
        self.assertIn(
            "// Preparing firmware image\n// Checking archive...",
            result.stdout,
        )
        self.assertIn(
            "// Preparing firmware installer\n"
            "// Waiting for Forge-X services to stop.",
            result.stdout,
        )
        self.assertFalse(self.typer_log.exists())
        self.assertTrue((self.staging / "flashforge_init.sh").exists())
        self.assertFalse(list(self.staging.glob(".forge-x-install-*")))
        self.assertTrue((self.runner_staging / "runner.sh").exists())

    def test_accepted_image_stops_supplied_stock_parent(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh",
             "#!/bin/bash\necho stopped > \"$RESULT_PATH\"\n", 0o755),
        ])
        launcher_log = self.root / "launcher-stop.log"
        fake_killall = self.root / "killall"
        fake_killall.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LAUNCHER_LOG\"\n",
            encoding="utf-8")
        fake_killall.chmod(0o755)
        stock_parent = subprocess.Popen(["sleep", "60"])
        self.environment["FIRMWARE_INSTALL_PARENT_PID"] = str(stock_parent.pid)
        self.environment["LAUNCHER_LOG"] = str(launcher_log)
        self.environment["PATH"] = (
            str(self.root) + os.pathsep + self.environment["PATH"])

        installer = self._installer("install-image-parent-kill.sh")
        try:
            result = self._run(image, installer)
            stock_parent.wait(timeout=5)
        finally:
            if stock_parent.poll() is None:
                stock_parent.kill()
                stock_parent.wait()

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(stock_parent.returncode, -9)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertIn(
            "-9 ffstartup-arm",
            launcher_log.read_text(encoding="utf-8").splitlines())

    def test_terminal_failure_retries_until_stock_parent_is_stopped(self):
        command = r'''
source "$1"
FIRMWARE_INSTALL_PARENT_PID=4242
attempts=0
kill() {
    if [ "$1" = "-9" ]; then
        attempts=$((attempts + 1))
        echo "kill-attempt=$attempts"
        [ "$attempts" -ge 3 ]
        return
    fi
    return 0
}
sleep() { :; }
sync() { :; }
firmware_message() { :; }
release_firmware_source_mount() { :; }
fail_firmware_image "test failure"
'''

        result = subprocess.run(
            ["bash", "-c", command, "firmware-test",
             str(self.install_image_library)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("kill-attempt=1", result.stdout)
        self.assertIn("kill-attempt=2", result.stdout)
        self.assertIn("kill-attempt=3", result.stdout)

    def test_test_command_selects_image_for_stock_model(self):
        self._set_stock_identity("Adventurer5MPro", "0024")
        regular = self.root / "Adventurer5M-test.tgz"
        pro = self.root / "Adventurer5MPro-test.tgz"
        regular.touch()
        pro.touch()

        result = subprocess.run(
            ["bash", str(self.install_image), "test", str(self.root)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), str(pro))

    def test_test_command_reports_image_for_another_machine(self):
        pro = self.root / "Adventurer5MPro-test.tgz"
        pro.touch()

        result = subprocess.run(
            ["bash", str(self.install_image), "test", str(self.root)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(pro.name, result.stdout)
        self.assertIn("Image model: Adventurer5MPro", result.stdout)
        self.assertIn("Printer model: Adventurer5M; skipped.", result.stdout)

    def test_source_directory_is_resolved_by_installer(self):
        self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh",
             "#!/bin/bash\necho directory > \"$RESULT_PATH\"\n", 0o755),
        ])

        result = self._run(self.root)

        self.assertEqual(result.returncode, 0, result.stdout)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertEqual(
            self.result.read_text(encoding="utf-8").strip(), "directory")

    def test_successful_preflight_removes_both_previous_staging_trees(self):
        self.staging.mkdir()
        stale_package_file = self.staging / "left-by-old-package"
        stale_package_file.write_text("stale", encoding="utf-8")
        self.runner_staging.mkdir()
        stale_runner_file = self.runner_staging / "left-by-old-runner"
        stale_runner_file.write_text("stale", encoding="utf-8")
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", """#!/bin/bash
[ ! -e "left-by-old-package" ] || exit 9
echo clean > "$RESULT_PATH"
""", 0o755),
        ])

        result = self._run(image)

        self.assertEqual(result.returncode, 0, result.stdout)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertEqual(self.result.read_text(encoding="utf-8").strip(), "clean")
        self.assertFalse(stale_package_file.exists())
        self.assertFalse(stale_runner_file.exists())

    def test_temporary_source_mount_is_released_after_staging(self):
        umount_log = self.root / "umount.log"
        fake_umount = self.root / "umount"
        fake_umount.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$1\" > \"$UMOUNT_LOG\"\n",
            encoding="utf-8")
        fake_umount.chmod(0o755)
        self.environment["UMOUNT_LOG"] = str(umount_log)
        self.environment["PATH"] = (
            str(self.root) + os.pathsep + self.environment["PATH"])

        with tempfile.TemporaryDirectory(
                prefix="forge-x-boot-flag-test-", dir="/tmp") as source:
            image = self._archive("Adventurer5M-test.tgz", [
                ("forge-x-init.sh",
                 "#!/bin/bash\necho unmounted > \"$RESULT_PATH\"\n", 0o755),
            ])
            image.rename(pathlib.Path(source) / image.name)

            result = self._run(pathlib.Path(source))

            self.assertEqual(result.returncode, 0, result.stdout)
            self._wait_for(self.result.exists, "firmware entrypoint did not run")
            self.assertEqual(
                umount_log.read_text(encoding="utf-8").strip(), source)

    def test_tar_xz_prefers_forge_x_script_and_ignores_flashforge_script(self):
        self._set_stock_identity("Adventurer5MPro", "0024")
        image = self._archive("Adventurer5MPro-test.tar.xz", [
            ("forge-x-init.sh", """#!/bin/bash
printf 'forge-x|%s|%s\\n' "$1" "$2" > "$RESULT_PATH"
""", 0o644),
            ("flashforge_init.sh", "if then this is intentionally invalid\n", 0o755),
        ])

        result = self._run(image)

        self.assertEqual(result.returncode, 0, result.stdout)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertEqual(
            self.result.read_text(encoding="utf-8").strip(),
            "forge-x|Adventurer5MPro|0024",
        )

    def test_invalid_forge_x_script_blocks_flashforge_fallback(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", "if then invalid\n", 0o755),
            ("flashforge_init.sh", """#!/bin/bash
echo unexpected > "$RESULT_PATH"
""", 0o755),
        ])

        result = self._run(image)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.result.exists())
        self.assertIn("No valid installer in image", result.stdout)

    def test_nonzero_entrypoint_records_delayed_completion_error(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", "#!/bin/bash\nexit 7\n", 0o755),
        ])

        result = self._run(image)

        self.assertEqual(result.returncode, 0, result.stdout)
        runner_log = self.runner_staging / "runner.log"
        self._wait_for(
            lambda: runner_log.exists()
            and "Installer exited with status 7"
            in runner_log.read_text(encoding="utf-8"),
            "runner did not record the entrypoint failure",
        )
        runner_output = runner_log.read_text(encoding="utf-8")
        self.assertIn("Firmware installer failed", runner_output)
        self.assertIn("Ensure writing stopped, then power off.", runner_output)
        self.assertFalse(self.typer_log.exists())

    def test_archive_member_cannot_escape_staging_directory(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("../outside", "escaped", 0o644),
            ("forge-x-init.sh", "#!/bin/bash\nexit 0\n", 0o755),
        ])

        result = self._run(image)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.root / "outside").exists())
        self.assertIn("Archive is corrupt or unsafe", result.stdout)

    def test_binary_entrypoint_requires_elf_header(self):
        self.staging.mkdir()
        binary = self.staging / "forge-x-init"
        binary.write_bytes(b"#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        (self.staging / "flashforge_init.sh").write_text(
            "#!/bin/bash\nexit 0\n", encoding="utf-8")

        command = """
source "$1"
select_firmware_entrypoint
"""
        result = subprocess.run(
            ["bash", "-c", command, "firmware-test",
             str(self.install_image_library)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_binary_entrypoint_is_selected_before_forge_x_shell(self):
        self.staging.mkdir()
        binary = self.staging / "forge-x-init"
        binary.write_bytes(b"\x7fELF" + bytes(60))
        binary.chmod(0o755)
        (self.staging / "forge-x-init.sh").write_text(
            "#!/bin/bash\nexit 0\n", encoding="utf-8")

        command = """
source "$1"
select_firmware_entrypoint || exit 10
printf '%s|%s\\n' "${FIRMWARE_ENTRYPOINT##*/}" "$FIRMWARE_ENTRYPOINT_KIND"
"""
        result = subprocess.run(
            ["bash", "-c", command, "firmware-test",
             str(self.install_image_library)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), "forge-x-init|binary")

    def test_valid_forge_x_shell_can_replace_invalid_custom_binary(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init", "not an ELF binary\n", 0o755),
            ("forge-x-init.sh", """#!/bin/bash
echo custom-shell > "$RESULT_PATH"
""", 0o644),
            ("flashforge_init.sh", """#!/bin/bash
echo unexpected-flashforge > "$RESULT_PATH"
""", 0o755),
        ])

        result = self._run(image)

        self.assertEqual(result.returncode, 0, result.stdout)
        self._wait_for(self.result.exists, "firmware entrypoint did not run")
        self.assertEqual(
            self.result.read_text(encoding="utf-8").strip(), "custom-shell")

    def test_handoff_returns_without_waiting_or_holding_output_pipe(self):
        release = self.root / "release-entrypoint"
        self.environment["RELEASE_PATH"] = str(release)
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", """#!/bin/bash
while [ ! -f "$RELEASE_PATH" ]; do
    sleep 0.05
done
echo detached > "$RESULT_PATH"
""", 0o755),
        ])

        process = subprocess.Popen(
            ["bash", str(self.install_image), str(image)],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        try:
            output, _ = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 0, output)
            self.assertFalse(self.result.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            release.touch()

        self._wait_for(self.result.exists, "detached entrypoint did not finish")

    def test_runner_waits_for_old_mod_then_clears_screen_history(self):
        self.staging.mkdir()
        self.runner_staging.mkdir()
        runner = self.runner_staging / "runner.sh"
        screen_history = self.root / "logged-message-queue"
        screen_history.write_text("old boot message\n", encoding="utf-8")
        runner_source = INSTALL_IMAGE_RUNNER.read_text(encoding="utf-8")
        runner_source = runner_source.replace(
            "/tmp/logged_message_queue", str(screen_history))
        runner.write_text(runner_source, encoding="utf-8")
        runner.chmod(0o755)
        entrypoint = self.staging / "forge-x-init.sh"
        busy_count = self.root / "busy-count"
        entrypoint.write_text(
            "#!/bin/bash\n"
            "[ ! -e \"$RUNNER_SCREEN_HISTORY\" ] || exit 9\n"
            "cat \"$RUNNER_BUSY_COUNT\" > \"$RESULT_PATH\"\n",
            encoding="utf-8",
        )
        entrypoint.chmod(0o755)

        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        (fake_bin / "mount").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (fake_bin / "lsof").write_text(
            "#!/bin/sh\n"
            "pwd > \"$RUNNER_LSOF_CWD\"\n"
            "[ -f \"$RUNNER_SCREEN_HISTORY\" ] || "
            "echo early > \"$RUNNER_HISTORY_ORDER_ERROR\"\n"
            "count=$(cat \"$RUNNER_BUSY_COUNT\" 2>/dev/null || echo 0)\n"
            "count=$((count + 1))\n"
            "echo \"$count\" > \"$RUNNER_BUSY_COUNT\"\n"
            "[ \"$count\" -gt 2 ] || echo 'logged /opt/config/mod/.bin/exec/logged'\n",
            encoding="utf-8",
        )
        (fake_bin / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        for helper in fake_bin.iterdir():
            helper.chmod(0o755)

        environment = dict(self.environment)
        environment["RUNNER_BUSY_COUNT"] = str(busy_count)
        environment["RUNNER_SCREEN_HISTORY"] = str(screen_history)
        history_order_error = self.root / "history-order-error"
        environment["RUNNER_HISTORY_ORDER_ERROR"] = str(history_order_error)
        lsof_cwd = self.root / "lsof-cwd"
        environment["RUNNER_LSOF_CWD"] = str(lsof_cwd)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        old_mod_cwd = self.root / "opt" / "config" / "mod"
        old_mod_cwd.mkdir(parents=True)
        result = subprocess.run(
            [str(runner), str(self.staging), "forge-x-init.sh", "shell",
             "Adventurer5M", "0023", "0"],
            env=environment,
            cwd=old_mod_cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.result.read_text(encoding="utf-8").strip(), "3")
        self.assertFalse(screen_history.exists())
        self.assertFalse(history_order_error.exists())
        self.assertEqual(
            pathlib.Path(lsof_cwd.read_text(encoding="utf-8").strip()),
            self.runner_staging,
        )

    def test_binary_runner_stops_observing_after_startup_window(self):
        self.staging.mkdir()
        runner = self._write_runner()
        release = self.root / "release-binary"
        entrypoint = self.staging / "forge-x-init"
        entrypoint.write_text(
            "#!/bin/bash\n"
            "while [ ! -e \"$RELEASE_PATH\" ]; do sleep 0.05; done\n"
            "echo detached > \"$RESULT_PATH\"\n",
            encoding="utf-8",
        )
        entrypoint.chmod(0o755)
        environment = dict(self.environment)
        environment["RELEASE_PATH"] = str(release)
        runner_output = self.root / "binary-runner.log"

        try:
            try:
                with runner_output.open("w", encoding="utf-8") as output:
                    result = subprocess.run(
                        [str(runner), str(self.staging), "forge-x-init", "binary",
                         "Adventurer5M", "0023", "0"],
                        env=environment,
                        text=True,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        timeout=15,
                        check=False,
                    )
            except subprocess.TimeoutExpired:
                self.fail(
                    "runner did not detach:\n"
                    + runner_output.read_text(encoding="utf-8"))

            output = runner_output.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, output)
            self.assertIn("remained active through the 10s startup window", output)
            self.assertFalse(self.result.exists())
        finally:
            release.touch()

        self._wait_for(self.result.exists, "detached binary did not continue")

    def test_binary_runner_trusts_early_handled_failure(self):
        self.staging.mkdir()
        runner = self._write_runner()
        entrypoint = self.staging / "forge-x-init"
        entrypoint.write_text("#!/bin/bash\nexit 100\n", encoding="utf-8")
        entrypoint.chmod(0o755)

        result = subprocess.run(
            [str(runner), str(self.staging), "forge-x-init", "binary",
             "Adventurer5M", "0023", "0"],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=4,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("handled failure (status 100)", result.stdout)
        self.assertFalse(self.typer_log.exists())

    def test_binary_runner_records_early_unexpected_failure(self):
        self.staging.mkdir()
        runner = self._write_runner()
        entrypoint = self.staging / "forge-x-init"
        entrypoint.write_text("#!/bin/bash\nexit 127\n", encoding="utf-8")
        entrypoint.chmod(0o755)
        result = subprocess.run(
            [str(runner), str(self.staging), "forge-x-init", "binary",
             "Adventurer5M", "0023", "0"],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=4,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("unexpectedly during startup with status 127", result.stdout)
        self.assertIn("Firmware installer failed", result.stdout)
        self.assertIn("installer could not start", result.stdout)
        self.assertFalse(self.typer_log.exists())

    def test_runner_reports_when_previous_runtime_never_releases(self):
        self.staging.mkdir()
        self.runner_staging.mkdir()
        runner = self.runner_staging / "runner.sh"
        runner_source = INSTALL_IMAGE_RUNNER.read_text(encoding="utf-8")
        runner_source = runner_source.replace(
            "FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS=30",
            "FIRMWARE_RUNTIME_RELEASE_TIMEOUT_SECONDS=2",
        )
        runner.write_text(runner_source, encoding="utf-8")
        runner.chmod(0o755)
        (self.staging / "forge-x-init.sh").write_text(
            "#!/bin/bash\necho unexpected > \"$RESULT_PATH\"\n",
            encoding="utf-8",
        )
        (self.staging / "forge-x-init.sh").chmod(0o755)
        fake_bin = self.root / "blocked-bin"
        fake_bin.mkdir()
        (fake_bin / "mount").write_text(
            "#!/bin/sh\necho '/dev/mock on /data/.mod/.forge-x type mock'\n",
            encoding="utf-8",
        )
        (fake_bin / "sleep").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
        (fake_bin / "lsof").write_text(
            "#!/bin/sh\nexit 0\n", encoding="utf-8")
        for helper in fake_bin.iterdir():
            helper.chmod(0o755)

        environment = dict(self.environment)
        environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
        result = subprocess.run(
            [str(runner), str(self.staging), "forge-x-init.sh", "shell",
             "Adventurer5M", "0023", "0"],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.result.exists())
        self.assertIn("Previous Forge-X runtime did not stop", result.stdout)
        self.assertIn("Firmware installer blocked", result.stdout)
        self.assertIn("Power off the printer", result.stdout)
        self.assertFalse(self.typer_log.exists())

    def test_gzip_compressed_tgz_is_rejected(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", "#!/bin/bash\nexit 0\n", 0o755),
        ], mode="w:gz")

        result = self._run(image)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Archive is corrupt", result.stdout)

    def test_space_failure_preserves_previous_staging_until_preflight_passes(self):
        image = self._archive("Adventurer5M-test.tgz", [
            ("forge-x-init.sh", "#!/bin/bash\nexit 0\n", 0o755),
        ])
        self.staging.mkdir()
        previous = self.staging / "previous-attempt"
        previous.write_text("keep", encoding="utf-8")
        fake_df = self.root / "df"
        fake_df.write_text(
            "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n'\n"
            "printf 'mock 100 100 0 100%% /mock\\n'\n",
            encoding="utf-8",
        )
        fake_df.chmod(0o755)
        installer = self._installer(
            "install-image-no-space.sh", suppress_parent_kill=True,
            extra_replacements={
                "FIRMWARE_INSTALL_DF=df":
                    "FIRMWARE_INSTALL_DF=%s" % fake_df,
            })

        result = self._run(image, installer)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("Not enough space on /data", result.stdout)
        self.assertEqual(result.stdout.count("Not enough space on /data"), 1)
        self.assertIn("@@ The printer can now be powered off.", result.stdout)
        self.assertIn("0 MB free", result.stdout)
        self.assertEqual(previous.read_text(encoding="utf-8"), "keep")

    def test_cleanup_removes_staging_after_ordinary_boot(self):
        self.staging.mkdir()
        (self.staging / "left-by-installer").write_text("payload", encoding="utf-8")
        self.runner_staging.mkdir()
        (self.runner_staging / "left-by-runner").write_text("payload", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(self.install_image), "cleanup"],
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.staging.exists())
        self.assertFalse(self.runner_staging.exists())


if __name__ == "__main__":
    unittest.main()
