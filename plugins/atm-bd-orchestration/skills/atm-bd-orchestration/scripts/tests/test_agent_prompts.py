from pathlib import Path


AGENTS = Path(__file__).resolve().parents[4] / "agents"


def test_sanity_prompts_describe_ordered_linking_and_failures():
    dev = (AGENTS / "dev-sanity.md").read_text()
    stack = (AGENTS / "sanity-stack.md").read_text()
    for phrase in ("more than one open sanity task", "one spawn at a time", "never two in flight",
                   "only then run `atm-task-close.py", "sanity_round` unchanged", "keep the sanity task active",
                   "send the holder one plain", "re-spawn after", "report the evidence to the lead",
                   "one Jev client call", "up to three times", "llm_elapsed_s", "API_FAILURE"):
        assert phrase in dev
    for phrase in ("exactly one next-top layer", "Never rewrite a layer with children", "Make no ATM writes"):
        assert phrase in stack
    for prompt in (dev, stack):
        for forbidden in ("class bead", "outage bead", "hold on re-dispatch", "citation", "checker-defect", "sanity-run-table"):
            assert forbidden not in prompt
