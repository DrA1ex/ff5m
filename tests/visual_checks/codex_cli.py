## Host-side screenshot review through the installed Codex CLI.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Invoke codex exec with shared image-review prompts and JSON schemas."""

import base64
from concurrent.futures import CancelledError
import json
import os
import pathlib
import shutil
import signal
import subprocess
import tempfile
import time

from .openai_compatible import MAX_RESPONSE_BYTES, TransportFailure


def _executable(command):
    path = shutil.which(command)
    if path is None:
        raise TransportFailure("service_unavailable", "Codex CLI executable was not found")
    # The review runs in a temporary directory, so relative host paths need
    # resolution before changing the child's working directory.
    return str(pathlib.Path(path).absolute())


def _run(command, prompt, timeout, directory, cancelled=None):
    if cancelled is not None and cancelled.is_set():
        raise CancelledError("Codex screenshot review cancelled")
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=directory, start_new_session=True)
    except OSError:
        raise TransportFailure("service_unavailable", "Cannot start the Codex CLI")

    try:
        deadline = time.monotonic() + timeout
        pending_input = prompt.encode("utf-8")
        while True:
            if cancelled is not None and cancelled.is_set():
                raise CancelledError("Codex screenshot review cancelled")
            remaining = max(0.0, deadline - time.monotonic())
            try:
                process.communicate(
                    input=pending_input,
                    timeout=min(remaining, 0.2) if cancelled is not None else remaining)
                break
            except subprocess.TimeoutExpired:
                # communicate resumes its existing stdin write after a timeout.
                pending_input = None
                if cancelled is None or time.monotonic() >= deadline:
                    raise
    except subprocess.TimeoutExpired:
        raise TransportFailure("service_unavailable", "Codex screenshot review timed out")
    finally:
        # Also stop owned child processes when the run times out or is cancelled.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        if process.stdin is not None:
            process.stdin.close()

    if process.returncode:
        # CLI logs may contain credentials or local config; never copy them into reports.
        raise TransportFailure(
            "request_failed", "Codex CLI exited with code %d; check Codex login and model access"
            % process.returncode)


class CodexCLI:
    def __init__(self, settings, cancelled=None):
        self.settings = settings
        self.cancelled = cancelled

    def models(self):
        # CLI owns authentication and model discovery. Preflight only its availability.
        _executable(self.settings.codex_command)
        return {"data": [{"id": self.settings.model}]}

    def complete(self, payload):
        executable = _executable(self.settings.codex_command)
        with tempfile.TemporaryDirectory(prefix="ff5m-visual-codex-") as temporary:
            directory = pathlib.Path(temporary)
            schema = directory / "schema.json"
            answer = directory / "answer.json"
            schema.write_text(json.dumps(
                payload["response_format"]["json_schema"]["schema"]), encoding="utf-8")
            command = [
                executable, "exec",
                "--model", payload["model"],
                "--config", "model_reasoning_effort=" + json.dumps(self.settings.reasoning_effort),
                "--sandbox", "read-only", "--config", 'approval_policy="never"',
                "--ignore-user-config", "--config", "project_doc_max_bytes=0",
                "--ephemeral", "--skip-git-repo-check", "--color", "never",
                "--output-schema", str(schema), "--output-last-message", str(answer),
            ]
            prompt = [
                "Inspect only the supplied screenshots and return the requested JSON. "
                "Do not use tools, read project files, run commands, or contact devices."]
            image_count = 0
            for message in payload["messages"]:
                if isinstance(message["content"], str):
                    prompt.append(message["content"])
                    continue
                for part in message["content"]:
                    if part["type"] == "text":
                        prompt.append(part["text"])
                    elif part["type"] == "image_url":
                        image_count += 1
                        header, encoded = part["image_url"]["url"].split(",", 1)
                        extension = {
                            "data:image/png;base64": ".png",
                            "data:image/jpeg;base64": ".jpg",
                            "data:image/webp;base64": ".webp",
                            "data:image/bmp;base64": ".bmp",
                        }[header]
                        image = directory / ("image-%d%s" % (image_count, extension))
                        image.write_bytes(base64.b64decode(encoded, validate=True))
                        command.extend(["--image", str(image)])
            command.append("-")
            _run(command, "\n\n".join(prompt), self.settings.timeout, directory, self.cancelled)
            try:
                with answer.open("rb") as stream:
                    raw = stream.read(MAX_RESPONSE_BYTES + 1)
            except OSError:
                raise ValueError("Codex CLI did not write a final JSON answer")
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("Codex final answer exceeds the size limit")
            try:
                return json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("Codex final answer is not valid JSON")
