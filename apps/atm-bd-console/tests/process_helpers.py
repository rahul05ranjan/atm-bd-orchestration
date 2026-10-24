from __future__ import annotations

import subprocess
import sys
from shutil import which

import pytest

PROCESS_TIMEOUT = 60
UV = which("uv")


def run_process(stage: str, argv, *, cwd=None, env=None, check: bool = True, text: bool = True):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=text, timeout=PROCESS_TIMEOUT,
                                check=False)
    except subprocess.TimeoutExpired as error:
        pytest.fail(f"{stage}: command {argv!r} timed out after {PROCESS_TIMEOUT}s; "
                    f"stdout={error.stdout!r}; stderr={error.stderr!r}", pytrace=False)
    if check and result.returncode:
        pytest.fail(f"{stage}: command {argv!r} exited {result.returncode}; "
                    f"stdout={result.stdout!r}; stderr={result.stderr!r}", pytrace=False)
    return result


def build_command(*args: str) -> list[str]:
    assert UV is not None
    return [UV, "build", "--offline", "--no-python-downloads", "--no-build-isolation",
            "--python", sys.executable, *args]
