"""Real phase settings and installed assignment files for cross-skill tests."""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

TEMPLATE_DIRECTORY = Path(".claude/skills/atm-bd-orchestration/templates")
TEMPLATE_NAMES = {
    "dev": "dev-template.xml.j2",
    "fix": "fix-assignment.xml.j2",
    "sanity": "dev-sanity-template.xml.j2",
    "qa": "qa-template.xml.j2",
    "plan_review": "plan-review-template.xml.j2",
    "review": "review-template.xml.j2",
}


def run_subprocess(argv, *, on_timeout=None, **kwargs):
    try:
        return subprocess.run(argv, timeout=60, **kwargs)
    except subprocess.TimeoutExpired as error:
        diagnostic = on_timeout() if on_timeout else ""
        raise AssertionError(f"{argv!r} timed out; stdout: {error.stdout!r}; stderr: {error.stderr!r}{diagnostic}") from error


def phase_data(phase="t", prefix="x", plans_dir="plans", lead="phase-lead"):
    return {
        "plan": f"{plans_dir}/phase-{phase}.jsonl",
        "root": f"{prefix}-phase-{phase}",
        "integration_branch": f"integrate/phase-{phase}",
        "lead": lead,
        "templates": {key: str(TEMPLATE_DIRECTORY / name) for key, name in TEMPLATE_NAMES.items()},
    }


def phase_toml(data):
    scalars = "".join(f"{key} = {json.dumps(value)}\n" for key, value in data.items() if key != "templates")
    if "templates" not in data:
        return scalars
    return scalars + "\n[templates]\n" + "".join(
        f"{key} = {json.dumps(value)}\n" for key, value in data["templates"].items())


def install_templates(repo):
    source = Path(__file__).resolve().parents[2] / "atm-bd-orchestration/templates"
    target = Path(repo) / TEMPLATE_DIRECTORY
    shutil.copytree(source, target, dirs_exist_ok=True)


def write_phase(repo, phase="t", prefix="x", plans_dir="plans", lead="phase-lead"):
    install_templates(repo)
    path = Path(repo) / ".atm-bd" / f"phase-{phase}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(phase_toml(phase_data(phase, prefix, plans_dir, lead)), encoding="utf-8")
    return path
SERVER_DIR = ".dolt-server"   # data directory and log of the workspace's own dolt sql-server

# Runs dolt as its child and stops it when the test process that started it is gone (killed, so class
# cleanup never ran): the server must not outlive the run.
# dolt logs this line once it is listening; it is the only readiness signal start_server waits for.
READY_MARK = "Server ready"
# The supervisor relays dolt's output to the workspace log and writes READY to its stdout once dolt is ready. It
# stops dolt when its stdin reaches EOF, which happens when the process that started it dies, and it exits only after
# dolt has exited, so its exit means the server is gone. It never polls.
SUPERVISOR = """\
import os, signal, subprocess, sys, threading
log_path, server = sys.argv[1], sys.argv[2:]
proc = subprocess.Popen(server, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
def terminate_server(*_):
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
signal.signal(signal.SIGTERM, terminate_server)
def stop_with_parent():
    sys.stdin.read()
    terminate_server()
threading.Thread(target=stop_with_parent, daemon=True).start()
ready = False
with open(log_path, "w") as log:
    for line in proc.stdout:
        log.write(line)
        log.flush()
        if not ready and "%s" in line:
            ready = True
            print("READY", flush=True)
code = proc.wait()
if os.environ.get("BEAD_POUR_REPORT_REAP") == "1":
    print(f"SERVER_REAPED {proc.pid} {code}", file=sys.stderr, flush=True)
if not ready:
    print(f"EXITED {code}", flush=True)
sys.exit(code)
""" % READY_MARK

# Holds each supervisor's stdin write end for the life of this process: dropping it stops that server.
LIFELINES: dict[Path, tuple[subprocess.Popen, bool]] = {}
HANG_GUARD_SECONDS = 60


def server_logs(root: Path) -> str:
    log = root / SERVER_DIR / "server.log"
    if not log.is_file():
        return ""
    return "\n--- server.log (last 40 lines) ---\n" + "\n".join(log.read_text(errors="replace").splitlines()[-40:])


def bounded_read(stream, method, process, root, on_timeout):
    result = queue.Queue(maxsize=1)

    def read():
        try:
            result.put((getattr(stream, method)(), None))
        except Exception as error:
            result.put((None, error))

    threading.Thread(target=read, daemon=True).start()
    try:
        value, error = result.get(timeout=HANG_GUARD_SECONDS)
    except queue.Empty as timeout:
        logs = server_logs(root)
        try:
            on_timeout()
        except Exception as cleanup_error:
            raise AssertionError(f"{method} timed out waiting for process {process.pid}; cleanup failed: {cleanup_error}{logs}") from timeout
        raise AssertionError(f"{method} timed out waiting for process {process.pid}{logs}") from timeout
    if error:
        raise AssertionError(f"{method} failed for process {process.pid}: {error}{server_logs(root)}") from error
    return value


def stop_owner(owner, root):
    try:
        os.killpg(owner.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        owner.wait(timeout=HANG_GUARD_SECONDS)
    except subprocess.TimeoutExpired as timeout:
        raise AssertionError(f"owner {owner.pid} survived cleanup{server_logs(root)}") from timeout


def owner_port(owner, root):
    ready = bounded_read(owner.stdout, "readline", owner, root, lambda: stop_owner(owner, root)).strip()
    if not ready.isdecimal():
        detail = bounded_read(owner.stderr, "read", owner, root, lambda: stop_owner(owner, root))
        raise AssertionError(f"owner {owner.pid} did not report a port ({ready!r}): {detail}{server_logs(root)}")
    return int(ready)


def reap_killed_owner(owner, root):
    try:
        owner_port(owner, root)
        owner.kill()
        try:
            owner.wait(timeout=HANG_GUARD_SECONDS)
        except subprocess.TimeoutExpired as timeout:
            raise AssertionError(f"owner {owner.pid} did not exit{server_logs(root)}") from timeout
        return bounded_read(owner.stderr, "read", owner, root, lambda: stop_owner(owner, root))
    except BaseException as original:
        try:
            stop_owner(owner, root)
        except BaseException as cleanup_error:
            original.add_note(f"owner cleanup failed: {cleanup_error}")
        raise


def abort_server(root):
    entry = LIFELINES.get(root)
    if entry is not None and entry[0].poll() is None:
        try:
            entry[0].terminate()
        except ProcessLookupError:
            pass
    stop_server(root)


def start_server(root: Path, *, inherited_group=False) -> int:
    """Start a dolt sql-server for the workspace on a free loopback port; return the port once dolt reports it is ready."""
    data = root / SERVER_DIR
    data.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    supervisor = data / "supervise.py"
    supervisor.write_text(SUPERVISOR)
    env = os.environ.copy()
    env.pop("BEAD_POUR_REPORT_REAP", None)
    if inherited_group:
        env["BEAD_POUR_REPORT_REAP"] = "1"
    server = subprocess.Popen([sys.executable, str(supervisor), str(data / "server.log"),
                               "dolt", "sql-server", "-H", "127.0.0.1", "-P", str(port), f"--data-dir={data}"],
                              cwd=data, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                              start_new_session=not inherited_group, env=env)
    LIFELINES[root] = (server, not inherited_group)
    report = bounded_read(server.stdout, "readline", server, root, lambda: abort_server(root)).strip()
    if report != "READY":
        log = data / "server.log"
        tail = "\n".join(log.read_text(errors="replace").splitlines()[-40:]) if log.is_file() else ""
        stop_server(root)
        raise AssertionError(f"dolt sql-server on 127.0.0.1:{port} did not report ready ({report or 'EOF'}):\n{tail}")
    return port


def stop_server(root: Path) -> None:
    entry = LIFELINES.pop(root, None)
    if entry is None:
        return
    server, owns_group = entry
    try:
        server.stdin.close()
    except BrokenPipeError:
        pass
    try:
        server.wait(timeout=HANG_GUARD_SECONDS)
    except subprocess.TimeoutExpired as timeout:
        try:
            server.terminate()
        except ProcessLookupError:
            pass
        try:
            server.wait(timeout=HANG_GUARD_SECONDS)
        except subprocess.TimeoutExpired:
            if owns_group:
                try:
                    os.killpg(server.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                server.kill()
            server.wait(timeout=HANG_GUARD_SECONDS)
        raise AssertionError(f"supervisor {server.pid} did not stop{server_logs(root)}") from timeout
