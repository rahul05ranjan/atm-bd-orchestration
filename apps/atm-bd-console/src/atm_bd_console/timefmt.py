"""The two time texts every control shares: a local clock and a compact age."""
from __future__ import annotations

from datetime import datetime, tzinfo


def clock(value: datetime, *, seconds: bool = False, local_zone: tzinfo | None = None) -> str:
    """Format a timestamp in local time, optionally using an injected zone."""
    local_time = value.astimezone(local_zone) if local_zone is not None else value.astimezone()
    return local_time.strftime("%H:%M:%S" if seconds else "%H:%M")


def _age(age_s: int) -> str:
    age_s = max(0, age_s)
    if age_s < 60:
        return f"{age_s}s"
    if age_s < 3600:
        return f"{age_s // 60}m"
    if age_s < 86400:
        return f"{age_s // 3600}h{(age_s % 3600) // 60:02d}"
    return f"{age_s // 86400}d"
