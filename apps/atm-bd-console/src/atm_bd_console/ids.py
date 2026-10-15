from __future__ import annotations


def short_id(bead_id: str, prefix: str) -> str:
    """The bead id without its `<prefix>-` root prefix; an id without that prefix is returned unchanged."""
    return bead_id.removeprefix(f"{prefix}-")
