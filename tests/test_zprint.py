"""Offline contract tests for stock printer LAN API calls (no printer needed)."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".shell/commands/zprint.sh"


class ZPrintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.capture = self.dir / "request.json"
        self.fake_curl = self.dir / "curl"
        self.fake_curl.write_text("""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['MOCK_CAPTURE'], 'w') as f:
    json.dump({'url': next(a for a in args if a.startswith('http://')),
               'data': args[args.index('-d') + 1], 'args': args}, f)
print(os.environ.get('MOCK_RESPONSE', '{"code":0,"message":"Success"}'))
sys.exit(int(os.environ.get('MOCK_EXIT', '0')))
""")
        self.fake_curl.chmod(0o755)
        self.config = self.dir / "config.json"
        self.config.write_text(json.dumps({"printerSerialNumber": "SERIAL-1", "lanCode": "CODE-1"}, indent=2))
        self.gcode = self.dir / 'quoted "model".gcode'
        self.gcode.write_text('M109 S220\nM190 S60\n')
        self.env = dict(os.environ, ZPRINT_IP="127.0.0.1", ZPRINT_CURL=str(self.fake_curl),
                        ZPRINT_CONFIG_FILE=str(self.config), ZPRINT_DATA_DIR=str(self.dir),
                        ZPRINT_GCODE_INPUT=str(self.dir / 'printer'), MOCK_CAPTURE=str(self.capture))

    def run_cmd(self, command, filename="ignored", **env):
        return subprocess.run(["bash", str(SCRIPT), command, filename],
                              env=dict(self.env, **env), text=True, capture_output=True)

    def capture_request(self):
        data = json.loads(self.capture.read_text())
        return data["url"], json.loads(data["data"]), data["args"]

    def test_close_success(self):
        res = self.run_cmd("CLOSE")
        self.assertEqual(res.returncode, 0, res.stderr)
        url, data, args = self.capture_request()
        self.assertEqual(url, "http://127.0.0.1:8898/control")
        self.assertEqual(data["payload"], {"cmd": "stateCtrl_cmd", "args": {"action": "setClearPlatform"}})
        self.assertEqual(data["serialNumber"], "SERIAL-1")
        self.assertIn("--max-time", args)
        self.assertIn("--connect-timeout", args)

    def test_print_escapes_filename(self):
        res = self.run_cmd("PRINT", self.gcode.name)
        self.assertEqual(res.returncode, 0, res.stderr)
        url, data, _ = self.capture_request()
        self.assertEqual(url, "http://127.0.0.1:8898/printGcode")
        self.assertEqual(data["fileName"], self.gcode.name)
        self.assertIs(data["levelingBeforePrint"], True)

    def test_api_errors_are_not_success(self):
        for response in ['{"code":-2,"message":"Lan mode error"}',
                         '{"code":1,"message":"Access code is different"}',
                         'invalid JSON', '{"message":"Success"}']:
            with self.subTest(response=response):
                result = self.run_cmd("CLOSE", MOCK_RESPONSE=response)
                self.assertNotEqual(result.returncode, 0)

    def test_connection_failure(self):
        res = self.run_cmd("CLOSE", MOCK_EXIT="7")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("connection error", res.stderr)

    def test_missing_credentials(self):
        self.config.write_text('{}')
        res = self.run_cmd("CLOSE")
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(self.capture.exists())

    def test_missing_print_file(self):
        res = self.run_cmd("PRINT", "missing.gcode")
        self.assertNotEqual(res.returncode, 0)
        self.assertFalse(self.capture.exists())


if __name__ == '__main__':
    unittest.main()
