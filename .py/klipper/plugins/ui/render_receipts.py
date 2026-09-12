"""Optional Typer render-commit receipts and one-in-flight tracking."""

import re
from collections import namedtuple


_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

RenderReceipt = namedtuple(
    "RenderReceipt", "token success total_us cpu_us flush_us")
PendingReceipt = namedtuple(
    "PendingReceipt", "token submitted_at deadline metadata")
ReceiptMeasurement = namedtuple(
    "ReceiptMeasurement",
    "receipt submitted_at received_at latency_ms metadata")


def validate_render_receipt_token(token):
    token = str(token)
    if _TOKEN_RE.match(token) is None:
        raise ValueError("invalid render receipt token")
    return token


def parse_render_receipt(line):
    fields = str(line).strip().split()
    if len(fields) != 6 or fields[0] != "render":
        return None
    token = fields[1]
    status = fields[2]
    if status not in ("ok", "failed"):
        return None
    try:
        validate_render_receipt_token(token)
        total_us, cpu_us, flush_us = (
            int(fields[3]), int(fields[4]), int(fields[5]))
    except (TypeError, ValueError):
        return None
    if min(total_us, cpu_us, flush_us) < 0 or flush_us > total_us:
        return None
    return RenderReceipt(
        token, status == "ok", total_us, cpu_us, flush_us)


class ReceiptTracker:
    """Track exactly one optional render receipt without queue semantics."""

    def __init__(self, timeout=1.0):
        timeout = float(timeout)
        if timeout <= 0.0:
            raise ValueError("receipt timeout must be positive")
        self.timeout = timeout
        self._pending = None

    @property
    def pending(self):
        return self._pending

    def expect(self, token, submitted_at, metadata=None):
        if self._pending is not None:
            raise RuntimeError("a render receipt is already pending")
        token = validate_render_receipt_token(token)
        submitted_at = float(submitted_at)
        self._pending = PendingReceipt(
            token, submitted_at, submitted_at + self.timeout, metadata)
        return token

    def resolve(self, receipt, received_at):
        pending = self._pending
        if pending is None or receipt.token != pending.token:
            return None
        self._pending = None
        received_at = float(received_at)
        return ReceiptMeasurement(
            receipt, pending.submitted_at, received_at,
            max(0.0, received_at - pending.submitted_at) * 1000.0,
            pending.metadata)

    def expired(self, eventtime):
        pending = self._pending
        return pending is not None and float(eventtime) >= pending.deadline

    def cancel(self):
        pending = self._pending
        self._pending = None
        return pending
