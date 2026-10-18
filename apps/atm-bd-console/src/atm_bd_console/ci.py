from .enums import CiState

_FAILED_PLATFORMS = (
    (CiState.FAIL_MACOS, ("macos", "darwin")),
    (CiState.FAIL_WINDOWS, ("windows", "win-", "win_")),
    (CiState.FAIL_LINUX, ("ubuntu", "linux")),
)


def ci_failure_key(failed_checks: tuple[str, ...]) -> CiState:
    platforms: set[CiState] = set()
    for name in failed_checks:
        lowered = name.lower()
        platform = next((key for key, words in _FAILED_PLATFORMS if any(word in lowered for word in words)), None)
        if platform is None:
            return CiState.FAIL_BUG
        platforms.add(platform)
    return platforms.pop() if len(platforms) == 1 else CiState.FAIL_BUG
