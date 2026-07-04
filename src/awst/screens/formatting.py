"""Pure formatting helpers for presenting AWS data."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

_MINUTE = 60
_HOUR = 3600
_DAY = 86400


def relative_age(moment: datetime, now: datetime) -> str:
    """Render how long ago ``moment`` was, e.g. "2h ago"."""
    seconds = int((now - moment).total_seconds())
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{seconds // _MINUTE}m ago"
    if seconds < _DAY:
        return f"{seconds // _HOUR}h ago"
    return f"{seconds // _DAY}d ago"


def status_style(status: str) -> str:
    """Rich style for a CloudFormation stack status (rollbacks/failures win)."""
    if "ROLLBACK" in status or "FAILED" in status:
        return "red"
    if status.endswith("_IN_PROGRESS"):
        return "yellow"
    if status.endswith("_COMPLETE"):
        return "green"
    return ""
