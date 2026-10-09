## Behavioral contracts for packaged G-code upload dialogs.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import ast
import asyncio
import enum
import importlib.metadata
import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock


ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / ".root"))
sys.modules.setdefault("importlib_metadata", importlib.metadata)
from moonraker.components.klippy_connection import KlippyConnection
from moonraker.utils import ServerError

# inotify is Linux-only; these tests exercise uploads without filesystem watches.
inotify = types.ModuleType("inotify_simple")
inotify.INotify = mock.Mock()
inotify.flags = enum.IntFlag(
    "flags", "CREATE DELETE MODIFY MOVED_TO MOVED_FROM ONLYDIR CLOSE_WRITE")
with mock.patch.dict(sys.modules, {"inotify_simple": inotify}):
    from moonraker.components.file_manager import file_manager as fm

spec = importlib.util.spec_from_file_location(
    "upload_gcode_parser", ROOT / ".py/klipper/patches/gcode.py")
gcode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gcode)


class GCode3MFUploadTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = mock.Mock()
        self.server.error = ServerError
        self.connection = KlippyConnection.__new__(KlippyConnection)
        self.connection.writer = object()
        self.connection.closing = False
        self.connection.server = self.server
        self.connection._request_standard = mock.AsyncMock(side_effect=self.deliver)
        self.api = mock.Mock()
        self.api.klippy = self.connection
        self.api.run_gcode = mock.AsyncMock(side_effect=self.public_gcode)
        self.api.start_print = mock.AsyncMock()
        self.queue = mock.Mock()
        self.queue.queue_job = mock.AsyncMock()
        self.server.lookup_component.side_effect = {
            "klippy_apis": self.api, "job_queue": self.queue}.__getitem__
        self.messages = []
        self.events = []
        self.parser = gcode.GCodeDispatch(mock.Mock())
        self.parser.register_command("RESPOND", self.respond, when_not_ready=True)

        self.manager = fm.FileManager.__new__(fm.FileManager)
        self.manager.server = self.server
        self.manager.queue_gcodes = False
        self.manager._handle_operation_check = mock.Mock(return_value=True)
        self.manager._process_uploaded_file = mock.AsyncMock(return_value={"size": 3})
        self.metadata_wait = mock.AsyncMock(side_effect=self.metadata)
        self.manager.gcode_metadata = mock.Mock()
        self.manager.gcode_metadata.parse_metadata.return_value.wait = self.metadata_wait
        self.manager.fs_observer = mock.Mock()
        self.manager._sched_changed_event = mock.Mock(return_value={"action": "create_file"})
        self.upload = {
            "root": "gcodes", "filename": "folder/job.gcode",
            "source_filename": "job.gcode.3mf", "unzip_3mf": True,
            "unzip_ufp": False, "start_print": False, "is_link": False,
            "dest_path": "/gcodes/folder/job.gcode", "dir_path": "",
            "tmp_file_path": "/tmp/upload.mru", "plate_index": None,
            "user": object(),
        }

    def respond(self, command):
        self.assertEqual(command.get("TYPE"), "command")
        message = command.get("MSG")
        self.messages.append(message)
        self.events.append(message)

    async def deliver(self, request):
        self.assertEqual(request.get_endpoint(), "gcode/script")
        self.assertIs(request.get_subscribable(), self.api)
        self.parser._process_commands(request.get_args()["script"].splitlines(), need_ack=False)
        return "ok"

    async def public_gcode(self, script):
        from moonraker.common import WebRequest
        return await self.connection.request(WebRequest(
            "gcode/script", {"script": script}, transport=self.api))

    async def metadata(self):
        self.events.append("metadata")

    async def finish(self, **changes):
        self.upload.update(changes)
        return await self.manager._finish_gcode_upload(self.upload)

    def test_http_upload_handler_registers_plateindex(self):
        # Avoid importing application.py: it needs Tornado and streaming_form_data,
        # while this regression only checks multipart field registration.
        source = (ROOT / ".root/moonraker/components/application.py").read_text()
        module = ast.parse(source)
        handler = next(
            cls for cls in module.body
            if isinstance(cls, ast.ClassDef) and cls.name == "FileUploadHandler"
        )
        prepare = next(
            method for method in handler.body
            if isinstance(method, ast.AsyncFunctionDef) and method.name == "prepare"
        )
        assignments = [
            node for node in ast.walk(prepare)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and target.attr == "_targets"
                for target in node.targets
            )
        ]
        self.assertEqual(len(assignments), 1)
        field_names = [
            key.value for key in assignments[0].value.keys
            if isinstance(key, ast.Constant)
        ]
        self.assertIn("plateindex", field_names)

    async def test_multi_plate_upload_extracts_selected_plate_and_starts_print(self):
        self.manager._process_uploaded_file = fm.FileManager._process_uploaded_file.__get__(self.manager)
        self.server.get_event_loop.return_value.run_in_thread = mock.AsyncMock(
            side_effect=asyncio.to_thread)
        self.manager.get_path_info = mock.Mock(return_value={"size": 15})
        with tempfile.TemporaryDirectory() as tmp:
            self.manager.file_paths = {"gcodes": tmp}
            source = pathlib.Path(tmp) / "upload.mru"
            selected = b"; plate seven\nG28\n"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Metadata/plate_1.gcode", "; plate one\n")
                archive.writestr("Metadata/plate_7.gcode", selected)

            self.upload = self.manager._parse_upload_args({
                "filename": "job.gcode.3mf", "tmp_file_path": str(source),
                "plateindex": "7", "print": "true",
            })
            self.assertEqual(self.upload["plate_index"], "7")
            result = await self.finish()

            self.assertEqual((pathlib.Path(tmp) / "job.gcode").read_bytes(), selected)
            self.assertFalse(source.exists())
            self.assertTrue(result["print_started"])
            self.api.start_print.assert_awaited_once_with(
                "job.gcode", user=self.upload["user"])

    async def test_upload_stages_are_delivered_without_console_command_history(self):
        result = await self.finish()
        self.assertEqual(result, {
            "action": "create_file", "print_started": False, "print_queued": False})
        self.assertIn("action:prompt_text Extracting G-code from archive...", self.messages)
        self.assertIn("action:prompt_text Processing folder/job.gcode...", self.messages)
        self.assertEqual(self.messages[-1], "action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE)
        self.assertNotIn("action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE, self.messages[:-1])
        self.server.send_event.assert_not_called()
        self.api.run_gcode.assert_not_awaited()
        self.manager.gcode_metadata.parse_metadata.assert_called_once_with(
            "folder/job.gcode", {"size": 3})
        self.manager.fs_observer.on_item_create.assert_called_once_with(
            "gcodes", self.upload["dest_path"])

    async def test_filename_and_error_text_remain_valid_gcode(self):
        prompt = fm.GCode3MFUploadPrompt(
            self.server, 'job#1*2;"\\|\nM112.gcode.3mf')
        await prompt.stage("Processing #1*2;\nG28...")
        self.assertIn("action:prompt_show", self.messages)
        self.assertTrue(any("M112.gcode.3mf" in msg for msg in self.messages))
        await prompt.fail("Could not process #1*2", 'invalid "#*;\\|\narchive')
        self.assertEqual(self.messages[-1], "action:prompt_show")
        footer = next(msg for msg in self.messages if msg.startswith("action:prompt_footer_button "))
        label, command, style = footer.removeprefix("action:prompt_footer_button ").split("|")
        self.assertEqual((label, style), ("OK", "primary"))
        self.parser._process_commands([command], need_ack=False)
        self.assertEqual(self.messages[-1], "action:prompt_end")

    async def test_unavailable_dialog_does_not_fail_upload(self):
        self.connection.writer = None
        result = await self.finish()
        self.assertFalse(result["print_started"])
        self.metadata_wait.assert_awaited_once()
        self.connection._request_standard.assert_not_awaited()

    async def test_semicolon_in_filename_does_not_truncate_dialog_command(self):
        prompt = fm.GCode3MFUploadPrompt(self.server, "part;one.gcode.3mf")
        await prompt.stage("Preparing part;one...")
        self.assertIn("action:prompt_text part one.gcode.3mf", self.messages)
        self.assertIn("action:prompt_text Preparing part one...", self.messages)
        self.assertEqual(self.messages[-1], "action:prompt_show")

    async def test_dialog_transport_failure_does_not_fail_upload(self):
        self.connection._request_standard.side_effect = ServerError("Disconnected", 503)
        result = await self.finish()
        self.assertEqual(result["action"], "create_file")
        self.metadata_wait.assert_awaited_once()

    async def test_prompt_closes_before_print_and_never_closes_print_prompt(self):
        async def start(filename, user):
            self.assertEqual(self.messages[-1], "action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE)
            self.events.append("start")
            self.messages.append("action:prompt_begin Print preparation")
        self.api.start_print.side_effect = start
        result = await self.finish(start_print=True)
        self.assertTrue(result["print_started"])
        self.assertEqual(self.messages[-1], "action:prompt_begin Print preparation")
        self.assertLess(self.events.index("metadata"), self.events.index("start"))
        self.api.start_print.assert_awaited_once_with("folder/job.gcode", user=self.upload["user"])

    async def test_busy_printer_queues_file_and_closes_prompt(self):
        self.manager._handle_operation_check.return_value = False
        self.manager.queue_gcodes = True
        result = await self.finish(start_print=True)
        self.assertEqual((result["print_started"], result["print_queued"]), (False, True))
        self.api.start_print.assert_not_awaited()
        self.queue.queue_job.assert_awaited_once_with(
            "folder/job.gcode", check_exists=False, user=self.upload["user"])
        self.assertEqual(self.messages[-1], "action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE)

    async def test_failed_start_falls_back_to_queue(self):
        self.api.start_print.side_effect = ServerError("Not ready", 503)
        self.manager.queue_gcodes = True
        result = await self.finish(start_print=True)
        self.assertTrue(result["print_queued"])
        self.queue.queue_job.assert_awaited_once()
        self.assertNotIn("action:prompt_begin " + fm.GCode3MFUploadPrompt.ERROR_TITLE, self.messages)

    async def test_failed_start_keeps_upload_success_and_shows_dismissible_error(self):
        self.api.start_print.side_effect = ServerError("Not ready", 503)
        result = await self.finish(start_print=True)
        self.assertEqual((result["print_started"], result["print_queued"]), (False, False))
        self.assertIn("action:prompt_text Print did not start. The file was saved.", self.messages)
        self.assertIn("action:prompt_text Not ready", self.messages)
        self.assertEqual(self.messages[-1], "action:prompt_show")

    async def test_busy_printer_without_queue_reports_saved_file(self):
        self.manager._handle_operation_check.return_value = False
        result = await self.finish(start_print=True)
        self.assertFalse(result["print_started"])
        self.assertIn("action:prompt_text Printer is busy or not ready.", self.messages)
        self.api.start_print.assert_not_awaited()

    async def test_preparation_errors_propagate_and_leave_dismissible_prompt(self):
        for failure in ("extract", "metadata", "queue"):
            with self.subTest(failure=failure):
                self.setUp()
                error = ServerError("Preparation failed", 400)
                if failure == "extract":
                    self.manager._process_uploaded_file.side_effect = error
                elif failure == "metadata":
                    self.metadata_wait.side_effect = error
                else:
                    self.manager.queue_gcodes = True
                    self.manager._handle_operation_check.return_value = False
                    self.upload["start_print"] = True
                    self.queue.queue_job.side_effect = error
                with self.assertRaises(ServerError) as caught:
                    await self.finish()
                self.assertIs(caught.exception, error)
                self.assertIn("action:prompt_text Preparation failed", self.messages)
                self.assertEqual(self.messages[-1], "action:prompt_show")

    async def test_rejected_overwrite_does_not_replace_active_printer_dialog(self):
        self.manager._handle_operation_check.side_effect = ServerError("File loaded", 403)
        with self.assertRaisesRegex(ServerError, "upload not permitted") as caught:
            await self.finish()
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(self.messages, [])
        self.manager._process_uploaded_file.assert_not_awaited()

    async def test_cancelled_preparation_closes_prompt_and_propagates_cancellation(self):
        self.metadata_wait.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.finish()
        self.assertEqual(self.messages[-1], "action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE)
        self.api.start_print.assert_not_awaited()
        self.manager.fs_observer.on_item_create.assert_not_called()

    async def test_regular_gcode_upload_keeps_existing_flow_without_dialog(self):
        result = await self.finish(unzip_3mf=False, start_print=True)
        self.assertTrue(result["print_started"])
        self.assertEqual(self.messages, [])

    async def test_cancelled_first_delivery_cleans_up_possibly_visible_prompt(self):
        async def deliver_then_cancel(request):
            await self.deliver(request)
            self.connection._request_standard.side_effect = self.deliver
            raise asyncio.CancelledError()

        self.connection._request_standard.side_effect = deliver_then_cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.finish()
        self.assertEqual(self.messages[-1], "action:prompt_end " + fm.GCode3MFUploadPrompt.TITLE)
        self.manager._process_uploaded_file.assert_not_awaited()

    async def test_invalid_archive_preserves_http_400_and_destination(self):
        self.manager._process_uploaded_file = fm.FileManager._process_uploaded_file.__get__(self.manager)
        self.server.get_event_loop.return_value.run_in_thread = mock.AsyncMock(side_effect=asyncio.to_thread)
        with tempfile.TemporaryDirectory() as tmp:
            source = pathlib.Path(tmp) / "upload.mru"
            dest = pathlib.Path(tmp) / "job.gcode"
            dest.write_bytes(b"existing\n")
            for contents, message in ((b"not a zip", "Invalid G-code 3MF"), (None, "MD5 mismatch")):
                with self.subTest(message=message):
                    if contents is not None:
                        source.write_bytes(contents)
                    else:
                        with zipfile.ZipFile(source, "w") as archive:
                            archive.writestr("Metadata/plate_1.gcode", "G28\n")
                            archive.writestr("Metadata/plate_1.gcode.md5", "0" * 32)
                    with self.assertRaisesRegex(ServerError, message) as caught:
                        await self.finish(tmp_file_path=str(source), dest_path=str(dest))
                    self.assertEqual(caught.exception.status_code, 400)
                    self.assertEqual(dest.read_bytes(), b"existing\n")
                    self.assertEqual(self.messages[-1], "action:prompt_show")

    async def test_parsed_archive_upload_saves_gcode_and_cleans_temporary_file(self):
        self.manager._process_uploaded_file = fm.FileManager._process_uploaded_file.__get__(self.manager)
        self.server.get_event_loop.return_value.run_in_thread = mock.AsyncMock(side_effect=asyncio.to_thread)
        self.manager.get_path_info = mock.Mock(return_value={"size": 4})
        with tempfile.TemporaryDirectory() as tmp:
            self.manager.file_paths = {"gcodes": tmp}
            source = pathlib.Path(tmp) / "upload.mru"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Metadata/plate_1.gcode", "G28\n")
            self.upload = self.manager._parse_upload_args({
                "filename": "job.gcode.3mf", "tmp_file_path": str(source)})
            result = await self.finish()
            self.assertEqual((pathlib.Path(tmp) / "job.gcode").read_bytes(), b"G28\n")
            self.assertFalse(source.exists())
            self.assertEqual(result["action"], "create_file")
            self.assertIn("action:prompt_text job.gcode.3mf", self.messages)
            self.manager.gcode_metadata.parse_metadata.assert_called_once_with("job.gcode", {"size": 4})


if __name__ == "__main__":
    unittest.main()
