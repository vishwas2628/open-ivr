"""Shared pytest fixtures and fakes for the openivr test suite.

Everything here is offline: no network, no Asterisk, and no writes outside
``tmp_path``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAMPLE_FLOW: dict[str, Any] = {
    "version": 1,
    "start_menu": "main",
    "welcome": "welcome",
    "goodbye": "goodbye",
    "menus": {
        "main": {
            "prompt": "main-menu",
            "timeout": {
                "seconds": 3,
                "prompt": "timeout-msg",
                "max_retries": 1,
                "fail_action": {"action": "hangup"},
            },
            "invalid": {"prompt": "invalid-msg", "max_retries": 1},
            "options": {
                "1": {"action": "dial", "label": "Sales", "endpoint": "PJSIP/1001"},
                "2": {"action": "submenu", "label": "Support", "target": "support"},
                "3": {
                    "action": "voicemail",
                    "label": "Voicemail",
                    "mailbox": "support",
                    "greeting": "vm-greeting",
                    "after": {"action": "hangup"},
                },
            },
        },
        "support": {
            "prompt": "support-menu",
            "timeout": {"seconds": 3, "max_retries": 1},
            "options": {
                "1": {"action": "repeat"},
                "9": {"action": "goto", "target": "main"},
            },
        },
    },
}


def write_flow(path: Path, flow: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(flow or SAMPLE_FLOW, indent=2), encoding="utf-8")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal project tree: data dirs, sounds, sample flow."""
    (tmp_path / "data" / "sounds").mkdir(parents=True)
    (tmp_path / "data" / "recordings").mkdir(parents=True)
    (tmp_path / "data" / "logs").mkdir(parents=True)
    write_flow(tmp_path / "data" / "ivr_flow.json")
    return tmp_path


@pytest.fixture()
def cfg(project: Path):
    from openivr.config import load_config

    config = load_config(project)
    config.ari.password = "secret"  # type: ignore[attr-defined]
    return config


@pytest.fixture()
def flow(cfg):
    from openivr.flow import Flow

    return Flow.load(cfg.flow_path)


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"
