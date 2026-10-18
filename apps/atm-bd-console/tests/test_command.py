"""command.run: the one runner git_source, github_source and beads_source share."""
from __future__ import annotations

import ast
import os
from pathlib import Path

from atm_bd_console import command
from atm_bd_console.result import CommandFailed, Err, Ok, Timeout

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "atm_bd_console"


def test_success_returns_code_and_stdout(fake_bin, tmp_path) -> None:
    fake_bin.make("tool", 'printf "%s" "$PWD"\n')
    assert command.run(("tool", "a"), "fix it", cwd=tmp_path) == Ok(command.Output(0, str(tmp_path)))
    assert fake_bin.argv() == [("tool", "a")]


def test_failure_carries_stripped_stderr_and_the_callers_fix(fake_bin) -> None:
    fake_bin.make("tool", 'printf "out\\n"\nprintf "  broken\\n" >&2\nexit 3\n')
    assert command.run(("tool",), "fix it") == Err(CommandFailed("command_failed", ("tool",), 3, "broken", "fix it"))


def test_failure_without_stderr_carries_stdout(fake_bin) -> None:
    fake_bin.make("tool", 'printf "{\\"message\\": \\"Not Found\\"}\\n"\nexit 1\n')
    result = command.run(("tool",), "fix it")
    assert isinstance(result, Err) and result.error.stderr == '{"message": "Not Found"}'


def test_ok_codes_accept_a_nonzero_exit(fake_bin) -> None:
    fake_bin.make("tool", 'printf "conflict"\nexit 1\n')
    assert command.run(("tool",), "fix it", ok_codes=(0, 1)) == Ok(command.Output(1, "conflict"))


def test_timeout_is_typed_with_the_callers_seconds(fake_bin) -> None:
    fake_bin.make("tool", "exec sleep 3600\n")
    assert command.run(("tool",), "fix it", timeout_s=1) == Err(Timeout("timeout", ("tool",), 1, "fix it"))


def test_missing_program_is_command_failed_127(fake_bin) -> None:
    result = command.run(("no-such-tool",), "install it")
    assert isinstance(result, Err) and isinstance(result.error, CommandFailed)
    assert (result.error.code, result.error.fix) == (127, "install it")


def test_stdin_and_env_reach_the_program(fake_bin, monkeypatch, tmp_path) -> None:
    stdin_log = tmp_path / "stdin"
    monkeypatch.setenv("FAKE_STDIN_LOG", str(stdin_log))
    fake_bin.make("tool", 'printf "%s" "$ATM_IDENTITY"\ncat > "$FAKE_STDIN_LOG"\n')
    result = command.run(("tool",), "fix it", stdin="piped text", env={**os.environ, "ATM_IDENTITY": "cipher"})
    assert result == Ok(command.Output(0, "cipher"))
    assert stdin_log.read_text() == "piped text"


def test_git_github_beads_status_and_atm_sources_run_only_through_command() -> None:
    for name in ("git_source.py", "github_source.py", "beads_source.py", "status.py", "atm_source.py"):
        tree = ast.parse((PACKAGE / name).read_text())
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert "subprocess" not in imported, name
        assert any(isinstance(node, ast.ImportFrom) and any(a.name == "command" for a in node.names)
                   for node in ast.walk(tree)), name
