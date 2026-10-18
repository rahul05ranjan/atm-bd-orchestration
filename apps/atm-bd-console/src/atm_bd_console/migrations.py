from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .ci import ci_failure_key


def migrate_snapshot_ci(value: Any, fields: dict[str, Any], metadata: Mapping[str, Any]) -> Any:
    legacy_ci = metadata["legacy_ci"]
    if not isinstance(value, str) or value not in legacy_ci:
        return value
    return legacy_ci[value].value


def migrate_pull_request_ci(value: Any, fields: dict[str, Any], metadata: Mapping[str, Any]) -> Any:
    legacy_ci = metadata["legacy_ci"]
    if not isinstance(value, str) or value not in legacy_ci:
        return value
    ci = legacy_ci[value]
    checks = fields.get("failed_checks", [])
    if value == "failure" and isinstance(checks, list) and all(isinstance(check, str) for check in checks):
        ci = ci_failure_key(tuple(checks))
    return ci.value
