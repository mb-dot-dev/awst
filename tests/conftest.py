"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _aws_test_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fake AWS credentials and isolated config files so no test can ever touch a real account."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    # setenv-then-delenv registers the original (absent) value with monkeypatch, so any
    # AWS_PROFILE the code under test sets is removed again at teardown.
    monkeypatch.setenv("AWS_PROFILE", "scrubbed")
    monkeypatch.delenv("AWS_PROFILE")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "scrubbed")
    monkeypatch.delenv("AWS_DEFAULT_PROFILE")
