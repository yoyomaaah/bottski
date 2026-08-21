"""Logging with secret redaction. Every logger in the app goes through setup()."""

from __future__ import annotations

import logging
import sys


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        # Longest first so partial overlaps redact fully.
        self._secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self._secrets:
            if s in msg:
                msg = msg.replace(s, "***REDACTED***")
        record.msg = msg
        record.args = ()
        return True


def setup(secrets: list[str], level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactingFilter(secrets))
    root.addHandler(handler)
    return root
