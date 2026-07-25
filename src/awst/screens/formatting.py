"""Pure formatting helpers for presenting AWS data."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

_MINUTE = 60
_HOUR = 3600
_DAY = 86400
_KIB = 1024
_MASK = "•" * 8


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


def mask_value(value: str, param_type: str, *, revealed: bool) -> str:
    """Hide a SecureString value behind a fixed-width mask unless it has been revealed.

    The mask's width is constant so it does not leak the secret's length.
    """
    if param_type == "SecureString" and not revealed:
        return _MASK
    return value


def status_style(status: str) -> str:
    """Rich style for a CloudFormation stack status (rollbacks/failures win)."""
    if "ROLLBACK" in status or "FAILED" in status:
        return "red"
    if status.endswith("_IN_PROGRESS"):
        return "yellow"
    if status.endswith("_COMPLETE"):
        return "green"
    return ""


def human_size(size: int) -> str:
    """Render a byte count for humans, e.g. "1.5 KB"."""
    if size < _KIB:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= _KIB
        if round(value, 1) < _KIB or unit == "PB":
            return f"{value:.1f} {unit}"
    return f"{value / _KIB:.1f} PB"
