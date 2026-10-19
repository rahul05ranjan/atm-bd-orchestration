"""Phase-wide bead validation and stack metadata contracts."""
import copy
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bead_schema  # noqa: E402


FAMILY = {"branch": "sprint/bi-7", "worktree": "/work/bi-7", "pr_target": "main", "difficulty": "normal"}


@pytest.mark.parametrize("edge", ["blocks", "validates"])
def test_sanity_accepts_one_dev_link(edge):
    bead = {"id": "atm-bi-7.group-sanity", "metadata": {**FAMILY, "dev_bead": "atm-bi-7.group-dev"},
            "dependencies": [{"type": edge, "depends_on_id": "atm-bi-7.group-dev"}]}
    assert bead_schema.problems(bead, bead_schema.SanityBead) == []


def test_sanity_rejects_absent_or_duplicate_dev_links():
    bead = {"id": "atm-phase-bi.15-sanity", "metadata": {**FAMILY, "dev_bead": "atm-phase-bi.15"}}
    assert bead_schema.problems(bead, bead_schema.SanityBead)
    bead["dependencies"] = [{"type": edge, "depends_on_id": "atm-phase-bi.15"} for edge in ("blocks", "validates")]
    assert bead_schema.problems(bead, bead_schema.SanityBead)


def test_stack_layers_have_all_fields_and_preserve_order():
    layer = {"branch": "first", "pr": 1, "head": "abc", "base": "trunk", "linked": True}
    second = {**layer, "branch": "second", "pr": 2}
    stack = {"trunk": "main", "stack_number": None, "layers": [layer, second]}
    assert bead_schema.StackMetadata.model_validate(stack).layers[-1].branch == "second"
    for key in layer:
        incomplete = copy.deepcopy(stack)
        del incomplete["layers"][0][key]
        with pytest.raises(ValidationError):
            bead_schema.StackMetadata.model_validate(incomplete)


def test_validate_phase_includes_qa_and_unknown_stage():
    qa = {"id": "atm-phase-bi.18-qa", "labels": ["stage:qa"],
          "metadata": {**FAMILY, "checked_bead": "atm-phase-bi.18", "round": 1}}
    assert bead_schema.validate_phase([qa]) == []
    del qa["metadata"]["branch"]
    errors = bead_schema.validate_phase([qa, {"id": "atm-bi-unexpected", "labels": ["stage:unexpected"]}])
    assert any(error.startswith("atm-phase-bi.18-qa: branch") for error in errors)
    assert "atm-bi-unexpected: no model" in errors
