import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUTHORIZATION = ROOT / ".root/moonraker/components/authorization.py"


def load_hostname_validator():
    tree = ast.parse(AUTHORIZATION.read_text(encoding="utf-8"))
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_is_valid_hostname"
    )
    namespace = {}
    exec(compile(
        ast.Module(body=[helper], type_ignores=[]),
        str(AUTHORIZATION),
        "exec",
    ), namespace)
    return namespace["_is_valid_hostname"]


class TrustedHostnameValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.is_valid = staticmethod(load_hostname_validator())

    def test_accepts_existing_trusted_client_syntax(self):
        for value in (
            "printer.local",
            "a-b.example",
            "1printer.example",
            "localhost",
            "printer.12ab",
        ):
            with self.subTest(value=value):
                self.assertTrue(self.is_valid(value))

    def test_rejects_invalid_labels_and_suffixes(self):
        for value in (
            "",
            "ab",
            "-printer.local",
            "printer-.local",
            "printer..local",
            "a-aa",
            "Printer.local",
            "printer.c0m",
            "printer.x",
            "printer.local.",
        ):
            with self.subTest(value=value):
                self.assertFalse(self.is_valid(value))

    def test_accepts_long_enough_hyphen_component_before_suffix(self):
        self.assertTrue(self.is_valid("a-aaa"))

    def test_rejects_long_invalid_input_without_regex_backtracking(self):
        self.assertFalse(self.is_valid("0-" * 10_000 + "0"))


if __name__ == "__main__":
    unittest.main()
