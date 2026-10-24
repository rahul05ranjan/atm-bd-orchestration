from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atm_bd_console.codec import from_json
from atm_bd_console.command import Output
from atm_bd_console.enums import SprintState
from atm_bd_console.error_text import error_text
from atm_bd_console.icons import SPRINT_ICONS
from atm_bd_console.result import CommandFailed, ConfigInvalid, Err, NotFound, Ok, Timeout
from atm_bd_console.status import DagPane, _fallback, status_text
from atm_bd_console.types import Snapshot, SprintView, StepView

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot() -> Snapshot:
    decoded = from_json(Snapshot, (FIXTURES / "contract" / "snapshot-running.json").read_text())
    assert isinstance(decoded, Ok)
    sprints = (
        SprintView("atm-bh-1", "contract", SprintState.CLOSED, (), ()),
        SprintView("atm-bh-2", "source", SprintState.IN_PROGRESS, ("atm-bh-1",),
                   (StepView("atm-bh-2.group-dev", "dev", SprintState.IN_PROGRESS, "fenix"),)),
        SprintView("atm-bh-3", "later", SprintState.NOT_READY, ("atm-bh-1", "atm-bh-2"), ()),
    )
    return replace(decoded.value, mermaid="graph TD\n    a --> b", sprints=sprints)


def test_mmdflux_output_is_returned_verbatim(fake_bin, monkeypatch, tmp_path) -> None:
    stdin_log = tmp_path / "mmdflux-stdin"
    monkeypatch.setenv("FAKE_STDIN_LOG", str(stdin_log))
    fake_bin.make(
        "mmdflux",
        'if [ "$1" = "--version" ]; then printf "mmdflux 2.6.1\\n"; exit 0; fi\n'
        'cat > "$FAKE_STDIN_LOG"\nprintf \'rendered\\n\'\n',
    )
    rendered, error = status_text(snapshot())
    assert rendered == "rendered\n" and error is None
    assert stdin_log.read_text() == "graph TD\n    a --> b"
    assert fake_bin.argv() == [("mmdflux", "--version"), ("mmdflux", "--format", "text")]


def test_old_mmdflux_returns_typed_fix_without_rendering(fake_bin) -> None:
    fake_bin.make("mmdflux", 'if [ "$1" = "--version" ]; then printf "mmdflux 2.6.0\\n"; exit 0; fi\n')

    rendered, error = status_text(snapshot())

    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")
    assert isinstance(error, ConfigInvalid)
    assert error.key == "mmdflux_version"
    assert error.value == "mmdflux 2.6.0"
    assert error.fix == "install or upgrade mmdflux >=2.6.1"
    assert fake_bin.argv() == [("mmdflux", "--version")]


def test_unversioned_mmdflux_returns_typed_fix_without_rendering(fake_bin) -> None:
    fake_bin.make("mmdflux", 'if [ "$1" = "--version" ]; then printf "unknown\\n"; exit 0; fi\n')

    _, error = status_text(snapshot())

    assert isinstance(error, ConfigInvalid)
    assert error.key == "mmdflux_version"
    assert error.fix == "install or upgrade mmdflux >=2.6.1"
    assert fake_bin.argv() == [("mmdflux", "--version")]


def test_missing_binary_uses_dependency_depth_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "")
    rendered, error = status_text(snapshot())
    assert isinstance(error, NotFound)
    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")


def test_render_failure_is_the_runners_command_failed(fake_bin) -> None:
    fake_bin.make(
        "mmdflux",
        'if [ "$1" = "--version" ]; then printf "mmdflux 2.6.1\\n"; exit 0; fi\n'
        'printf "bad diagram\\n" >&2\nexit 4\n',
    )

    rendered, error = status_text(snapshot())

    assert rendered == (FIXTURES / "render" / "status-fallback.txt").read_text().rstrip("\n")
    assert isinstance(error, CommandFailed)
    assert (error.kind, error.code, error.stderr) == ("command_failed", 4, "bad diagram")
    assert error.argv == ("mmdflux", "--format", "text")


def test_fallback_is_one_row_per_sprint_in_dependency_depth_order(monkeypatch) -> None:
    sprints = (
        SprintView("atm-bh-3", "later", SprintState.NOT_READY, ("atm-bh-2",), ()),
        SprintView("atm-bh-2", "source", SprintState.IN_PROGRESS, ("atm-bh-1",),
                   (StepView("atm-bh-2.group-dev", "dev", SprintState.IN_PROGRESS, "fenix"),)),
        SprintView("atm-bh-1", "contract", SprintState.CLOSED, (), ()),
        SprintView("atm-bh-4", "other", SprintState.READY, (),
                   (StepView("atm-bh-4.group-dev", "dev", SprintState.OPEN, None),)),
    )
    value = replace(snapshot(), sprints=sprints)
    monkeypatch.setenv("PATH", "")
    rendered, error = status_text(value)
    assert isinstance(error, NotFound)
    # Depth 0 first (bh-1 before bh-4 by declared order), then depth 1, then depth 2.
    assert rendered == "\U000f05e0 bh-1\n\U000f040d bh-4  dev: unassigned\n\U000f051f bh-2  dev: ·fenix\n\U000f137c bh-3  ← bh-2"


def test_fallback_renders_every_sprint_state() -> None:
    sprints = tuple(
        SprintView(f"atm-bi-{index}", state.value, state, (), ())
        for index, state in enumerate(SprintState)
    )

    rendered = _fallback(replace(snapshot(), sprints=sprints))

    assert [line.split()[0] for line in rendered.splitlines()] == [
        SPRINT_ICONS[state][0] for state in SprintState
    ]


def test_nonzero_and_timeout_return_error_and_fallback(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "bad"))
    rendered, error = status_text(snapshot())
    assert isinstance(error, CommandFailed) and error.code == 1
    assert rendered.startswith("\U000f05e0 bh-1")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 20)

    monkeypatch.setattr(subprocess, "run", timed_out)
    rendered, error = status_text(snapshot())
    assert isinstance(error, Timeout) and error.seconds == 20
    assert rendered.startswith("\U000f05e0 bh-1")


# DAG pane keeps the last good diagram (#533). A fake runner replaces mmdflux; the clock is a passed-in value.
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
PARSE_STDERR = "Error: parse error: Parse error at line 1, column 1: expected flowchart"


class FakeMmdflux:
    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv, fix, **kwargs):
        self.calls.append(tuple(argv))
        if argv[1] == "--version":
            return Ok(Output(0, "mmdflux 2.6.1\n"))
        return self.outcomes.pop(0)


def failing(code=1, stderr=PARSE_STDERR):
    return Err(CommandFailed("command_failed", ("mmdflux", "--format", "text"), code, stderr, ""))


def pane_show(pane, fake, value, now=NOW):
    return pane.show(value, now, lambda s: status_text(s, fake))


def test_good_then_failing_render_keeps_first_diagram_with_stale_marker_and_reason() -> None:
    fake = FakeMmdflux(Ok(Output(0, "DIAGRAM\n")), failing())
    pane = DagPane()
    assert pane_show(pane, fake, snapshot()) == ("DIAGRAM\n", None)

    text, error = pane_show(pane, fake, snapshot(), NOW + timedelta(minutes=4))

    assert text.startswith("DIAGRAM\n")
    assert "stale 4m" in text and PARSE_STDERR in text and "exit 1" in text
    assert isinstance(error, CommandFailed) and error.stderr == PARSE_STDERR


def test_empty_sprints_do_not_run_mmdflux_and_the_reason_is_the_beads_error() -> None:
    beads = Timeout("timeout", ("bd", "list"), 20, "retry the read")
    empty = replace(snapshot(), sprints=(), mermaid="graph TD", errors=(beads,))
    fake = FakeMmdflux()
    pane = DagPane()

    text, error = pane_show(pane, fake, empty)

    assert fake.calls == []
    assert error is beads
    assert text.startswith("no diagram yet") and "timeout" in text


def test_empty_sprints_without_a_read_error_say_there_is_no_phase_data() -> None:
    _, error = status_text(replace(snapshot(), sprints=(), mermaid="graph TD", errors=()), FakeMmdflux())
    assert isinstance(error, NotFound) and error.what == "phase data"


def test_empty_after_good_keeps_the_last_diagram_without_calling_mmdflux() -> None:
    fake = FakeMmdflux(Ok(Output(0, "DIAGRAM\n")))
    pane = DagPane()
    pane_show(pane, fake, snapshot())
    beads = Timeout("timeout", ("bd", "list"), 20, "retry the read")

    text, error = pane_show(pane, fake, replace(snapshot(), sprints=(), errors=(beads,)), NOW + timedelta(seconds=90))

    assert text.startswith("DIAGRAM\n") and "stale 1m" in text and error is beads
    assert fake.calls.count(("mmdflux", "--format", "text")) == 1


def test_mmdflux_failure_keeps_argv_code_stderr_logs_it_and_has_its_own_fix() -> None:
    logged: list = []
    fake = FakeMmdflux(failing())
    pane = DagPane(log=logged.append)

    _, error = pane_show(pane, fake, snapshot())

    assert isinstance(error, CommandFailed)
    assert (error.argv, error.code, error.stderr) == (("mmdflux", "--format", "text"), 1, PARSE_STDERR)
    assert "report it as a defect" in error.fix and "graph TD" in error.fix
    assert "correct the Mermaid source" not in error.fix
    assert logged == [error]


def test_timeout_fix_names_the_loaded_host_and_the_wait() -> None:
    fake = FakeMmdflux(Err(Timeout("timeout", ("mmdflux", "--format", "text"), 20, "")))
    _, error = status_text(snapshot(), fake)
    assert isinstance(error, Timeout) and "host is loaded" in error.fix and "20s" in error.fix


def test_first_tick_failure_is_a_labelled_fallback_never_empty() -> None:
    text, error = pane_show(DagPane(), FakeMmdflux(failing()), snapshot())
    assert error is not None
    assert text.startswith("no diagram yet, text fallback below")
    assert "bh-1" in text and "bh-2" in text


def test_recovery_clears_the_stale_marker() -> None:
    fake = FakeMmdflux(Ok(Output(0, "ONE\n")), failing(), Ok(Output(0, "TWO\n")))
    pane = DagPane()
    pane_show(pane, fake, snapshot())
    assert "stale" in pane_show(pane, fake, snapshot(), NOW + timedelta(minutes=1))[0]

    assert pane_show(pane, fake, snapshot(), NOW + timedelta(minutes=2)) == ("TWO\n", None)


def test_stale_reason_prints_a_many_id_bd_argv_as_a_count_and_the_error_keeps_the_full_argv() -> None:
    ids = tuple(f"atm-bk-{n}" for n in range(28))
    beads = Timeout("timeout", ("bd", "show", *ids), 20, "check host load")
    pane = DagPane()
    pane_show(pane, FakeMmdflux(Ok(Output(0, "DIAGRAM\n"))), snapshot())

    text, error = pane_show(pane, FakeMmdflux(), replace(snapshot(), sprints=(), errors=(beads,)), NOW + timedelta(minutes=2))

    assert "bd show (28 ids) timed out after 20s" in text and "atm-bk-0" not in text
    assert isinstance(error, Timeout)
    assert error is beads and error.argv == ("bd", "show", *ids)


def test_dag_reason_uses_the_shared_error_text_not_the_backticked_log_format() -> None:
    error = Timeout("timeout", ("gh", "api", "graphql"), 10, "retry")
    pane = DagPane()

    text, shown = pane_show(pane, FakeMmdflux(), replace(snapshot(), sprints=(), mermaid="graph TD", errors=(error,)))

    assert shown is error
    assert error_text(error) in text
    assert "`retry`" not in text
