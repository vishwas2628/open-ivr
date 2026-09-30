"""Starter flow used when no flow document exists yet."""

from __future__ import annotations

from typing import Any


def default_flow() -> dict[str, Any]:
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
                    "1": {
                        "action": "dial",
                        "label": "Sales",
                        "endpoint": "PJSIP/1001",
                    },
                    "2": {
                        "action": "submenu",
                        "label": "Support",
                        "target": "support",
                    },
                    "3": {
                        "action": "voicemail",
                        "label": "Leave a message",
                        "mailbox": "support",
                        "greeting": "vm-support",
                        "after": {"action": "hangup"},
                    },
                    "0": {
                        "action": "dial",
                        "label": "Operator",
                        "endpoint": "PJSIP/1000",
                    },
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
                    "1": {"action": "dial", "label": "Support desk", "endpoint": "PJSIP/1002"},
                    "2": {
                        "action": "collect",
                        "label": "Enter ticket number",
                        "prompt": "enter-ticket",
                        "min_digits": 4,
                        "max_digits": 8,
                        "variable": "ticket",
                        "next": {"action": "dial", "endpoint": "PJSIP/1002"},
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


DIGIT_SLOTS = "1234567890*#"
