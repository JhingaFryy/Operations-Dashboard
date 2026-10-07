"""Logging levels for this service.

This API logs to stdout and systemd captures it into the journal, so there is no application log
file and no application-owned rotation here - journald's retention is the single owner.

The levels matter anyway: SQLAlchemy emits every statement it runs whenever its logger is
INFO-enabled, which is what grew BL-DCMS's logs by ~6 GB in two days. SQLAlchemy's own default is
WARNING, but nothing stopped a future `echo=True`/dictConfig from lifting it, so configure_logging()
pins it and the application stays at INFO.
"""

import logging

import pytest

from app.core.logging_config import SQLALCHEMY_LOGGERS, configure_logging, sqlalchemy_level


@pytest.fixture(autouse=True)
def _restore_levels():
    before = {name: logging.getLogger(name).level for name in ("",) + SQLALCHEMY_LOGGERS}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


def test_sqlalchemy_is_pinned_to_warning(monkeypatch):
    monkeypatch.delenv("SQL_ECHO_LEVEL", raising=False)
    configure_logging()
    for name in SQLALCHEMY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING, name
    assert logging.getLogger("sqlalchemy.engine.Engine").isEnabledFor(logging.INFO) is False


def test_application_logging_stays_at_info(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    configure_logging()
    assert logging.getLogger().level == logging.INFO
    assert logging.getLogger("app.services").isEnabledFor(logging.INFO) is True


def test_errors_and_warnings_are_never_suppressed(monkeypatch, caplog):
    monkeypatch.delenv("SQL_ECHO_LEVEL", raising=False)
    configure_logging()
    with caplog.at_level(logging.WARNING, logger="sqlalchemy.engine"):
        logging.getLogger("sqlalchemy.engine").error("connection invalidated")
    assert "connection invalidated" in caplog.text


def test_sql_logging_is_opt_in_for_development(monkeypatch):
    monkeypatch.setenv("SQL_ECHO_LEVEL", "INFO")
    assert sqlalchemy_level() == logging.INFO
    configure_logging()
    assert logging.getLogger("sqlalchemy.engine.Engine").isEnabledFor(logging.INFO) is True


def test_log_level_is_configurable(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    configure_logging()
    assert logging.getLogger().level == logging.WARNING
