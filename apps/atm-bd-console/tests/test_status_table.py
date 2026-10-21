from dataclasses import replace
from datetime import UTC, datetime, timedelta
from inspect import getdoc

from contract_samples import BEAD, CONFIG, GH_STATUS, INPUTS, MEMBER, PHASE, PR_ROW, SNAPSHOT, SPRINT, STEP, TASK

from atm_bd_console import runner, status_table
from atm_bd_console.enums import AgentIcon, CiState, MergeState, PrState, SprintState
from atm_bd_console.facts import ActiveAgent, CiFacts, PrFacts, SprintFacts, _gavel, agent_icon_of, sprint_facts
from atm_bd_console.icons import icon
from atm_bd_console.model import tick
from atm_bd_console.result import Ok
from atm_bd_console.runner import _durable_state
from atm_bd_console.status_table import STATE, markdown, rich_table, rows
from atm_bd_console.types import INITIAL_STATE

NOW = datetime(2026, 10, 17, tzinfo=UTC)


def _snapshot():
    sprint = replace(SPRINT, status=SprintState.IN_PROGRESS)
    step = replace(STEP, kind="dev", carry_forward=None, sprint_bead=sprint.id, closed_at=None)
    task = replace(TASK, id=step.bead_id, state_since=NOW)
    return replace(SNAPSHOT, phase_data=replace(PHASE, sprints=(sprint,), steps=(step,)), task_data=(task,),
                   member_data=(replace(MEMBER, harness="codex-cli", model="terra"),),
                   gh=replace(GH_STATUS, mode="graphql", ok=True, error=None, truncated=False), prs=())


def test_rows_use_active_tasks_order_and_ignore_queued():
    snapshot = _snapshot()
    earlier = replace(snapshot.task_data[0], agent="other", state_since=NOW - timedelta(seconds=1))
    queued = replace(earlier, agent="queued", state="assigned")
    other = replace(MEMBER, name="other", harness="claude-code", model="opus")
    snapshot = replace(snapshot, task_data=(*snapshot.task_data, earlier, queued),
                       member_data=(*snapshot.member_data, other))
    assert rows(snapshot)[0].state == (AgentIcon.OPUS, AgentIcon.TERRA)


def test_sprint_producer_list_covers_every_enum_member():
    facts = replace(sprint_facts(_snapshot())[0], active=(), gavel=None)
    for state in SprintState:
        current = replace(facts, status=state)
        assert next(value for producer in STATE if (value := producer(current))) == (state,)


def test_rows_preserve_ci_and_pr_enum_values_and_absent_source_defaults(monkeypatch):
    facts = replace(sprint_facts(_snapshot())[0], active=(), gavel=None)

    def row_for(current):
        monkeypatch.setattr(status_table, "sprint_facts", lambda _snapshot, current=current: (current,))
        return rows(_snapshot())[0]

    for state in CiState:
        assert row_for(replace(facts, ci=CiFacts(state))).ci is state
    for state in PrState:
        assert row_for(replace(facts, pr=PrFacts(state, None))).pr_state is state
    missing = row_for(replace(facts, ci=None, pr=None))
    assert missing.ci is CiState.NONE
    assert missing.pr_state is PrState.NONE


def test_child_task_inherits_qa_step_role_through_parent_chain():
    snapshot = _snapshot()
    qa = replace(snapshot.phase_data.steps[0], kind="qa")
    child = replace(BEAD, id="child-task", parent=qa.bead_id)
    snapshot = replace(snapshot, phase_data=replace(snapshot.phase_data, steps=(qa,)),
                       task_data=(replace(snapshot.task_data[0], id=child.id),), task_bead_data=(child,))
    assert rows(snapshot)[0].state == (AgentIcon.QA_RUNNING,)


def test_sprint_facts_cover_active_queued_qa_and_pr_sources():
    snapshot = _snapshot()
    sprint = snapshot.phase_data.sprints[0]
    base = SprintFacts(sprint.id, "bh-6", SprintState.IN_PROGRESS, (), None, None, CiFacts(CiState.NONE), ())
    assert sprint_facts(snapshot) == (replace(base, active=(ActiveAgent("arch-ctm", AgentIcon.TERRA, NOW),)),)
    queued = replace(snapshot, task_data=(replace(snapshot.task_data[0], state="assigned"),))
    assert sprint_facts(queued) == (base,)
    qa = replace(snapshot.phase_data.steps[0], kind="qa")
    running_qa = replace(snapshot, phase_data=replace(snapshot.phase_data, steps=(qa,)))
    assert sprint_facts(running_qa) == (replace(base, active=(ActiveAgent("arch-ctm", AgentIcon.QA_RUNNING, NOW),)),)
    closed_qa = replace(qa, closed_at=NOW, close_reason="PASS")
    fix = replace(qa, bead_id="fix", kind="fix", closed_at=NOW + timedelta(seconds=1))
    invalidated = replace(snapshot, task_data=(), phase_data=replace(snapshot.phase_data, steps=(closed_qa, fix)))
    assert sprint_facts(invalidated) == (base,)
    pr = replace(PR_ROW.pr, state=PrState.OPEN, ci=CiState.PASS, head=sprint.branch)
    open_row = replace(PR_ROW, pr=pr, merge=None)
    merged_pr = replace(pr, state=PrState.MERGED, number=pr.number + 1)
    merge = replace(PR_ROW.merge, branch=pr.head, target=pr.base, state=MergeState.CONFLICT)
    merged_row = replace(PR_ROW, pr=merged_pr, merge=merge)
    with_prs = replace(snapshot, task_data=(), prs=(merged_row, open_row))
    assert sprint_facts(with_prs) == (replace(base, ci=CiFacts(CiState.PASS), pr=PrFacts(PrState.OPEN, open_row)),)
    merged_only = replace(with_prs, prs=(merged_row,))
    assert sprint_facts(merged_only) == (replace(base, ci=CiFacts(CiState.PASS), pr=PrFacts(PrState.MERGED, merged_row)),)
    gh_off = replace(with_prs, gh=replace(snapshot.gh, mode="off", ok=None))
    assert sprint_facts(gh_off) == (replace(base, ci=CiFacts(CiState.UNKNOWN), pr=PrFacts(PrState.UNKNOWN, open_row),
                                           problems=("GitHub data off",)),)


def test_gavel_needs_a_closed_dev_step_and_follows_open_finding_children():
    snapshot = _snapshot()
    dev = replace(snapshot.phase_data.steps[0], closed_at=NOW)
    finding = replace(STEP, bead_id="finding", kind="finding", carry_forward=None, sprint_bead=SPRINT.id,
                      status=SprintState.OPEN, closed_at=None, severity="blocking")
    snapshot = replace(snapshot, task_data=(), phase_data=replace(snapshot.phase_data, steps=(dev, finding)))
    assert rows(snapshot)[0].state == (AgentIcon.QA_FAIL,)
    closed = replace(finding, status=SprintState.CLOSED, closed_at=NOW + timedelta(seconds=1))
    snapshot = replace(snapshot, phase_data=replace(snapshot.phase_data, steps=(dev, closed)))
    assert rows(snapshot)[0].state == (AgentIcon.QA_PASS,)
    snapshot = replace(snapshot, phase_data=replace(snapshot.phase_data, steps=(replace(dev, closed_at=None), finding)))
    assert rows(snapshot)[0].state == (SprintState.IN_PROGRESS,)


def test_qa_kind_and_sprint_result_distinction_are_documented():
    assert all(term in (getdoc(agent_icon_of) or "") for term in ("qa", "sprint_qa", "fix"))
    assert all(term in (getdoc(_gavel) or "") for term in ("dev", "finding", "QA verdict"))


def test_fix_verification_is_an_active_agent_and_stored_qa_verdicts_never_set_the_gavel():
    snapshot = _snapshot()
    dev = replace(snapshot.phase_data.steps[0], closed_at=NOW)
    sprint_qa = replace(STEP, bead_id="sprint-qa", kind="qa", carry_forward=None, sprint_bead=SPRINT.id,
                        closed_at=NOW + timedelta(seconds=1), close_reason="FAIL")
    fix_qa = replace(sprint_qa, bead_id="fix-qa", carry_forward="finding", closed_at=NOW + timedelta(seconds=2),
                     close_reason="FAIL")
    snapshot = replace(snapshot, task_data=(), phase_data=replace(snapshot.phase_data, steps=(dev, sprint_qa, fix_qa)))
    assert rows(snapshot)[0].state == (AgentIcon.QA_PASS,)
    running = replace(snapshot, task_data=(replace(TASK, id=fix_qa.bead_id, state_since=NOW),))
    assert sprint_facts(running)[0].active[0].icon is AgentIcon.QA_RUNNING
    assert rows(running)[0].state == (AgentIcon.QA_RUNNING,)


def _sprint_with(snapshot, status, steps):
    sprint = replace(snapshot.phase_data.sprints[0], status=status)
    return replace(snapshot, phase_data=replace(snapshot.phase_data, sprints=(sprint,), steps=steps), task_data=())


def test_only_blocking_children_set_the_gavel():
    snapshot = _snapshot()
    dev = replace(snapshot.phase_data.steps[0], closed_at=NOW)

    def child(bead_id, kind, severity, status=SprintState.OPEN):
        return replace(STEP, bead_id=bead_id, kind=kind, carry_forward=None, sprint_bead=SPRINT.id,
                       status=status, closed_at=None, severity=severity)

    def gavel(*steps):
        return rows(replace(snapshot, task_data=(), phase_data=replace(snapshot.phase_data, steps=(dev, *steps))))[0].state

    assert gavel(child("minor", "finding", "minor")) == (AgentIcon.QA_PASS,)
    assert gavel(child("important", "finding", "important")) == (AgentIcon.QA_PASS,)
    assert gavel(child("fix", "fix", "blocking")) == (AgentIcon.QA_FAIL,)
    assert gavel(child("fix", "fix", "blocking", SprintState.CLOSED)) == (AgentIcon.QA_PASS,)
    assert gavel(child("minor", "finding", "minor"), child("fix", "fix", "blocking")) == (AgentIcon.QA_FAIL,)


def test_blocked_and_ready_sprints_with_no_agent_draw_their_status_icon():
    snapshot = _snapshot()
    dev = replace(snapshot.phase_data.steps[0], closed_at=NOW)
    finding = replace(STEP, bead_id="finding", kind="finding", carry_forward=None, sprint_bead=SPRINT.id,
                      status=SprintState.OPEN, closed_at=None)
    assert rows(_sprint_with(snapshot, SprintState.BLOCKED, ()))[0].state == (SprintState.BLOCKED,)
    assert rows(_sprint_with(snapshot, SprintState.READY, ()))[0].state == (SprintState.READY,)
    assert rows(_sprint_with(snapshot, SprintState.BLOCKED, (dev, finding)))[0].state == (SprintState.BLOCKED,)


def test_closed_sprint_draws_closed_icon_not_gavel():
    snapshot = _snapshot()
    dev = replace(snapshot.phase_data.steps[0], closed_at=NOW)
    assert rows(_sprint_with(snapshot, SprintState.CLOSED, (dev,)))[0].state == (SprintState.CLOSED,)
    assert icon(SprintState.CLOSED)[0] == "\U000f05e0"


def test_pr_preference_and_conflict_require_matching_base():
    snapshot = _snapshot()
    pr = replace(PR_ROW.pr, state=PrState.OPEN, ci=CiState.FAIL_WINDOWS, failed_checks=("windows test",), head=SPRINT.branch)
    conflict = replace(PR_ROW.merge, branch=pr.head, target=pr.base, state=MergeState.CONFLICT)
    open_row = replace(PR_ROW, pr=pr, merge=conflict)
    merged_row = replace(open_row, pr=replace(pr, number=pr.number + 10, state=PrState.MERGED))
    snapshot = replace(snapshot, prs=(merged_row, open_row))
    facts = sprint_facts(snapshot)[0]
    assert facts.pr == PrFacts(PrState.CONFLICT, open_row)
    assert facts.ci == CiFacts(CiState.FAIL_WINDOWS)
    snapshot = replace(snapshot, prs=(replace(open_row, merge=replace(conflict, target="wrong")),))
    assert rows(snapshot)[0].pr_state is PrState.OPEN


def test_merged_wins_over_closed_conflict_is_ignored_and_empty_url_is_unlinked():
    snapshot = _snapshot()
    merged = replace(PR_ROW.pr, state=PrState.MERGED, ci=CiState.PASS, head=SPRINT.branch, url="")
    conflict = replace(PR_ROW.merge, branch=merged.head, target=merged.base, state=MergeState.CONFLICT)
    merged_row = replace(PR_ROW, pr=merged, merge=conflict)
    closed_row = replace(merged_row, pr=replace(merged, state=PrState.CLOSED, number=merged.number + 1))
    snapshot = replace(snapshot, prs=(closed_row, merged_row))
    row = rows(snapshot)[0]
    assert row.pr_state is PrState.MERGED
    assert row.ci is CiState.PASS
    assert f" #{merged.number} |" in markdown((row,))
    assert "](" not in markdown((row,))


def test_unknown_sprint_and_github_failures_are_reported():
    from atm_bd_console.atm_source import sprint_state_of

    snapshot = _snapshot()
    sprint = replace(snapshot.phase_data.sprints[0], status=sprint_state_of("wat"), raw_status="wat")
    phase = replace(snapshot.phase_data, sprints=(sprint,), closed=False)
    inputs = replace(INPUTS, phase=Ok(phase), tasks=Ok(()), members=Ok(snapshot.member_data),
                     gh=replace(snapshot.gh, mode="off", ok=None, paused=False), prs=Ok(()), merges=(), remotes=None)
    result, _, _ = tick(inputs, INITIAL_STATE, NOW, lambda _: None)
    row = rows(result)[0]
    assert row.state == (SprintState.UNKNOWN,)
    assert row.ci is CiState.UNKNOWN and row.pr_state is PrState.UNKNOWN
    table = rich_table((row,))
    state_cell = table.columns[1]._cells[0]
    ci_cell = table.columns[2]._cells[0]
    pr_cell = table.columns[3]._cells[0]
    assert (state_cell.plain, state_cell.spans[0].style) == ("?", "#2D81E0")
    assert (ci_cell.plain, ci_cell.style) == ("?", "#2D81E0")
    assert (pr_cell.plain, pr_cell.style) == ("?", "#2D81E0")
    assert any("wat" in problem.text for problem in result.problems)
    assert any("GitHub data off" in problem.text for problem in result.problems)


def test_merge_error_keeps_pr_state_and_reports_problem():
    snapshot = _snapshot()
    pr = replace(PR_ROW.pr, state=PrState.OPEN, head=SPRINT.branch)
    merge = replace(PR_ROW.merge, branch=pr.head, target=pr.base, state=MergeState.ERROR)
    snapshot = replace(snapshot, prs=(replace(PR_ROW, pr=pr, merge=merge),))
    assert rows(snapshot)[0].pr_state is PrState.OPEN
    inputs = replace(INPUTS, phase=Ok(replace(snapshot.phase_data, closed=False)), tasks=Ok(()),
                     members=Ok(snapshot.member_data), gh=snapshot.gh, prs=Ok((pr,)), merges=(Ok(merge),), remotes=None)
    result, _, _ = tick(inputs, INITIAL_STATE, NOW, lambda _: None)
    # Only a ready step is blocked by an unavailable merge check; the dispatch hold reports it, not a merge fault.
    assert not any("merge check" in problem.text for problem in result.problems)


def test_merge_check_states_are_open_pr_only_and_named():
    snapshot = _snapshot()
    pr = replace(PR_ROW.pr, state=PrState.OPEN, head=SPRINT.branch)
    error = replace(PR_ROW.merge, branch=pr.head, target=pr.base, state=MergeState.ERROR)
    closed = replace(pr, state=PrState.CLOSED)
    assert sprint_facts(replace(snapshot, prs=(replace(PR_ROW, pr=closed, merge=error),)))[0].problems == ()
    closed_inputs = replace(INPUTS, phase=Ok(replace(snapshot.phase_data, closed=False)), tasks=Ok(()),
                            members=Ok(snapshot.member_data), gh=snapshot.gh, prs=Ok((closed,)), merges=(Ok(error),),
                            remotes=None)
    result, _, _ = tick(closed_inputs, INITIAL_STATE, NOW, lambda _: None)
    assert not any(problem.key.startswith("merge_error:") for problem in result.problems)
    result, _, _ = tick(replace(closed_inputs, prs=Ok((pr,))), INITIAL_STATE, NOW, lambda _: None)
    assert not any(problem.key.startswith("merge_error:") for problem in result.problems)

    unpushed = replace(error, state=MergeState.HEAD_NOT_PUSHED)
    facts = sprint_facts(replace(snapshot, prs=(replace(PR_ROW, pr=pr, merge=unpushed),)))[0]
    assert facts.problems == (f"head not pushed for {pr.head}",)
    inputs = replace(INPUTS, phase=Ok(replace(snapshot.phase_data, closed=False)), tasks=Ok(()),
                     members=Ok(snapshot.member_data), gh=snapshot.gh, prs=Ok((pr,)), merges=(Ok(unpushed),), remotes=None)
    result, _, _ = tick(inputs, INITIAL_STATE, NOW, lambda _: None)
    assert not any("merge check failed" in problem.text for problem in result.problems)

    deleted = replace(error, state=MergeState.BASE_DELETED)
    facts = sprint_facts(replace(snapshot, prs=(replace(PR_ROW, pr=pr, merge=deleted),)))[0]
    assert facts.problems == (f"base deleted for {pr.base}",)


def test_markdown_is_stable_and_escapes_sprint_ids():
    snapshot = _snapshot()
    sprint = replace(snapshot.phase_data.sprints[0], id="atm-bi-1|test\\x")
    snapshot = replace(snapshot, phase_data=replace(snapshot.phase_data, sprints=(sprint,), steps=()), task_data=())
    expected = ("| sprint | state | ci | pr |\n|---|:-:|:-:|---|\n"
                f"| bi-1\\|test\\\\x | {icon(SprintState.IN_PROGRESS)[0]} | {icon(CiState.NONE)[0]} | - |")
    assert markdown(rows(snapshot)) == expected
    assert markdown(rows(snapshot)) == expected


def test_collision_goes_in_the_leads_one_batch_and_failed_delivery_retries():
    snapshot = _snapshot()
    other = replace(snapshot.task_data[0], agent="other")
    inputs = replace(INPUTS, phase=Ok(replace(snapshot.phase_data, closed=False)),
                     tasks=Ok((*snapshot.task_data, other)), members=Ok(snapshot.member_data),
                     gh=snapshot.gh, prs=Ok(()), merges=(), remotes=None)
    result, actions, proposed = tick(inputs, INITIAL_STATE, NOW, lambda _: None)
    batches = [action for action in actions if action.kind == "batch"]
    assert [(action.to, action.kind) for action in actions] == [(inputs.lead, "batch")]
    collision = next(problem for problem in result.problems if problem.key.startswith("collision:"))
    assert "other" in collision.text and "arch-ctm" in collision.text
    assert collision.text in batches[0].text
    failed = replace(batches[0], outcome="failed timeout")
    retriable = _durable_state(INITIAL_STATE, proposed, actions, (failed,))
    _, again, _ = tick(inputs, retriable, NOW, lambda _: None)
    assert [action.kind for action in again] == ["batch"]
    durable = _durable_state(INITIAL_STATE, proposed, actions, (replace(batches[0], outcome="sent"),))
    _, again, second = tick(inputs, durable, NOW + timedelta(minutes=5), lambda _: None)
    assert again == ()
    _, next_cycle, _ = tick(inputs, second, NOW + timedelta(minutes=10), lambda _: None)
    assert [action.kind for action in next_cycle] == ["batch"]


def test_collision_is_sent_once_per_cycle_not_once_per_tick(monkeypatch):
    snapshot = _snapshot()
    other = replace(snapshot.task_data[0], agent="other")
    inputs = replace(INPUTS, phase=Ok(replace(snapshot.phase_data, closed=False)),
                     tasks=Ok((*snapshot.task_data, other)), members=Ok(snapshot.member_data),
                     gh=snapshot.gh, prs=Ok(()), merges=(), remotes=None)
    sent = []
    monkeypatch.setattr(runner.atm_source, "send", lambda *args: sent.append(args) or Ok(None))

    class Store:
        def append_action(self, _line):
            return Ok(None)

    class Logger:
        def event(self, *_args, **_kwargs):
            return Ok(None)

    state = INITIAL_STATE
    for minutes in (0, 3, 6, 10):
        now = NOW + timedelta(minutes=minutes)
        if minutes == 10:
            inputs = replace(inputs, tasks=Ok((*inputs.tasks.value, replace(other, agent="third"))))
        result, actions, proposed = tick(inputs, state, now, lambda _: None)
        assert len([problem for problem in result.problems if problem.key.startswith("collision:")]) == 1
        reported, errors = runner._run_actions(replace(CONFIG, dry_run=False), actions, Store(), now, Logger())
        assert errors == ()
        state = _durable_state(state, proposed, actions, reported)
    assert len(sent) == 2
    assert all(call[0] == f"{inputs.lead}@{CONFIG.team}" and call[2:] == ("atm-bd", "tool") for call in sent)
    assert all(SPRINT.id in call[1] and STEP.worktree in call[1] and "arch-ctm" in call[1] and "other" in call[1]
               for call in sent)
    assert "third" in sent[1][1]


def test_twenty_spec_rows_and_rich_colours():
    statuses = (SprintState.CLOSED, SprintState.IN_PROGRESS, SprintState.IN_PROGRESS, SprintState.IN_PROGRESS,
                SprintState.IN_PROGRESS, SprintState.IN_PROGRESS, SprintState.READY, SprintState.BLOCKED,
                SprintState.IN_PROGRESS, SprintState.HOOKED, SprintState.IN_PROGRESS, SprintState.IN_PROGRESS,
                SprintState.IN_PROGRESS, SprintState.IN_PROGRESS, SprintState.IN_PROGRESS, SprintState.IN_PROGRESS,
                SprintState.IN_PROGRESS, SprintState.DEFERRED, SprintState.ACCEPTED, SprintState.NOT_READY)
    people = {2: ("qa", "qa", ""), 3: ("sanity", "sanity", ""), 4: ("luna", "dev", "luna"),
              5: ("fable", "dev", "fable"), 12: ("sol", "dev", "sol"), 13: ("terra", "dev", "terra"),
              14: ("astra", "dev", "astra"), 15: ("opus", "dev", "opus"),
              16: ("sonnet", "dev", "sonnet"), 17: ("haiku", "dev", "haiku")}
    pr_values = {1: (118, PrState.MERGED, CiState.PASS), 2: (120, PrState.OPEN, CiState.RUNNING),
                 3: (121, PrState.OPEN, CiState.FAIL_BUG), 4: (127, PrState.DRAFT, CiState.NONE),
                 6: (130, PrState.OPEN, CiState.FAIL_WINDOWS), 9: (132, PrState.OPEN, CiState.RUNNING),
                 11: (134, PrState.OPEN, CiState.PASS), 12: (135, PrState.OPEN, CiState.RUNNING),
                 13: (136, PrState.OPEN, CiState.FAIL_MACOS), 14: (137, PrState.OPEN, CiState.FAIL_LINUX),
                 15: (138, PrState.DRAFT, CiState.NONE), 16: (139, PrState.OPEN, CiState.FAIL_BUG),
                 17: (140, PrState.OPEN, CiState.PASS), 18: (141, PrState.CLOSED, CiState.NONE),
                 19: (142, PrState.MERGED, CiState.PASS)}
    sprints, steps, tasks, members, prs = [], [], [], [], []
    for index, status in enumerate(statuses, 1):
        sprint = replace(SPRINT, id=f"atm-bi-{index}", branch=f"sprint/bi-{index}", status=status)
        sprints.append(sprint)
        if index in people:
            agent, kind, model = people[index]
            step = replace(STEP, bead_id=f"{sprint.id}.{kind}", sprint_bead=sprint.id, kind=kind,
                           carry_forward=None, closed_at=None)
            steps.append(step)
            tasks.append(replace(TASK, id=step.bead_id, agent=agent, state_since=NOW))
            harness = "claude-code" if model in ("fable", "opus", "sonnet", "haiku") else "codex-cli"
            members.append(replace(MEMBER, name=agent, model=model, harness=harness))
        if index in (6, 11):
            steps.append(replace(STEP, bead_id=f"{sprint.id}.dev", sprint_bead=sprint.id, kind="dev",
                                 carry_forward=None, closed_at=NOW))
            steps.append(replace(STEP, bead_id=f"{sprint.id}.qa", sprint_bead=sprint.id, kind="qa",
                                 carry_forward=None, closed_at=NOW, close_reason="FAIL" if index == 6 else "PASS"))
        if index == 6:
            steps.append(replace(STEP, bead_id=f"{sprint.id}.finding", sprint_bead=sprint.id, kind="finding",
                                 carry_forward=None, status=SprintState.OPEN, closed_at=None, severity="blocking"))
        if index in pr_values:
            number, state, ci = pr_values[index]
            pr = replace(PR_ROW.pr, number=number, head=sprint.branch, state=state, ci=ci,
                         url=f"https://example.test/pr/{number}")
            merge = replace(PR_ROW.merge, branch=pr.head, target=pr.base, state=MergeState.CONFLICT) if index == 14 else None
            prs.append(replace(PR_ROW, pr=pr, merge=merge))
    snapshot = replace(_snapshot(), phase_data=replace(PHASE, sprints=tuple(sprints), steps=tuple(steps)),
                       task_data=tuple(tasks), member_data=tuple(members), prs=tuple(prs))
    expected_lines = (
        "| sprint | state | ci | pr |", "|---|:-:|:-:|---|",
        "| bi-1 | \U000f05e0 | \U000f062c | \U000f062d [#118](https://example.test/pr/118) |",
        "| bi-2 | \U000f029b | \U000f0898 | \U000f04c2 [#120](https://example.test/pr/120) |",
        "| bi-3 | \U000f01a6 | \U000f00e4 | \U000f04c2 [#121](https://example.test/pr/121) |",
        "| bi-4 | \U000f0f65 | \U000f01d8 | \U000f0cb6 [#127](https://example.test/pr/127) |",
        "| bi-5 | \U000f0717 | \U000f01d8 | - |",
        "| bi-6 | \U000f029b | \U000f05b3 | \U000f04c2 [#130](https://example.test/pr/130) |",
        "| bi-7 | \U000f040d | \U000f01d8 | - |", "| bi-8 | \U000f033e | \U000f01d8 | - |",
        "| bi-9 | \U000f051f | \U000f0898 | \U000f04c2 [#132](https://example.test/pr/132) |",
        "| bi-10 | \U000f06e2 | \U000f01d8 | - |",
        "| bi-11 | \U000f029b | \U000f062c | \U000f04c2 [#134](https://example.test/pr/134) |",
        "| bi-12 | \U000f05a8 | \U000f0898 | \U000f04c2 [#135](https://example.test/pr/135) |",
        "| bi-13 | \U000f01e7 | \U000f0035 | \U000f04c2 [#136](https://example.test/pr/136) |",
        "| bi-14 | \U000f0674 | \U000f033d | \U000f062c [#137](https://example.test/pr/137) |",
        "| bi-15 | \U000f06c4 | \U000f01d8 | \U000f0cb6 [#138](https://example.test/pr/138) |",
        "| bi-16 | \U000f0382 | \U000f00e4 | \U000f04c2 [#139](https://example.test/pr/139) |",
        "| bi-17 | \U000f0156 | \U000f062c | \U000f04c2 [#140](https://example.test/pr/140) |",
        "| bi-18 | \U000f03e5 | \U000f01d8 | \U000f073a [#141](https://example.test/pr/141) |",
        "| bi-19 | \U000f0513 | \U000f062c | \U000f062d [#142](https://example.test/pr/142) |",
        "| bi-20 | \U000f137c | \U000f01d8 | - |",
    )
    expected = "\n".join(expected_lines)
    values = rows(snapshot)
    assert markdown(values) == expected == markdown(rows(snapshot))
    table = rich_table(values)
    colours = ("#2DE057", "#4A5AF8", "#E551BA", "#FFE066", "#FF9F80", "#F85149", "#0077FF",
               "#FA8537", "#0077FF", "#2DE057", "#2EA043", "#FFA60D", "#2DE057", "#FFE066",
               "#FF8C66", "#FF784D", "#FF6533", "#FFB726", "#E551BA", "#D97757")
    assert tuple(cell.spans[0].style for cell in table.columns[1]._cells) == colours
    ci_colours = ("#2EA043", "#58A6FF", "#F85149", "#2D81E0", "#2D81E0", "#F85149", "#2D81E0",
                  "#2D81E0", "#58A6FF", "#2D81E0", "#2EA043", "#58A6FF", "#F85149", "#F85149",
                  "#2D81E0", "#F85149", "#2EA043", "#2D81E0", "#2EA043", "#2D81E0")
    pr_colours = ("#8957E5", "#2D81E0", "#2D81E0", "#FFA60D", "", "#2D81E0", "", "", "#2D81E0", "",
                  "#2D81E0", "#2D81E0", "#2D81E0", "#F85149", "#FFA60D", "#2D81E0", "#2D81E0",
                  "#F85149", "#8957E5", "")
    assert tuple(cell.style for cell in table.columns[2]._cells) == ci_colours
    assert tuple(cell.style for cell in table.columns[3]._cells) == pr_colours
