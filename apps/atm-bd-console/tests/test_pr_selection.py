import json
from dataclasses import replace
from itertools import product

import pytest
from contract_samples import PHASE, PR_ROW, SNAPSHOT, SPRINT

from atm_bd_console import facts, github_source
from atm_bd_console.command import Output
from atm_bd_console.enums import PrState
from atm_bd_console.pr_selection import select_pr
from atm_bd_console.result import Ok

PRIORITIES = (
    (PrState.OPEN, 2), (PrState.DRAFT, 2), (PrState.MERGED, 1), (PrState.CLOSED, 0),
    (PrState.NONE, -1), (PrState.CONFLICT, -1), (PrState.UNKNOWN, -1),
)
TEST_REPO = "owner/repo"


@pytest.mark.parametrize("first,second", tuple(product(PRIORITIES, repeat=2)))
def test_select_pr_prioritizes_state_then_number_independent_of_input_order(first, second):
    first_state, first_priority = first
    second_state, second_priority = second
    older = replace(PR_ROW.pr, state=first_state, number=10)
    newer = replace(PR_ROW.pr, state=second_state, number=20)
    expected = older if first_priority > second_priority else newer
    assert select_pr(iter((older, newer))) is expected
    assert select_pr(iter((newer, older))) is expected


def test_select_pr_empty_input():
    assert select_pr(iter(())) is None


def test_facts_routes_selection_through_shared_helper_and_preserves_row(monkeypatch):
    chosen = replace(PR_ROW, pr=replace(PR_ROW.pr, head=SPRINT.branch, state=PrState.CLOSED))
    other = replace(chosen, pr=replace(chosen.pr, number=chosen.pr.number + 1, state=PrState.OPEN))
    snapshot = replace(SNAPSHOT, phase_data=replace(PHASE, sprints=(SPRINT,)), prs=(chosen, other))
    calls = []

    def choose(candidates):
        calls.append(tuple(candidates))
        return chosen.pr

    monkeypatch.setattr(facts, "select_pr", choose)
    assert facts._selected_pr(SPRINT, (), snapshot) is chosen
    assert calls == [(chosen.pr, other.pr)]


def test_rest_routes_check_selection_through_shared_helper(monkeypatch):
    calls = []
    selections = []
    pulls = [
        {"number": number, "html_url": f"https://example/pr/{number}", "state": "open",
         "head": {"ref": "branch", "sha": f"sha-{number}"}, "base": {"ref": "base"}}
        for number in (10, 20)
    ]

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        body = pulls if argv == github_source.rest_argv(TEST_REPO) else {"total_count": 0, "check_runs": []}
        return Ok(Output(0, json.dumps(body)))

    def choose(candidates):
        selections.append(tuple(candidates))
        return selections[-1][0]

    monkeypatch.setattr(github_source.command, "run", run)
    monkeypatch.setattr(github_source, "select_pr", choose)
    result, status = github_source.open_prs("rest", "/repo", (("branch",),), None, TEST_REPO)
    assert isinstance(result, Ok) and status.ok
    assert [[pr.number for pr in candidates] for candidates in selections] == [[10, 20]]
    assert len(calls) == 2
    assert calls[1][-1] == f"repos/{TEST_REPO}/commits/sha-10/check-runs?per_page=100"
