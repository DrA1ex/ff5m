## Shared numeric input editing and validation.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

from decimal import Decimal, InvalidOperation
import math
import re


_INTEGER_RE = re.compile(r"^-?\d+$")
_DECIMAL_RE = re.compile(r"^-?(?:\d+(?:\.\d*)?|\.\d+)$")


class NumericInputSpec:
    """Portable editing rules for an on-screen numeric keypad.

    Character-level editing accepts incomplete values such as ``-`` or
    ``12.``. ``parse`` performs the final completeness and range checks.
    """

    MODES = ("integer", "decimal")

    def __init__(self, mode="decimal", minimum=None, maximum=None,
                 max_length=10, fraction_digits=None):
        mode = str(mode).lower()
        if mode not in self.MODES:
            raise ValueError("Numeric input mode must be integer or decimal")
        self.mode = mode
        self.minimum = self._constraint(minimum, "minimum")
        self.maximum = self._constraint(maximum, "maximum")
        if (self.minimum is not None and self.maximum is not None
                and self.minimum > self.maximum):
            raise ValueError("Numeric input minimum exceeds maximum")
        self.max_length = int(max_length)
        if self.max_length < 1:
            raise ValueError("Numeric input max_length must be positive")
        if fraction_digits is None:
            self.fraction_digits = None
        else:
            self.fraction_digits = int(fraction_digits)
            if self.fraction_digits < 0:
                raise ValueError(
                    "Numeric input fraction_digits must be non-negative")
        if self.mode == "integer" and self.fraction_digits not in (None, 0):
            raise ValueError("Integer input cannot have fractional digits")

    @staticmethod
    def _constraint(value, name):
        if value is None:
            return None
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError("Numeric input %s must be finite" % name)
        if not result.is_finite():
            raise ValueError("Numeric input %s must be finite" % name)
        return result

    @property
    def allows_decimal(self):
        return self.mode == "decimal" and self.fraction_digits != 0

    @property
    def allows_negative(self):
        return self.minimum is None or self.minimum < 0

    def apply(self, text, token):
        """Apply one keypad token, returning unchanged text when disallowed."""
        value = str(text or "")
        token = {"dot": "decimal", "back": "backspace"}.get(
            str(token), str(token))
        if token == "backspace":
            return value[:-1]
        if token == "sign":
            if not self.allows_negative:
                return value
            if value.startswith("-"):
                return value[1:]
            return "-" + value if len(value) < self.max_length else value
        if token == "decimal":
            if not self.allows_decimal or "." in value:
                return value
            candidate = (value + "." if value not in ("", "-")
                         else ("-0." if value == "-" else "0."))
            return candidate if len(candidate) <= self.max_length else value
        if len(token) != 1 or not token.isdigit():
            return value
        if len(value) >= self.max_length:
            return value
        if "." in value and self.fraction_digits is not None:
            fraction = value.split(".", 1)[1]
            if len(fraction) >= self.fraction_digits:
                return value
        return value + token

    def parse(self, text):
        value = str(text or "").strip().replace(",", ".")
        if len(value) > self.max_length:
            raise ValueError(
                "Value must use at most %d characters" % self.max_length)
        pattern = _INTEGER_RE if self.mode == "integer" else _DECIMAL_RE
        if pattern.match(value) is None:
            raise ValueError(
                "Enter a whole number" if self.mode == "integer"
                else "Enter a decimal number")
        try:
            number = Decimal(value)
        except InvalidOperation:
            raise ValueError("Enter a finite number")
        if not number.is_finite():
            raise ValueError("Enter a finite number")
        if self.fraction_digits is not None and "." in value:
            if len(value.split(".", 1)[1]) > self.fraction_digits:
                raise ValueError(
                    "Use at most %d digits after the decimal point" %
                    self.fraction_digits)
        if self.minimum is not None and number < self.minimum:
            raise ValueError("Value must be at least %s" % self.minimum)
        if self.maximum is not None and number > self.maximum:
            raise ValueError("Value must be at most %s" % self.maximum)
        if self.mode == "integer":
            return int(number)
        result = float(number)
        if not math.isfinite(result):
            raise ValueError("Enter a finite number")
        return result

    def is_valid(self, text):
        try:
            self.parse(text)
        except ValueError:
            return False
        return True
