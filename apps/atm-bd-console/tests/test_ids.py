from __future__ import annotations

from atm_bd_console.ids import short_id


def test_short_id_strips_only_the_root_prefix_and_its_dash() -> None:
    assert short_id("atm-bh-6", "atm") == "bh-6"
    assert short_id("atm-bh-6.group-dev", "atm") == "bh-6.group-dev"
    assert short_id("atmx-bh-6", "atm") == "atmx-bh-6"
    assert short_id("atm", "atm") == "atm"
