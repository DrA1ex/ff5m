"""Regression tests for Moonraker upload error classification."""

import ast
import asyncio
import logging
import os
import pathlib
import shutil
import types
import unittest


ROOT = pathlib.Path(__file__).parents[1]
FILE_MANAGER = (
    ROOT / ".root" / "moonraker" / "components" / "file_manager" /
    "file_manager.py"
)


class ServerError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class GCode3MFError(Exception):
    pass


class EventLoop:
    async def run_in_thread(self, callback, *args):
        return callback(*args)


class Server:
    error = ServerError

    def get_event_loop(self):
        return EventLoop()


class Manager:
    server = Server()
    file_paths = {}

    def get_path_info(self, path, root):
        return {"path": path, "root": root}


def load_process_uploaded_file(gcode_3mf, shutil_module=shutil):
    source = FILE_MANAGER.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(FILE_MANAGER))
    file_manager = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "FileManager"
    )
    method = next(
        node for node in file_manager.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_process_uploaded_file"
    )
    future = ast.ImportFrom(
        module="__future__",
        names=[ast.alias(name="annotations")],
        level=0,
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[future, method], type_ignores=[]
    ))
    namespace = {
        "ServerError": ServerError,
        "asyncio": asyncio,
        "gcode_3mf": gcode_3mf,
        "logging": logging,
        "os": os,
        "shutil": shutil_module,
    }
    exec(compile(module, str(FILE_MANAGER), "exec"), namespace)
    return namespace["_process_uploaded_file"]


def upload_info(**overrides):
    info = {
        "dest_path": "/tmp/part.gcode",
        "dir_path": "",
        "is_link": False,
        "plate_index": None,
        "root": "gcodes",
        "tmp_file_path": "/tmp/part.gcode.3mf",
        "unzip_3mf": True,
        "unzip_ufp": False,
    }
    info.update(overrides)
    return info


class FileManagerUploadTest(unittest.TestCase):
    def test_invalid_gcode_3mf_preserves_specific_bad_request(self):
        def fail_extract(*args):
            raise GCode3MFError("plateindex is required")

        gcode_3mf = types.SimpleNamespace(
            GCode3MFError=GCode3MFError,
            extract_gcode_3mf=fail_extract,
        )
        process_upload = load_process_uploaded_file(gcode_3mf)

        with self.assertRaises(ServerError) as raised:
            asyncio.run(process_upload(Manager(), upload_info()))

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(str(raised.exception), "plateindex is required")

    def test_unexpected_write_failure_remains_internal_server_error(self):
        def fail_move(*args):
            raise OSError("disk failure")

        gcode_3mf = types.SimpleNamespace(
            GCode3MFError=GCode3MFError,
            extract_gcode_3mf=lambda *args: None,
        )
        failing_shutil = types.SimpleNamespace(move=fail_move)
        process_upload = load_process_uploaded_file(
            gcode_3mf, shutil_module=failing_shutil
        )

        with self.assertLogs(level="ERROR") as logged:
            with self.assertRaises(ServerError) as raised:
                asyncio.run(process_upload(
                    Manager(), upload_info(unzip_3mf=False)
                ))

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(str(raised.exception), "Unable to save file")
        self.assertIn("Upload Write Error", logged.output[0])


if __name__ == "__main__":
    unittest.main()
