"""Every handoff that sends fenced JSON (or a command line) to another agent or script matches its receiver's declared input.

The sender side is what the skill tells an agent to send (a rendered assignment template, an instruction paragraph); the
receiver side is the parser or the `## Inputs` contract the receiver publishes. A reviewer whose agent file is not shipped
by this package (arch-qa, req-qa, ...) is checked against its agent file only when the host repository has one.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[2]
AGENTS = ROOT.parents[1] / "agents"
TEMPLATES = ROOT / "templates"
EXAMPLES = ROOT / "examples"


def render(template: str, variables: Path) -> str:
    result = subprocess.run(["sc-compose", "render", "--strict", "--file", str(TEMPLATES / template),
                             "--var-file", str(variables)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def inputs_json(agent: str) -> dict:
    return json.loads(re.search(r"## Inputs.*?```json\n(.*?)\n```", (AGENTS / agent).read_text(), re.S).group(1))


def load_client():
    spec = importlib.util.spec_from_file_location("jev_client", ROOT / "scripts" / "jev_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    return client


def rerun_paragraph() -> str:
    text = (AGENTS / "dev-sanity.md").read_text()
    return text[text.index("A rerun supplies one"):text.index("never replaces a valid original reply")]


class DevSanityHandoffTests(unittest.TestCase):
    """dev-sanity -> sc-sanity-llm / sc-sanity-jev / jev_client.py, and dev-sanity -> sanity-merge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                                  check=True, capture_output=True, text=True).stdout.strip()

        git("init", "-q")
        (repo / "crates/example-otlp/src").mkdir(parents=True)
        (repo / "docs").mkdir()
        (repo / "docs/retry.md").write_text("retry policy\n")
        (repo / "crates/example-otlp/src/lib.rs").write_text("// old\n")
        git("add", "-A")
        git("commit", "-qm", "base")
        self.base = git("rev-parse", "HEAD")
        (repo / "crates/example-otlp/src/lib.rs").write_text("// exporter\n")
        git("add", "-A")
        git("commit", "-qm", "work")
        self.commit, self.repo = git("rev-parse", "HEAD"), repo

    def assignment(self, fixture: str) -> dict:
        variables = json.loads((EXAMPLES / fixture).read_text())
        variables.update(worktree_path=str(self.repo), commit=self.commit, base_sha=self.base)
        path = Path(self.tmp.name) / "vars.json"
        path.write_text(json.dumps(variables))
        return json.loads(render("dev-sanity-assignment.json.j2", path))

    def test_rendered_assignment_has_exactly_the_reviewers_input_fields(self):
        for fixture in ("dev-sanity-assignment-vars.json", "dev-sanity-assignment-context-vars.json"):
            for agent in ("sc-sanity-llm.md", "sc-sanity-jev.md"):
                with self.subTest(fixture=fixture, agent=agent):
                    self.assertEqual(set(self.assignment(fixture)), set(inputs_json(agent)))

    def test_rendered_assignment_is_accepted_by_the_jev_client(self):
        client = load_client()
        for fixture in ("dev-sanity-assignment-vars.json", "dev-sanity-assignment-context-vars.json"):
            with self.subTest(fixture=fixture):
                (request,) = client.assignment_requests(self.assignment(fixture))
                self.assertIn("deliverable", request["state"])

    def test_rerun_instruction_sends_context_the_receivers_accept(self):
        paragraph = rerun_paragraph()
        self.assertNotIn("{path, why}", paragraph)
        match = re.search(r"--argjson context '<\[(.*?)\]>'", paragraph)
        self.assertIsNotNone(match, "dev-sanity.md must show the rerun context as a JSON array of path strings")
        self.assertEqual(match.group(1), '"path", ...')
        context = ["docs/retry.md"]  # the documented shape, filled with a real path at the pinned commit
        base = self.assignment("dev-sanity-assignment-vars.json")
        manifest = Path(self.tmp.name) / "manifest.json"
        manifest.write_text(json.dumps({"assignments": [{"number": 1, "assignment": base}]}))
        jq = re.search(r"`(jq --argjson n <n> .*?)`;", paragraph, re.S).group(1)
        jq = jq.replace("<n>", "1").replace("'<[\"path\", ...]>'", "\"$CONTEXT\"").replace("$manifest", str(manifest))
        rerun = subprocess.run(["bash", "-c", jq], env={"CONTEXT": json.dumps(context), "PATH": "/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin"},
                               capture_output=True, text=True)
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        sent = json.loads(rerun.stdout)
        self.assertEqual(sent["context"], context)
        (request,) = load_client().assignment_requests(sent)   # a path-string context is what the client reads
        self.assertEqual(request["state"]["context"], {"docs/retry.md": "retry policy\n"})

    def test_the_old_rerun_shape_is_rejected_by_the_receiver(self):
        sent = {**self.assignment("dev-sanity-assignment-vars.json"), "context": [{"path": "docs/retry.md", "why": "x"}]}
        client = load_client()
        with self.assertRaises(client.JevError):
            client.assignment_requests(sent)

    def test_selection_entry_keys_in_the_instruction_are_the_ones_sanity_merge_requires(self):
        text = (AGENTS / "dev-sanity.md").read_text()
        paragraph = text[text.index("`sanity-merge` accepts exactly these keys per entry:"):]
        paragraph = paragraph[:paragraph.index("\n\n")]
        documented = set(re.findall(r"`(\w+)`", paragraph.split("entry:", 1)[1].split(". ", 1)[0]))
        merge = (ROOT / "scripts/sanity-merge").read_text()
        required = ast.literal_eval(re.search(r"required = (\{[^}]*\})", merge).group(1))
        self.assertEqual(documented, required)
        self.assertIn("`{reviewer, context, reply}`", paragraph)
        self.assertIn("set(rerun) != {'reviewer', 'context', 'reply'}".replace("'", '"'), merge)


class ReviewerHandoffTests(unittest.TestCase):
    """quality-mgr / plan review -> reviewers, and -> fix-round-scope."""

    def test_ceremony_qa_instruction_names_its_declared_inputs(self):
        declared = inputs_json("ceremony-qa.md")
        checked = 0
        for template in ("qa-template.xml.j2", "plan-review-template.xml.j2"):
            text = (TEMPLATES / template).read_text()
            for mention in re.findall(r"`ceremony-qa` with (.*?)\.", text):
                with self.subTest(template=template):
                    checked += 1
                    keys = set(re.findall(r"`(\w+)` =", mention))
                    self.assertTrue(keys, mention)
                    self.assertLessEqual(keys, set(declared))
                    self.assertIn(f"`review_mode` = `{declared['review_mode']}`", mention)
        self.assertGreater(checked, 0)

    def test_reviewer_assignments_render_valid_json_matching_the_receivers_agent_file_when_present(self):
        for template in sorted(TEMPLATES.glob("*-assignment.json.j2")):
            reviewer = template.name.removesuffix("-assignment.json.j2")
            if reviewer == "dev-sanity":
                continue
            with self.subTest(reviewer=reviewer):
                sent = json.loads(render(template.name, EXAMPLES / f"{reviewer}-assignment-vars.json"))
                agent = AGENTS / f"{reviewer}.md"
                if not agent.is_file() or "## Inputs" not in agent.read_text():
                    self.skipTest(f"{reviewer}.md is absent or declares no ## Inputs JSON here; sender checked as valid JSON only")
                self.assertLessEqual(set(sent), set(inputs_json(f"{reviewer}.md")))

    def test_fix_round_scope_flags_in_the_senders_exist_in_the_script(self):
        script = (ROOT / "scripts/fix-round-scope").read_text()
        declared = set(re.findall(r'add_argument\("(--[\w-]+)"', script)) | {"-h", "--help"}
        commands = set(re.search(r'choices=\(([^)]*)\)', script).group(1).replace('"', "").replace(" ", "").split(","))
        seen = 0
        for template in ("qa-template.xml.j2", "plan-review-template.xml.j2"):
            text = (TEMPLATES / template).read_text()
            for command, rest in re.findall(r"fix-round-scope (\w+) ((?:--?[\w-]+|<[^>]*>|\{\{.*?\}\}|[\w./<>-]+| )*)", text):
                with self.subTest(template=template, command=command):
                    seen += 1
                    self.assertIn(command, commands)
                    self.assertLessEqual(set(re.findall(r"(?<!\S)(--[\w-]+)", rest)), declared)
        self.assertGreater(seen, 0)


if __name__ == "__main__":
    unittest.main()
