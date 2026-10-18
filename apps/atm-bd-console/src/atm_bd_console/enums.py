from enum import StrEnum


class AgentIcon(StrEnum):
    FABLE = "fable"
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"
    ASTRA = "astra"
    SOL = "sol"
    TERRA = "terra"
    LUNA = "luna"
    OTHER = "other"
    SEVERITY = "severity"
    QA_RUNNING = "qa_running"
    QA_PASS = "qa_pass"
    QA_FAIL = "qa_fail"


class SprintState(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    CLOSED = "closed"
    PINNED = "pinned"
    HOOKED = "hooked"
    ACCEPTED = "accepted"
    PLANNED = "planned"
    PROPOSED = "proposed"
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class CiState(StrEnum):
    NONE = "none"
    RUNNING = "running"
    PASS = "pass"
    FAIL_MACOS = "fail_macos"
    FAIL_WINDOWS = "fail_windows"
    FAIL_LINUX = "fail_linux"
    FAIL_BUG = "fail_bug"
    UNKNOWN = "unknown"


class PrState(StrEnum):
    NONE = "none"
    OPEN = "open"
    DRAFT = "draft"
    MERGED = "merged"
    CLOSED = "closed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class MergeState(StrEnum):
    CLEAN = "clean"
    CONFLICT = "conflict"
    ERROR = "error"
    UNKNOWN = "unknown"
