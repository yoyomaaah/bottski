import logging

from bottski.log import RedactingFilter


def _emit(filter_: RedactingFilter, msg: str, *args) -> str:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    filter_.filter(record)
    return record.getMessage()


def test_secret_never_survives_plain_message():
    f = RedactingFilter(["sk_super_secret"])
    assert "sk_super_secret" not in _emit(f, "key is sk_super_secret ok")


def test_secret_never_survives_formatted_args():
    f = RedactingFilter(["sk_super_secret"])
    assert "sk_super_secret" not in _emit(f, "key is %s", "sk_super_secret")


def test_empty_secrets_are_ignored():
    f = RedactingFilter(["", "abc"])
    assert _emit(f, "hello abc") == "hello ***REDACTED***"
