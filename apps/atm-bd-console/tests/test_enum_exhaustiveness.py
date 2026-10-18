import ast
from pathlib import Path

import pytest

from atm_bd_console import icons, status_table

MATCH_SITES = (
    (icons, "icon"), (icons, "merge_icon"),
    (status_table, "_sprint_status"),
)


@pytest.mark.parametrize("module,function_name", MATCH_SITES)
def test_enum_dispatch_ends_in_assert_never(module, function_name):
    source = ast.parse(Path(module.__file__).read_text())
    function = next(node for node in source.body
                    if isinstance(node, ast.FunctionDef) and node.name == function_name)
    dispatch = next(node for node in ast.walk(function) if isinstance(node, ast.Match))
    fallback = dispatch.cases[-1]
    assert isinstance(fallback.pattern, ast.MatchAs)
    expected = ast.parse("match value:\n    case _ as unreachable:\n        assert_never(unreachable)")
    assert ast.dump(fallback.pattern) == ast.dump(expected.body[0].cases[0].pattern)
    assert ast.dump(fallback.body[0]) == ast.dump(ast.parse("assert_never(unreachable)").body[0])
