"""Wheel target planning and artifact coverage without publication."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_python import wheel_targets, verify_platforms

TARGETS = [
 {"id":"linux-x86_64","os":"ubuntu-latest","target":"x86_64-unknown-linux-gnu","platform":"manylinux_2_28_x86_64","manylinux":"2_28"},
 {"id":"linux-aarch64","os":"ubuntu-24.04-arm","target":"aarch64-unknown-linux-gnu","platform":"manylinux_2_28_aarch64","manylinux":"2_28"},
 {"id":"macos-x86_64","os":"macos-15-intel","target":"x86_64-apple-darwin","platform":"macosx_10_13_x86_64","deployment_target":"10.13"},
 {"id":"macos-arm64","os":"macos-latest","target":"aarch64-apple-darwin","platform":"macosx_11_0_arm64","deployment_target":"11.0"},
 {"id":"windows-x86_64","os":"windows-latest","target":"x86_64-pc-windows-msvc","platform":"win_amd64"},
]


@pytest.mark.parametrize("wheel", ["cp310-abi3-win_amd64", {**TARGETS[0],"os":"windows-latest"}, {**TARGETS[2],"deployment_target":"11.0"}, {**TARGETS[0],"manylinux":"off"}])
def test_bad_wheel_contracts_rejected(wheel):
    with pytest.raises(ValueError):
        wheel_targets({"wheels":[wheel]})


def test_legacy_runner_strings_remain_supported():
    for runner in ["ubuntu-latest","ubuntu-24.04-arm","macos-latest","windows-latest"]:
        target, = wheel_targets({"wheels":[runner]})
        assert target["os"] == runner
        assert target["target"] == ""
