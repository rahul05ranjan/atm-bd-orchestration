from __future__ import annotations

from atm_bd_console import collision, facts


def test_collisions_is_owned_by_the_collision_module() -> None:
    # The sprint design names collision.py as the owner of detection; facts.py keeps only sprint facts.
    assert collision.collisions.__module__ == "atm_bd_console.collision"
    assert not hasattr(facts, "collisions")
