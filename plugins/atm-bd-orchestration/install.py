#!/usr/bin/env python3
"""atm-bd-orchestration install hook and standalone installer.

Two ways in:

1. sc-install (synaptic-canvas) loads this file as the package's Tier-3
   install hook and calls prepare()/complete()/cleanup() around its own
   manifest-artifact copy step, once per target (.claude and/or .codex).
2. Standalone: `python3 install.py --dest <repo>/.claude [--set NAME=VALUE ...]`
   runs prepare() and complete(); complete() places every file itself.

Configuration. The only input is the consuming repository's
`.claude/agents/registry.yaml` (plus `--set NAME=VALUE`, which wins). The
variables are the `required_variables` of `config/atm-bd-orchestration.yaml.j2`;
each is read from the top-level registry key of the same name, except the two
role members, which are read from `roles:`:

    dev_sanity_member  -> roles.dev-sanity
    qa_member          -> roles.quality-mgr

The template is rendered strictly with the sc-compose wheel into
`<repo>/.claude/project/atm-bd-orchestration.yaml`; a missing variable is an
install error that names it. There are no defaults. The resolved role members are
written back into `roles:` so `resolve-role` agrees with the config file.

Ownership. `<repo>/.claude/project/atm-bd-orchestration.lock.json` records the
package version and the sha256 of every file the install wrote. A rerun replaces
a recorded file only when it is unchanged since it was written and fails, naming
it, when it was modified; it never writes over a file it does not own, and it
fails when a target skill directory exists that it does not own. Files a newer
version stops shipping are removed when unchanged. With `--overwrite` (sc-install:
`overwrite` or `force`), a modified or foreign file at a shipped path, or a
modified file no longer shipped, is warned about and moved to
`<repo>/.backup/<UTC time>/<path>` instead of failing the install; the package's
file then takes its place. A repository installed by
0.x (no lock file) is migrated once: an existing file is owned when its bytes are
the bytes this version ships or bytes some 0.x version shipped
(`config/legacy-owned.json`); any other existing file fails the install.

Checks before anything is written: the sc-compose CLI and the python3 wheel the
same version, in the manifest's `sc-compose >= X.Y.Z, < N` range, pydantic and PyYAML present;
`.beads/metadata.json` shows `dolt_mode: server`; every agent named in
`qa_member` and `reviewers_round1` has `.claude/agents/<name>.md` (in the
repository or shipped by this install). `dev_sanity_member` is not checked: it
names a team member, which may have no agent file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PKG_DIR = Path(__file__).resolve().parent
PACKAGE = "atm-bd-orchestration"
REGISTRY_FILE = "registry.yaml"
MANIFEST_FILE = "manifest.yaml"
CONFIG_TEMPLATE = "config/atm-bd-orchestration.yaml.j2"
LEGACY_OWNED = "config/legacy-owned.json"
BACKUP_DIR = ".backup"
OVERWRITE_HINT = f" (or rerun with --overwrite to move it to {BACKUP_DIR}/)"
CONFIG_OUT = ".claude/project/atm-bd-orchestration.yaml"
LOCK_OUT = ".claude/project/atm-bd-orchestration.lock.json"
CONSUMER_REGISTRY = ".claude/agents/registry.yaml"
BEADS_METADATA = ".beads/metadata.json"

# Variables whose value is a list (YAML list in registry.yaml; `--set` takes a
# JSON array or a comma-separated list).
LIST_VARIABLES = frozenset({
    "requirements_globs", "adr_globs",
    "reviewers_round1",
})
# Variables read from registry.yaml `roles:` and written back there.
ROLE_KEYS = {"dev_sanity_member": "dev-sanity", "qa_member": "quality-mgr"}
# Variables naming agents that must have .claude/agents/<name>.md. dev_sanity_member
# is a team member running the dev-sanity directive and may have no agent file.
AGENT_VARIABLES = ("qa_member", "reviewers_round1")

# Install-time placeholders in installed skill/agent files (the files listed under
# `render:` in registry.yaml). repo_slug, repo_name and
# workflow_issues_root are derived from the repository, not configured.
RENDER_VARIABLES = (
    "dev_sanity_member",
    "bead_prefix",
    "workflow_issues_root",
    "repo_slug",
    "repo_name",
    "worktree_base",
    "console_install",
)
PLACEHOLDER_RE = re.compile(r"\{\{ (" + "|".join(RENDER_VARIABLES) + r") \}\}")

# Repository-root files an earlier version placed (0.10.4 and before); removed on upgrade like
# any file no longer shipped (repo path -> package source). The Jev transport now ships inside
# the atm-bd-orchestration skill.
RETIRED_REPO_FILES = {"scripts/jev_client.py": "assets/scripts/jev_client.py"}

CONSOLE_INSTALL = (
    "uv tool install "
    "git+https://github.com/randlee/atm-bd-orchestration.git#subdirectory=apps/atm-bd-console"
)
TOOL_INSTALLS = {
    "atm": "brew install randlee/tap/atm",
    "atm-bd": CONSOLE_INSTALL,
    "bd": "brew install beads",
    "git": "brew install git",
    "jq": "brew install jq",
}
HANDLED_ELSEWHERE_REQUIREMENTS = frozenset({"python3", "pyyaml", "pydantic", "sc-compose"})
PYTHON_INSTALL = "install Python 3.11 or newer with your system package manager"


class InstallError(RuntimeError):
    """An install refusal; the message names what to fix."""


# ---------------------------------------------------------------------------
# Package files
# ---------------------------------------------------------------------------


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise InstallError("PyYAML is required to read registry.yaml (pip install pyyaml)") from exc
    return yaml


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        data = _yaml().safe_load(path.read_text(encoding="utf-8")) or {}
    except _yaml().YAMLError as exc:
        raise InstallError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path}: expected a mapping at the top level")
    return data


def load_registry(pkg_dir: Path = PKG_DIR) -> Dict[str, Any]:
    return _load_yaml(pkg_dir / REGISTRY_FILE)


def load_manifest(pkg_dir: Path = PKG_DIR) -> Dict[str, Any]:
    return _load_yaml(pkg_dir / MANIFEST_FILE)


def load_manifest_artifacts(pkg_dir: Path = PKG_DIR) -> Dict[str, List[str]]:
    artifacts = load_manifest(pkg_dir).get("artifacts") or {}
    return {k: list(v or []) for k, v in artifacts.items()}


def package_version(pkg_dir: Path = PKG_DIR) -> str:
    return str(load_manifest(pkg_dir)["version"])


SC_COMPOSE_RANGE_RE = re.compile(r"sc-compose >= ([0-9]+\.[0-9]+\.[0-9]+), < ([0-9]+)")
TOOL_REQUIREMENT_RE = re.compile(r"([a-z][a-z0-9-]*)(?: >= ([0-9]+(?:\.[0-9]+){0,2}))?")


def sc_compose_range(pkg_dir: Path = PKG_DIR) -> Tuple[str, int]:
    """(floor, major ceiling) for the sc-compose CLI and Python wheel: the manifest's `sc-compose >= X.Y.Z, < N`."""
    for entry in load_manifest(pkg_dir).get("requires") or []:
        m = SC_COMPOSE_RANGE_RE.fullmatch(str(entry))
        if m:
            return m.group(1), int(m.group(2))
    raise InstallError(f"{pkg_dir / MANIFEST_FILE}: requires has no `sc-compose >= X.Y.Z, < N` range")


def sc_compose_supported(version: str, pkg_dir: Path = PKG_DIR) -> bool:
    """True when `version` (X.Y.Z) is in the manifest's sc-compose range."""
    floor, ceiling = sc_compose_range(pkg_dir)
    m = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)", version)
    if not m:
        return False
    parts = tuple(int(p) for p in m.groups())
    return tuple(int(p) for p in floor.split(".")) <= parts and parts[0] < ceiling


def sc_compose_upgrade(pkg_dir: Path = PKG_DIR) -> str:
    floor, ceiling = sc_compose_range(pkg_dir)
    return (f"python3 -m pip install 'sc-compose>={floor},<{ceiling}' and "
            f"brew upgrade randlee/tap/sc-compose (or cargo install sc-compose --version '>={floor}, <{ceiling}' --locked), "
            "the CLI and wheel at the same version")


def declared_tool_requirements(pkg_dir: Path = PKG_DIR) -> list[tuple[str, str]]:
    """CLI version floors declared in the manifest, excluding sc-compose's range."""
    requirements = []
    for entry in load_manifest(pkg_dir).get("requires") or []:
        requirement = str(entry).split("#", 1)[0].strip()
        if SC_COMPOSE_RANGE_RE.fullmatch(requirement):
            continue
        match = TOOL_REQUIREMENT_RE.fullmatch(requirement)
        if match is None:
            raise InstallError(f"{pkg_dir / MANIFEST_FILE}: unrecognized requirement {requirement!r}")
        name, floor = match.groups()
        if name in HANDLED_ELSEWHERE_REQUIREMENTS:
            continue
        if name not in TOOL_INSTALLS:
            raise InstallError(f"{pkg_dir / MANIFEST_FILE}: unrecognized requirement {name!r}")
        if floor is None:
            raise InstallError(f"{pkg_dir / MANIFEST_FILE}: {name} must declare a version floor")
        requirements.append((name, floor))
    return requirements


def manifest_requirement_floor(name: str, pkg_dir: Path = PKG_DIR) -> str:
    """The required version floor for one non-range manifest requirement."""
    for entry in load_manifest(pkg_dir).get("requires") or []:
        match = TOOL_REQUIREMENT_RE.fullmatch(str(entry).split("#", 1)[0].strip())
        if match is not None and match.group(1) == name:
            floor = match.group(2)
            if floor is None:
                raise InstallError(f"{pkg_dir / MANIFEST_FILE}: {name} must declare a version floor")
            return floor
    raise InstallError(f"{pkg_dir / MANIFEST_FILE}: requires has no {name} version floor")


def _version_parts(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"\b([0-9]+)\.([0-9]+)(?:\.([0-9]+))?\b", text)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _check_required_tool(name: str, required: str, install_command: str) -> None:
    if shutil.which(name) is None:
        raise InstallError(f"{name} is not on PATH; install it with: {install_command}")
    probe = subprocess.run([name, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    reported = (probe.stdout or probe.stderr).strip()
    if probe.returncode != 0:
        raise InstallError(f"{name} --version failed: {reported or 'no output'}; install it with: {install_command}")
    actual, floor = _version_parts(reported), _version_parts(required)
    if actual is None:
        raise InstallError(f"{name} --version did not report a semantic version: {reported or 'no output'}; install it with: {install_command}")
    assert floor is not None
    if actual < floor:
        raise InstallError(f"{name} is {'.'.join(map(str, actual))}, older than required {required}; install it with: {install_command}")


def config_variables(pkg_dir: Path = PKG_DIR) -> List[str]:
    """The required_variables of the config template: the one declaration of the inputs."""
    text = (pkg_dir / CONFIG_TEMPLATE).read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise InstallError(f"{CONFIG_TEMPLATE}: no front matter")
    names = (_yaml().safe_load(m.group(1)) or {}).get("required_variables") or []
    return [str(n) for n in names]


def load_legacy_owned(pkg_dir: Path = PKG_DIR) -> Dict[str, set]:
    path = pkg_dir / LEGACY_OWNED
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {rel: set(shas) for rel, shas in (data.get("files") or {}).items()}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Repository values
# ---------------------------------------------------------------------------


def repo_root_for(destination_path: Path) -> Path:
    """The consuming repository: the parent of <repo>/.claude or <repo>/.codex."""
    dest = Path(destination_path).resolve()
    if dest.name in (".claude", ".codex"):
        return dest.parent
    raise InstallError(f"--dest must be a repository's .claude or .codex directory, got {dest}")


def _git_origin_slug(repo_root: Path) -> Optional[str]:
    try:
        url = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    m = re.search(r"[:/]([^/:]+)/([^/]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def _registry_location(name: str) -> str:
    if name in ROLE_KEYS:
        return f"{CONSUMER_REGISTRY} roles.{ROLE_KEYS[name]}"
    return f"{CONSUMER_REGISTRY} {name}"


def parse_set(items: Iterable[str]) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise InstallError(f"--set expects NAME=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        overrides[k.strip()] = v.strip()
    return overrides


def _coerce_set_value(name: str, raw: Any) -> Any:
    if name not in LIST_VARIABLES or not isinstance(raw, str):
        return raw
    text = raw.strip()
    if text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise InstallError(f"--set {name}: not a JSON array: {exc}") from exc
    return [part.strip() for part in text.split(",") if part.strip()]


def resolve_config(repo_root: Path, overrides: Dict[str, Any], pkg_dir: Path = PKG_DIR) -> Dict[str, Any]:
    """Read the declared variables from registry.yaml and --set. Missing ones are left
    out (the strict render names them); unknown --set keys and wrong types are errors."""
    names = [*config_variables(pkg_dir), "max_sanity_rounds"]
    unknown = sorted(k for k in overrides if k not in names)
    if unknown:
        raise InstallError(
            "unknown --set variable(s): " + ", ".join(unknown)
            + f"; the variables are the required_variables of {CONFIG_TEMPLATE}: " + ", ".join(names))
    registry = repo_root / CONSUMER_REGISTRY
    reg = _load_yaml(registry) if registry.is_file() else {}
    misplaced = sorted(n for n in ROLE_KEYS if n in reg)
    if misplaced:
        raise InstallError(
            f"{registry}: " + ", ".join(f"{n} belongs under roles.{ROLE_KEYS[n]}" for n in misplaced))
    roles = reg.get("roles") or {}
    if not isinstance(roles, dict):
        raise InstallError(f"{registry}: roles must be a mapping")
    values: Dict[str, Any] = {}
    rounds = overrides.get("max_sanity_rounds", reg.get("max_sanity_rounds", 5))
    if isinstance(rounds, str) and rounds.isdecimal():
        rounds = int(rounds)
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise InstallError("max_sanity_rounds must be a positive integer")
    values["max_sanity_rounds"] = rounds
    for name in names:
        if name == "max_sanity_rounds":
            continue
        if name in overrides:
            value = _coerce_set_value(name, overrides[name])
        elif name in ROLE_KEYS:
            value = roles.get(ROLE_KEYS[name])
        else:
            value = reg.get(name)
        if value is None:
            continue
        if name in LIST_VARIABLES:
            if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                raise InstallError(f"{name} must be a list of non-empty strings ({_registry_location(name)}), got {value!r}")
        elif not isinstance(value, str) or not value:
            raise InstallError(f"{name} must be a non-empty string ({_registry_location(name)}), got {value!r}")
        values[name] = value
    return values


def derived_values(repo_root: Path, config: Dict[str, Any]) -> Dict[str, str]:
    """The install-time render values that come from the repository itself."""
    slug = _git_origin_slug(repo_root)
    if not slug:
        raise InstallError(f"{repo_root}: no git remote 'origin' with an owner/name URL; add one (git remote add origin ...)")
    return {
        "repo_slug": slug,
        "repo_name": slug.rsplit("/", 1)[-1],
        "workflow_issues_root": f"{config['bead_prefix']}-workflow-issues",
        "console_install": CONSOLE_INSTALL,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _sc_compose(template_text: str, variables: Dict[str, Any], *, suffix: str) -> Tuple[Optional[str], str]:
    """(rendered text, "") or (None, the sc-compose error), rendered strictly by the sc-compose wheel."""
    sys.path.insert(0, str(PKG_DIR / "skills/atm-beads/scripts"))
    try:
        import sc_render
    except ImportError as exc:
        raise InstallError(f"this python cannot import the sc-compose wheel ({exc}); "
                           f"install {sc_compose_upgrade()} and rerun") from exc
    finally:
        sys.path.pop(0)
    with tempfile.TemporaryDirectory(prefix="atm-bd-install-") as tmp:
        body = Path(tmp) / f"body{suffix}"
        body.write_text(template_text, encoding="utf-8")
        try:
            return sc_render.render(body, variables), ""
        except sc_render.RenderError as exc:
            return None, sc_render.error_text(exc)


MISSING_RE = re.compile(r"missing required variable: ([A-Za-z0-9_]+)")


def render_config(values: Dict[str, Any], pkg_dir: Path = PKG_DIR) -> bytes:
    """Render the config template strictly. A missing variable fails, naming it."""
    out, error = _sc_compose((pkg_dir / CONFIG_TEMPLATE).read_text(encoding="utf-8"), values, suffix=".yaml.j2")
    if out is None:
        missing = list(dict.fromkeys(MISSING_RE.findall(error)))
        if missing:
            raise InstallError(
                "sc-compose --strict: missing required variable(s): " + ", ".join(missing) + "; set "
                + "; ".join(f"{n} in {_registry_location(n)} (or --set {n}=...)" for n in missing))
        first = error.strip().splitlines()[:1]
        raise InstallError(f"sc-compose render of {CONFIG_TEMPLATE} failed: {first[0] if first else 'no output'}")
    return out.encode("utf-8")


def _split_frontmatter(text: str) -> Tuple[str, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[: end + 5], text[end + 5 :]
    return "", text


def render_text(text: str, variables: Dict[str, str]) -> str:
    """Render one skill/agent file body with sc-compose (strict: every referenced token must be declared)."""
    front, body = _split_frontmatter(text)
    declared = "".join(f"  - {name}\n" for name in RENDER_VARIABLES)
    template = f"---\nname: atm-bd-install\nversion: 1.0.0\nformat: markdown\nrequired_variables:\n{declared}---\n{body}"
    rendered, error = _sc_compose(template, {n: variables[n] for n in RENDER_VARIABLES}, suffix=".md")
    if rendered is None:
        first = error.strip().splitlines()[:1]
        raise InstallError(f"sc-compose render failed: {first[0] if first else 'no output'}")
    if body.endswith("\n") and not rendered.endswith("\n"):
        rendered += "\n"
    leftover = PLACEHOLDER_RE.search(rendered)
    if leftover:
        raise InstallError(f"placeholder {leftover.group(0)} survived rendering")
    return front + rendered


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_tools() -> None:
    _check_required_tool("python3", manifest_requirement_floor("python3"), PYTHON_INSTALL)
    floor, ceiling = sc_compose_range()
    needs = f"the package needs >= {floor}, < {ceiling}"
    if shutil.which("sc-compose") is None:
        raise InstallError(f"sc-compose is not on PATH; install {sc_compose_upgrade()} and rerun")
    cli = subprocess.run(["sc-compose", "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cli_version = (cli.stdout.split() or [""])[-1]
    if cli.returncode != 0 or not sc_compose_supported(cli_version):
        raise InstallError(f"sc-compose CLI is {cli.stdout.strip() or cli.stderr.strip()}, {needs}; "
                           f"install {sc_compose_upgrade()} and rerun")
    # validate-plan runs scripts/bead_schema.py (pydantic) and the scripts render with the
    # sc-compose wheel, all with the python3 on PATH.
    probe = subprocess.run(["python3", "-c", "import pydantic, yaml, sc_compose, importlib.metadata as m; "
                            "print(m.version('sc-compose'))"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if probe.returncode != 0:
        raise InstallError("python3 on PATH cannot import pydantic, PyYAML and the sc-compose wheel, which the "
                           f"scripts need; install them (python3 -m pip install pydantic pyyaml 'sc-compose=={cli_version}') "
                           "and rerun")
    wheel_version = probe.stdout.strip()
    if not sc_compose_supported(wheel_version):
        raise InstallError(f"python3 on PATH has the sc-compose wheel {wheel_version}, {needs}; "
                           f"install {sc_compose_upgrade()} and rerun")
    if wheel_version != cli_version:
        raise InstallError(f"the sc-compose CLI is {cli_version} and the python3 wheel is {wheel_version}; they must "
                           f"be the same version; install {sc_compose_upgrade()} and rerun")
    try:
        from importlib.metadata import PackageNotFoundError, version
        import sc_compose  # noqa: F401
        interpreter_version = version("sc-compose")
    except (ImportError, PackageNotFoundError) as exc:
        raise InstallError(f"the installer interpreter {sys.executable} cannot load sc-compose; "
                           f"upgrade the app or install sc-compose=={cli_version} in the host interpreter") from exc
    if interpreter_version != cli_version:
        raise InstallError(f"the sc-compose CLI is {cli_version} but the installer interpreter wheel is "
                           f"{interpreter_version}; upgrade the app or host to sc-compose=={cli_version} and rerun")
    for name, floor in declared_tool_requirements():
        _check_required_tool(name, floor, TOOL_INSTALLS[name])


def check_beads_server_mode(repo_root: Path) -> None:
    path = repo_root / BEADS_METADATA
    if not path.is_file():
        raise InstallError(f"BEADS_NOT_SERVER_MODE: {path} not found; the loop needs a beads database in "
                           "Dolt server mode (bd doctor --json), so initialise it against a dolt sql-server")
    try:
        mode = json.loads(path.read_text(encoding="utf-8")).get("dolt_mode")
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"BEADS_NOT_SERVER_MODE: {path}: unreadable: {exc}") from exc
    if mode != "server":
        raise InstallError(f"BEADS_NOT_SERVER_MODE: {path} has dolt_mode {mode!r}, not 'server'; validate-plan needs "
                           "bd doctor --json, which only server mode provides")


def check_agents(repo_root: Path, config: Dict[str, Any], shipped_agents: Iterable[str]) -> None:
    shipped = {Path(rel).stem for rel in shipped_agents}
    missing: List[str] = []
    for var in AGENT_VARIABLES:
        names = config[var] if isinstance(config[var], list) else [config[var]]
        for name in names:
            if name in shipped or (repo_root / ".claude" / "agents" / f"{name}.md").is_file():
                continue
            missing.append(f"{name} ({var})")
    if missing:
        raise InstallError("no .claude/agents/<name>.md for: " + ", ".join(dict.fromkeys(missing)))


# ---------------------------------------------------------------------------
# Install plan: what to write, what to remove, what is in the way
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    repo_root: Path
    target_prefix: str                                   # ".claude/" or ".codex/"
    writes: Dict[str, Tuple[bytes, bool]] = field(default_factory=dict)   # repo-rel -> (bytes, executable)
    removals: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    lock_files: Dict[str, str] = field(default_factory=dict)
    backups: List[str] = field(default_factory=list)     # files in the way, moved to .backup/ by --overwrite
    registry_text: Optional[str] = None                  # registry.yaml with the roles written, if it changes


def _read_lock(repo_root: Path) -> Optional[Dict[str, Any]]:
    path = repo_root / LOCK_OUT
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InstallError(f"{path}: not valid JSON ({exc}); restore it from git") from exc
    if data.get("package") != PACKAGE or not isinstance(data.get("files"), dict):
        raise InstallError(f"{path}: not an {PACKAGE} install record; restore it from git")
    return data


def desired_files(pkg_dir: Path, target_prefix: str, *, codex: bool, render_values: Dict[str, str],
                  config_bytes: bytes) -> Dict[str, Tuple[bytes, bool, str]]:
    """repo-relative path -> (bytes, executable, package source path)."""
    renders = set(load_registry(pkg_dir).get("render") or [])
    artifacts = load_manifest_artifacts(pkg_dir)
    cats = ("skills",) if codex else ("skills", "agents")
    out: Dict[str, Tuple[bytes, bool, str]] = {}
    for cat in cats:
        for rel in artifacts.get(cat, []):
            src = pkg_dir / rel
            if not src.is_file():
                raise InstallError(f"manifest artifact missing from the package: {rel}")
            data = src.read_bytes()
            if rel in renders:
                data = render_text(data.decode("utf-8"), render_values).encode("utf-8")
            out[target_prefix + rel] = (data, bool(src.stat().st_mode & stat.S_IXUSR), rel)
    out[CONFIG_OUT] = (config_bytes, False, CONFIG_TEMPLATE)
    return out


def plan_install(pkg_dir: Path, dest: Path, overrides: Dict[str, Any], *, codex: bool,
                 overwrite: bool = False) -> Tuple[Plan, Dict[str, Any]]:
    """Resolve, render and check everything; return the plan. Writes nothing."""
    pkg_dir = Path(pkg_dir).resolve()
    repo_root = repo_root_for(dest)
    target_prefix = Path(dest).resolve().name + "/"
    config = resolve_config(repo_root, overrides, pkg_dir)
    config_bytes = render_config(config, pkg_dir)
    check_beads_server_mode(repo_root)
    artifacts = load_manifest_artifacts(pkg_dir)
    check_agents(repo_root, config, [] if codex else artifacts.get("agents", []))
    render_values = {**{k: v for k, v in config.items() if k in RENDER_VARIABLES}, **derived_values(repo_root, config)}

    wanted = desired_files(pkg_dir, target_prefix, codex=codex, render_values=render_values, config_bytes=config_bytes)
    lock = _read_lock(repo_root)
    locked: Dict[str, str] = dict(lock["files"]) if lock else {}
    legacy = load_legacy_owned(pkg_dir) if lock is None else {}
    plan = Plan(repo_root=repo_root, target_prefix=target_prefix)

    def owned(key: str, data: bytes, pkg_rel: Optional[str]) -> Optional[bool]:
        """True owned and unmodified, False in the way, None for locked-but-modified."""
        digest = sha256(data)
        if key in locked:
            return True if locked[key] == digest else None
        if pkg_rel is None:
            return False
        shipped = {sha256(wanted[key][0])} if key in wanted else set()
        src = pkg_dir / pkg_rel
        if src.is_file() and pkg_rel != CONFIG_TEMPLATE:
            shipped.add(sha256(src.read_bytes()))   # sc-install copies the raw source before complete()
        shipped |= legacy.get(pkg_rel, set())
        return digest in shipped

    # A target skill directory that exists must hold at least one file this package owns.
    skill_names = sorted({rel.split("/")[1] for rel in artifacts.get("skills", [])})
    foreign_dirs: set = set()
    for name in skill_names:
        skill_dir = repo_root / target_prefix / "skills" / name
        if not skill_dir.is_dir():
            continue
        has_owned = False
        for path in skill_dir.rglob("*"):
            if not path.is_file():
                continue
            key = path.relative_to(repo_root).as_posix()
            pkg_rel = wanted[key][2] if key in wanted else _legacy_pkg_rel(key, target_prefix, legacy)
            if owned(key, path.read_bytes(), pkg_rel) is not False:
                has_owned = True
                break
        if not has_owned:
            foreign_dirs.add(name)
            plan.problems.append(f"skill '{name}' exists at {target_prefix}skills/{name} and is not owned by {PACKAGE}; "
                                 "remove or rename it and rerun")

    for key, (data, executable, pkg_rel) in sorted(wanted.items()):
        if any(key.startswith(f"{target_prefix}skills/{n}/") for n in foreign_dirs):
            continue
        path = repo_root / key
        if path.is_file():
            state = owned(key, path.read_bytes(), pkg_rel)
            if state is not True and overwrite:
                plan.backups.append(key)
            elif state is None:
                plan.problems.append(f"{key} was modified since {PACKAGE} installed it; restore it (or delete it) and rerun{OVERWRITE_HINT}")
                continue
            elif state is False:
                plan.problems.append(f"{key} exists and is not owned by {PACKAGE}; remove or rename it and rerun{OVERWRITE_HINT}")
                continue
        elif path.exists():
            plan.problems.append(f"{key} exists and is not a file")
            continue
        plan.writes[key] = (data, executable)
        plan.lock_files[key] = sha256(data)

    # Files an earlier install placed that this version no longer ships (this target only).
    stale: Dict[str, Optional[str]] = {k: v for k, v in locked.items() if (k.startswith(target_prefix) or k in RETIRED_REPO_FILES) and k not in wanted}
    for pkg_rel in legacy:
        key = target_prefix + pkg_rel
        if pkg_rel.startswith(("skills/", "agents/")) and key not in wanted and key not in stale:
            stale[key] = None
    for key, pkg_rel in RETIRED_REPO_FILES.items():
        if pkg_rel in legacy and key not in stale:
            stale[key] = None
    for key, recorded in sorted(stale.items()):
        path = repo_root / key
        if not path.is_file():
            continue
        digest = sha256(path.read_bytes())
        if recorded is not None:
            if digest == recorded:
                plan.removals.append(key)
            elif overwrite:
                plan.backups.append(key)
                plan.removals.append(key)
            else:
                plan.problems.append(f"{key} was modified since {PACKAGE} installed it and this version no longer "
                                     f"ships it; move it out of the way and rerun{OVERWRITE_HINT}")
        elif digest in legacy.get(RETIRED_REPO_FILES.get(key, key[len(target_prefix):]), set()):
            plan.removals.append(key)

    try:
        plan.registry_text = roles_text(repo_root / CONSUMER_REGISTRY, {role: config[var] for var, role in ROLE_KEYS.items()})
    except InstallError as exc:
        plan.problems.append(str(exc))

    # Keep the other target's records.
    for key, digest in locked.items():
        if not key.startswith(target_prefix) and key not in plan.lock_files and key not in wanted and key not in RETIRED_REPO_FILES:
            plan.lock_files[key] = digest
    return plan, config


def _legacy_pkg_rel(key: str, target_prefix: str, legacy: Dict[str, set]) -> Optional[str]:
    rel = key[len(target_prefix):] if key.startswith(target_prefix) else None
    return rel if rel is not None and rel in legacy else None


# ---------------------------------------------------------------------------
# Applying a plan
# ---------------------------------------------------------------------------


def roles_text(registry: Path, roles: Dict[str, str]) -> Optional[str]:
    """registry.yaml with roles.<role> set, keeping the rest of the file as written;
    None when nothing changes."""
    text = registry.read_text(encoding="utf-8") if registry.is_file() else ""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if re.match(r"^roles:\s*(#.*)?$", line.rstrip("\n"))), None)
    if start is None:
        new = text + ("\n" if text and not text.endswith("\n") else "")
        new += "roles:\n" + "".join(f"  {k}: {v}\n" for k, v in roles.items())
    else:
        end = start + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][:1] in (" ", "\t")):
            end += 1
        block = lines[start + 1:end]
        trailing: List[str] = []
        while block and not block[-1].strip():
            trailing.insert(0, block.pop())
        if block and not block[-1].endswith("\n"):
            block[-1] += "\n"
        indent = next((m.group(1) for m in (re.match(r"^([ \t]+)\S", line) for line in block) if m), "  ")
        for role, member in roles.items():
            pattern = re.compile(rf"^({re.escape(indent)}{re.escape(role)}:[ \t]*)([^#\n]*?)([ \t]*(?:#.*)?\n?)$")
            for i, line in enumerate(block):
                m = pattern.match(line)
                if m:
                    block[i] = f"{indent}{role}: {member}{m.group(3)}"
                    break
            else:
                block.append(f"{indent}{role}: {member}\n")
        new = "".join(lines[:start + 1] + block + trailing + lines[end:])
    try:
        new_roles = (_yaml().safe_load(new) or {}).get("roles") or {}
    except _yaml().YAMLError:
        new_roles = {}
    if not isinstance(new_roles, dict) or any(new_roles.get(k) != v for k, v in roles.items()):
        raise InstallError(f"{registry}: could not write roles {roles} (is roles: a flow mapping?); set them by hand and rerun")
    return None if new == text else new


def write_roles(registry: Path, roles: Dict[str, str]) -> bool:
    """Set roles.<role> in registry.yaml. Returns True when the file changed."""
    new = roles_text(registry, roles)
    if new is None:
        return False
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(new, encoding="utf-8")
    return True


def provision_tool_identity(repo_root: Path) -> None:
    """Create the default notification identity, tolerating a verified existing member."""
    command = ["atm", "teams", "add-member", "tool", "atm-bd", "--agent-type", "tool", "--model", "none"]
    try:
        added = subprocess.run(command, cwd=repo_root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if added.returncode == 0:
            return
        existing = subprocess.run(["atm", "members", "--team", "tool", "--json"], cwd=repo_root,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if existing.returncode == 0:
            members = json.loads(existing.stdout).get("members", [])
            if any(isinstance(member, dict) and member.get("name") == "atm-bd" and member.get("agent_type") == "tool"
                   for member in members):
                return
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise InstallError(f"could not provision atm-bd@tool: {exc}; run {' '.join(command)}") from exc
    raise InstallError(f"could not provision atm-bd@tool: {added.stderr.strip() or added.stdout.strip()}; run {' '.join(command)}")


def apply_plan(plan: Plan, version: str) -> int:
    """Write the plan; return the number of files whose bytes changed."""
    root = plan.repo_root
    provision_tool_identity(root)
    changed = 0
    if plan.backups:
        backup = root / BACKUP_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for key in plan.backups:
            print(f"warning: {key} is modified or not {PACKAGE}'s; moved to {(backup / key).relative_to(root)}", file=sys.stderr)
            (backup / key).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root / key), str(backup / key))
        ignore = root / BACKUP_DIR / ".gitignore"   # backups are for local review; git history already has the files
        if not ignore.exists():
            ignore.write_text("*\n")
    for key, (data, executable) in plan.writes.items():
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
            changed += 1
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    for key in plan.removals:
        path = root / key
        path.unlink(missing_ok=True)   # a backed-up file is already gone
        parent = path.parent
        stop = root / plan.target_prefix
        while parent not in (stop, root) and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    lock = {"package": PACKAGE, "version": version, "files": dict(sorted(plan.lock_files.items()))}
    lock_path = root / LOCK_OUT
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    if plan.registry_text is not None:
        (root / CONSUMER_REGISTRY).write_text(plan.registry_text, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------
# sc-install hook contract
# ---------------------------------------------------------------------------


def _ok() -> Dict[str, str]:
    return {"result": "success"}


def _fail(message: str) -> Dict[str, str]:
    return {"result": "fail", "message": message}


def _codex(destination_path: str, options: dict) -> bool:
    return bool(options.get("codex")) or Path(destination_path).name == ".codex"


def _plan_or_fail(source_path: str, destination_path: str, options: dict) -> Tuple[Optional[Plan], Dict[str, Any], Optional[dict]]:
    try:
        check_tools()
        plan, config = plan_install(Path(source_path), Path(destination_path), dict(options.get("args") or {}),
                                    codex=_codex(destination_path, options),
                                    overwrite=bool(options.get("overwrite") or options.get("force")))
    except InstallError as exc:
        return None, {}, _fail(str(exc))
    if plan.problems:
        return None, {}, _fail("; ".join(plan.problems))
    return plan, config, None


def prepare(source_path: str, destination_path: str, options: dict) -> dict:
    """Check the tools, the repository values, the beads mode, the agents and file
    ownership before anything is copied. Writes nothing."""
    _, _, failure = _plan_or_fail(source_path, destination_path, options)
    return failure or _ok()


def complete(source_path: str, destination_path: str, options: dict) -> dict:
    """Place every file (rendered where listed), remove unchanged files no longer
    shipped, write the config, the install record and the roles."""
    plan, config, failure = _plan_or_fail(source_path, destination_path, options)
    if failure:
        return failure
    assert plan is not None
    version = package_version(Path(source_path))
    try:
        changed = apply_plan(plan, version)
    except InstallError as exc:
        return _fail(str(exc))
    print(f"{PACKAGE} {version}: {changed} file(s) written, {len(plan.writes) - changed} unchanged, "
          f"{len(plan.removals)} removed, {len(plan.backups)} backed up to {BACKUP_DIR}/; roles {'updated' if plan.registry_text is not None else 'unchanged'} "
          f"in {CONSUMER_REGISTRY}; config {CONFIG_OUT}; record {LOCK_OUT}")
    return _ok()


def cleanup(source_path: str, destination_path: str, options: dict) -> dict:
    """Nothing to clean: prepare() writes nothing and complete() writes only recorded files."""
    return _ok()


# ---------------------------------------------------------------------------
# Standalone installer
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None, pkg_dir: Path = PKG_DIR) -> int:
    parser = argparse.ArgumentParser(description="Install atm-bd-orchestration into a repository's .claude (or .codex) directory.")
    parser.add_argument("--dest", required=True, help="<repo>/.claude or <repo>/.codex")
    parser.add_argument("--codex", action="store_true", help="the destination is a .codex directory (skills only, no agents)")
    parser.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="set a config variable (wins over registry.yaml); lists take a JSON array or a,b,c")
    parser.add_argument("--overwrite", action="store_true",
                        help=f"move modified or foreign files in the way to {BACKUP_DIR}/<time>/ and install over them")
    args = parser.parse_args(argv)
    try:
        overrides = parse_set(args.set)
    except InstallError as exc:
        print(f"install.py: {exc}", file=sys.stderr)
        return 1
    dest = Path(args.dest).expanduser().resolve()
    options = {"global": False, "local": True, "user": False, "project": False,
               "codex": args.codex or dest.name == ".codex", "force": False, "overwrite": args.overwrite, "expand": True, "args": overrides}
    for step in (prepare, complete):
        result = step(str(pkg_dir), str(dest), options)
        if result["result"] != "success":
            print(f"install.py: {result['message']}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
