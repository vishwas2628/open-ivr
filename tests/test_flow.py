"""Flow parsing, serialisation and validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import SAMPLE_FLOW, write_flow

from openivr.flow import ACTIONS, Flow, FlowError, menu_tree, validate


def test_load_sample_flow(cfg, flow: Flow) -> None:
    assert flow.start_menu == "main"
    assert set(flow.menus) == {"main", "support"}
    assert flow.welcome == "welcome"
    assert flow.goodbye == "goodbye"
    action = flow.menus["main"].options["1"].action
    assert action.type == "dial"
    assert action.endpoint == "PJSIP/1001"
    assert action.label == "Sales"
    vm = flow.menus["main"].options["3"].action
    assert vm.type == "voicemail"
    assert vm.mailbox == "support"
    assert vm.greeting == "vm-greeting"
    assert vm.after is not None and vm.after.type == "hangup"


def test_roundtrip_is_stable(tmp_path: Path, flow: Flow) -> None:
    first = flow.to_dict()
    again = Flow.from_dict(first).to_dict()
    assert again == first
    path = flow.save(tmp_path / "saved.json")
    assert Flow.load(path).to_dict() == first


def test_defaults_are_filled(flow: Flow) -> None:
    support = flow.menus["support"]
    assert support.timeout.seconds == 3
    assert support.timeout.fail_action is None
    assert support.invalid.max_retries == 1
    assert support.options["9"].action.type == "goto"


def test_prompts_collects_every_reference(flow: Flow) -> None:
    prompts = flow.prompts()
    assert {
        "welcome",
        "goodbye",
        "main-menu",
        "support-menu",
        "timeout-msg",
        "invalid-msg",
        "vm-greeting",
    } <= prompts


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FlowError):
        Flow.load(tmp_path / "nope.json")


def test_broken_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(FlowError):
        Flow.load(path)


def test_empty_menus_rejected() -> None:
    with pytest.raises(FlowError):
        Flow.from_dict({"menus": {}})


def test_menu_lookup_error(flow: Flow) -> None:
    with pytest.raises(FlowError):
        flow.menu("nope")


def test_valid_sample(flow: Flow) -> None:
    errors = validate(flow.to_dict())
    assert errors == []


def test_missing_start_menu() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["start_menu"] = "ghost"
    errors = validate(data)
    assert any("start_menu" in e for e in errors)


def test_unknown_action_type() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["1"]["action"] = "teleport"
    errors = validate(data)
    assert any("teleport" in e for e in errors)


def test_dial_without_endpoint() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["1"].pop("endpoint")
    errors = validate(data)
    assert any("endpoint" in e for e in errors)


def test_voicemail_without_greeting() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["3"].pop("greeting")
    errors = validate(data)
    assert any("greeting" in e for e in errors)


def test_collect_bounds() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["4"] = {
        "action": "collect",
        "min_digits": 9,
        "max_digits": 3,
        "next": {"action": "hangup"},
    }
    errors = validate(data)
    assert any("digits" in e for e in errors)


def test_unknown_menu_target() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["2"]["target"] = "ghost"
    errors = validate(data)
    assert any("ghost" in e for e in errors)


def test_unreachable_menu() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["orphan"] = {"prompt": "orphan", "options": {}}
    errors = validate(data)
    assert any("orphan" in e and "reachable" in e for e in errors)


def test_option_limit() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    options = data["menus"]["support"]["options"]
    for digit in "12345678":
        options[digit] = {"action": "repeat"}
    errors = validate(data, max_options=5)
    assert any("options" in e for e in errors)


def test_depth_limit() -> None:
    data: dict[str, Any] = {
        "start_menu": "a",
        "menus": {
            "a": {"options": {"1": {"action": "submenu", "target": "b"}}},
            "b": {"options": {"1": {"action": "submenu", "target": "c"}}},
            "c": {"options": {"1": {"action": "submenu", "target": "d"}}},
            "d": {"options": {"9": {"action": "hangup"}}},
        },
    }
    assert validate(data, max_depth=5) == []
    errors = validate(data, max_depth=2)
    assert errors and "nested" in errors[0]


def test_bad_digit_key() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["menus"]["main"]["options"]["ab"] = {"action": "repeat"}
    errors = validate(data)
    assert any("ab" in e for e in errors)


def test_media_validation(tmp_path: Path) -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    errors = validate(
        data,
        available_sounds={
            "welcome",
            "goodbye",
            "main-menu",
            "support-menu",
            "timeout-msg",
            "invalid-msg",
        },
    )
    assert any("vm-greeting" in e for e in errors)
    everything = {p for p in Flow.from_dict(data).prompts()}
    assert validate(data, available_sounds=everything) == []


def test_time_route_validation() -> None:
    data = copy.deepcopy(SAMPLE_FLOW)
    data["time_route"] = {
        "timezone": "UTC",
        "business_menu": "main",
        "after_hours_menu": "ghost",
        "hours": {"mon": ["09:00-18:00"]},
    }
    errors = validate(data)
    assert any("ghost" in e for e in errors)


def test_menu_tree_shape(flow: Flow) -> None:
    tree = menu_tree(flow)
    assert tree["name"] == "main"
    assert {child["digit"] for child in tree["children"]} == {"1", "2", "3"}
    submenu = next(c for c in tree["children"] if c["digit"] == "2")
    assert submenu["action"] == "submenu"
    assert [child["digit"] for child in submenu["children"]] == ["1", "9"]
    assert tree["children"][0]["label"]


def test_actions_are_documented() -> None:
    assert {
        "dial",
        "submenu",
        "voicemail",
        "collect",
        "repeat",
        "hangup",
        "goto",
        "time_route",
        "goback",
        "parent",
    } == ACTIONS


def test_save_is_atomic(tmp_path: Path, flow: Flow) -> None:
    target = tmp_path / "deep" / "flow.json"
    flow.save(target)
    assert target.exists()
    assert not target.with_suffix(".json.tmp").exists()
    assert json.loads(target.read_text(encoding="utf-8"))["start_menu"] == "main"


def test_validate_accepts_a_written_file(project: Path) -> None:
    from openivr.flow import validate as validate_fn

    path = write_flow(project / "data" / "ivr_flow.json")
    assert validate_fn(json.loads(path.read_text(encoding="utf-8"))) == []


def test_prompts_excludes_full_media_uris() -> None:
    """``sound:``/``tone:``/... are not files, so they must not look missing."""
    flow = Flow.from_dict(
        {
            "version": 1,
            "start_menu": "main",
            "welcome": "sound:demo-congrats",
            "goodbye": "tone:silence/1",
            "menus": {
                "main": {
                    "prompt": "digits:1",
                    "timeout": {"prompt": "numbers:1", "seconds": 2},
                    "invalid": {"prompt": None, "max_retries": 1},
                    "options": {
                        "1": {"action": "goto", "menu": "sales", "label": "sales"},
                        "2": {
                            "action": "voicemail",
                            "mailbox": "sales",
                            "greeting": "file:/tmp/nope.wav",
                            "label": "vm",
                        },
                        "3": {"action": "hangup", "label": "bye"},
                    },
                },
                "sales": {
                    "prompt": "vm-sales",
                    "timeout": {"seconds": 1},
                    "options": {},
                },
            },
        }
    )
    assert flow.prompts() == {"vm-sales"}


def test_prompts_collects_every_menu_and_retry_prompt() -> None:
    flow = Flow.from_dict(
        {
            "version": 1,
            "start_menu": "main",
            "welcome": "welcome",
            "goodbye": "goodbye",
            "time_route": {"prompt": "route", "hours": {"mon": [["09:00", "17:00"]]}},
            "menus": {
                "main": {
                    "prompt": "main-prompt",
                    "timeout": {"seconds": 2, "max_retries": 2, "prompt": "try-again"},
                    "invalid": {"max_retries": 1, "prompt": "not-valid"},
                    "options": {"1": {"action": "hangup", "label": "x"}},
                }
            },
        }
    )
    assert flow.prompts() == {
        "welcome",
        "goodbye",
        "route",
        "main-prompt",
        "try-again",
        "not-valid",
    }
