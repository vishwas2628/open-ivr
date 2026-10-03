"""Starter flow used when no flow document exists yet."""

from __future__ import annotations

from typing import Any

DIGIT_SLOTS = "1234567890*#"


def default_ivr_document() -> dict[str, Any]:
    """Builder-facing IVR JSON (plan_for_builder.md)."""
    return {
        "Version": "0.1.0",
        "welcome_prompt": {"path": "", "prompt": ""},
        "promotion_prompt": {"path": "", "prompt": ""},
        "invalid": {
            "prompt": "",
            "repeat_prompt": True,
            "max_retries": 3,
            "fail_action": "hangup",
        },
        "timeout": {
            "seconds": 15,
            "prompt": "",
            "repeat_prompt": True,
            "max_retries": 3,
            "fail_action": "hangup",
        },
        "repeat": {"max_attempts": 3, "fallback": "hangup"},
        "hold_music": {"prompt": ""},
        "start_menu": "main",
        "menus": {
            "main": {
                "menu_prompt": "",
                "dtmf_options": {},
            }
        },
    }


def default_flow() -> dict[str, Any]:
    """Runtime-shaped starter used by tests and Flow.from_dict fallbacks."""
    return {
        "version": 1,
        "start_menu": "main",
        "welcome": "welcome",
        "goodbye": "goodbye",
        "time_route": None,
        "menus": {
            "main": {
                "prompt": "main-menu",
                "timeout": {
                    "seconds": 8,
                    "prompt": "timeout-msg",
                    "max_retries": 1,
                    "fail_action": {"action": "hangup"},
                },
                "invalid": {"prompt": "invalid-msg", "max_retries": 1},
                "options": {
                    "1": {"action": "dial", "label": "Sales", "endpoint": "1001"},
                    "2": {"action": "submenu", "label": "Support", "target": "support"},
                    "3": {
                        "action": "voicemail",
                        "label": "Leave a message",
                        "mailbox": "support",
                        "greeting": "vm-support",
                        "after": {"action": "hangup"},
                    },
                    "0": {"action": "dial", "label": "Operator", "endpoint": "1000"},
                },
            },
            "support": {
                "prompt": "support-menu",
                "timeout": {
                    "seconds": 8,
                    "prompt": "timeout-msg",
                    "max_retries": 1,
                    "fail_action": {"action": "submenu", "target": "main"},
                },
                "invalid": {"prompt": "invalid-msg", "max_retries": 1},
                "options": {
                    "1": {"action": "dial", "label": "Support desk", "endpoint": "1002"},
                    "2": {
                        "action": "collect",
                        "label": "Enter ticket number",
                        "prompt": "enter-ticket",
                        "min_digits": 4,
                        "max_digits": 8,
                        "variable": "ticket",
                        "next": {"action": "dial", "endpoint": "1002"},
                    },
                    "3": {
                        "action": "voicemail",
                        "label": "Voicemail",
                        "mailbox": "support",
                        "greeting": "vm-support",
                        "after": {"action": "hangup"},
                    },
                    "0": {"action": "submenu", "label": "Back to main menu", "target": "main"},
                },
            },
        },
    }
