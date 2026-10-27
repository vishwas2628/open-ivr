"""IVR flow schema, parsing and validation.

This module is the single source of truth for the flow document that the
builder writes and the runtime loads::

    {
      "version": 1,
      "start_menu": "main",
      "welcome": "welcome",
      "goodbye": "goodbye",
      "menus": {
        "main": {
          "prompt": "main-menu",
          "timeout": {"seconds": 8, "prompt": "timeout-msg", "max_retries": 1,
                      "fail_action": {"action": "hangup"}},
          "invalid": {"prompt": "invalid-msg", "max_retries": 1},
          "options": {
            "1": {"action": "dial", "endpoint": "PJSIP/1001", "label": "Sales"},
            "2": {"action": "submenu", "target": "support", "label": "Support"},
            "3": {"action": "voicemail", "mailbox": "support",
                  "greeting": "vm-support", "after": {"action": "hangup"}},
            "4": {"action": "collect", "min_digits": 3, "max_digits": 6,
                  "prompt": "enter-account", "variable": "account",
                  "next": {"action": "submenu", "target": "main"}},
            "9": {"action": "repeat"}
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .media import KNOWN_URIS

FLOW_VERSION = 1

DIGITS = "0123456789*#"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

ACTIONS = {
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
}
TERMINAL_ACTIONS = {"hangup", "dial"}

DEFAULT_TIMEOUT = {"seconds": 8, "prompt": "timeout-msg", "max_retries": 1, "fail_action": None}
DEFAULT_INVALID = {"prompt": "invalid-msg", "max_retries": 1}


class FlowError(ValueError):
    """Raised when a flow document is unusable."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors) or "invalid flow")


@dataclass(slots=True)
class TimeoutCfg:
    seconds: float = 8.0
    prompt: str | None = None
    max_retries: int = 1
    fail_action: Action | None = None


@dataclass(slots=True)
class InvalidCfg:
    prompt: str | None = None
    max_retries: int = 1


@dataclass(slots=True)
class Action:
    """A menu option / transition target."""

    type: str
    label: str = ""
    endpoint: str = ""
    target: str = ""
    mailbox: str = ""
    greeting: str = ""
    after: Action | None = None
    next: Action | None = None
    variable: str = ""
    min_digits: int = 1
    max_digits: int = 4
    prompt: str | None = None
    business_menu: str = ""
    after_hours_menu: str = ""
    hours: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        if self.type == "dial":
            return f"dial {self.endpoint}"
        if self.type in {"submenu", "goto"}:
            return f"{self.type} {self.target}"
        if self.type == "voicemail":
            return f"voicemail {self.mailbox or 'default'}"
        if self.type == "collect":
            return f"collect {self.min_digits}-{self.max_digits} digits"
        return self.type


@dataclass(slots=True)
class Option:
    digit: str
    action: Action


@dataclass(slots=True)
class Menu:
    name: str
    prompt: str | None = None
    options: dict[str, Option] = field(default_factory=dict)
    timeout: TimeoutCfg = field(default_factory=TimeoutCfg)
    invalid: InvalidCfg = field(default_factory=InvalidCfg)
    on_exit: Action | None = None


@dataclass(slots=True)
class TimeRoute:
    """Optional business-hours routing on a menu."""

    prompt: str = "business-hours"
    timezone: str = "UTC"
    business_menu: str = ""
    after_hours_menu: str = ""
    hours: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Flow:
    version: int = FLOW_VERSION
    start_menu: str = "main"
    welcome: str | None = None
    goodbye: str | None = None
    menus: dict[str, Menu] = field(default_factory=dict)
    time_route: TimeRoute | None = None
    digits: dict[str, str] = field(default_factory=dict)

    def menu(self, name: str) -> Menu:
        try:
            return self.menus[name]
        except KeyError as exc:
            raise FlowError([f"menu {name!r} does not exist"]) from exc

    def prompts(self) -> set[str]:
        """Every prompt/media name the flow may play.

        Full media URIs (``sound:``, ``recording:`` …) are returned as-is by
        ``media.resolve_media`` and are not files in the sounds folder, so they
        are excluded here - otherwise every such prompt would look "missing".
        """
        found: set[str] = set()
        for value in (self.welcome, self.goodbye):
            if value:
                found.add(value)
        for menu in self.menus.values():
            if menu.prompt:
                found.add(menu.prompt)
            if menu.timeout.prompt:
                found.add(menu.timeout.prompt)
            if menu.invalid.prompt:
                found.add(menu.invalid.prompt)
            if menu.on_exit:
                found |= _action_prompts(menu.on_exit)
            for opt in menu.options.values():
                found |= _action_prompts(opt.action)
        if self.time_route:
            if self.time_route.prompt:
                found.add(self.time_route.prompt)
        return {name for name in found if not name.startswith(KNOWN_URIS)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "start_menu": self.start_menu,
            "welcome": self.welcome,
            "goodbye": self.goodbye,
            "time_route": _time_route_to_dict(self.time_route),
            "digits": self.digits,
            "menus": {name: _menu_to_dict(menu) for name, menu in self.menus.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Flow:
        if not isinstance(data, dict):
            raise FlowError([f"flow must be an object, got {type(data).__name__}"])
        menus_raw = data.get("menus") or {}
        if not isinstance(menus_raw, dict) or not menus_raw:
            raise FlowError(["flow.menus must be a non-empty object"])
        digits = {str(k): str(v) for k, v in (data.get("digits") or {}).items()}
        welcome = data.get("welcome")
        if not welcome and isinstance(data.get("welcome_prompt"), dict):
            welcome = data.get("welcome_prompt", {}).get("prompt") or None
        return cls(
            version=_flow_version(data),
            start_menu=str(data.get("start_menu", "main")),
            welcome=welcome or None,
            goodbye=data.get("goodbye") or None,
            menus={name: _menu_from_dict(name, body, data) for name, body in menus_raw.items()},
            time_route=_time_route_from_dict(data.get("time_route")),
            digits=digits,
        )

    @classmethod
    def load(cls, path: str | Path) -> Flow:
        p = Path(path)
        try:
            with p.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError as exc:
            raise FlowError([f"flow file {p} not found"]) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise FlowError([f"cannot read {p}: {exc}"]) from exc
        return cls.from_dict(data)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=False)
            fh.write("\n")
        tmp.replace(p)
        return p


def _action_prompts(action: Action | None) -> set[str]:
    out: set[str] = set()
    if action is None:
        return out
    if action.prompt:
        out.add(action.prompt)
    if action.greeting:
        out.add(action.greeting)
    for nested in (action.after, action.next):
        out |= _action_prompts(nested)
    return out


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _flow_version(data: dict[str, Any]) -> int:
    raw = data.get("version", data.get("Version", FLOW_VERSION))
    if isinstance(raw, int):
        return raw
    try:
        return int(str(raw))
    except (TypeError, ValueError):
        try:
            parts = [int(part) for part in str(raw).split(".")]
        except (TypeError, ValueError):
            parts = []
        if parts and parts[0] == 0 and len(parts) >= 2 and parts[1] == 1:
            return FLOW_VERSION
        return FLOW_VERSION


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _action_from_dict(data: Any, where: str) -> Action:
    if data is None:
        raise FlowError([f"{where}: missing action object"])
    if isinstance(data, str):
        data = {"action": data}
    if not isinstance(data, dict):
        raise FlowError([f"{where}: action must be an object"])
    kind = str(data.get("action", "")).strip()
    if kind not in ACTIONS:
        raise FlowError([f"{where}: unknown action {kind!r} (known: {', '.join(sorted(ACTIONS))})"])
    return Action(
        type=kind,
        label=str(data.get("label", "") or data.get("description", "") or ""),
        endpoint=str(data.get("endpoint", "") or ""),
        target=str(data.get("target", "") or ""),
        mailbox=str(data.get("mailbox", "") or ""),
        greeting=str(data.get("greeting", "") or ""),
        after=_action_from_dict(data["after"], f"{where}.after") if data.get("after") else None,
        next=_action_from_dict(data["next"], f"{where}.next") if data.get("next") else None,
        variable=str(data.get("variable", "") or ""),
        min_digits=_as_int(data.get("min_digits"), 1),
        max_digits=_as_int(data.get("max_digits"), 4),
        prompt=data.get("prompt") or None,
        business_menu=str(data.get("business_menu", "") or ""),
        after_hours_menu=str(data.get("after_hours_menu", "") or ""),
        hours=dict(data.get("hours") or {}),
    )


def _menu_from_dict(name: str, body: Any, root: dict[str, Any] | None = None) -> Menu:
    where = f"menus.{name}"
    if not isinstance(body, dict):
        raise FlowError([f"{where}: menu must be an object"])
    timeout_raw = body.get("timeout")
    if timeout_raw is None and isinstance(root, dict):
        timeout_raw = root.get("timeout")
    if timeout_raw is None:
        timeout_raw = {}
    if not isinstance(timeout_raw, dict):
        raise FlowError([f"{where}.timeout: must be an object"])
    invalid_raw = body.get("invalid")
    if invalid_raw is None and isinstance(root, dict):
        invalid_raw = root.get("invalid")
    if invalid_raw is None:
        invalid_raw = {}
    if not isinstance(invalid_raw, dict):
        raise FlowError([f"{where}.invalid: must be an object"])
    fail_action = timeout_raw.get("fail_action")
    options_raw = body.get("options") if body.get("options") is not None else body.get("dtmf_options")
    if options_raw is None:
        options_raw = {}
    if not isinstance(options_raw, dict):
        raise FlowError([f"{where}.options: must be an object keyed by digit"])
    prompt = body.get("prompt") or body.get("menu_prompt") or None
    return Menu(
        name=name,
        prompt=prompt,
        options={
            str(digit): Option(
                digit=str(digit), action=_action_from_dict(opt, f"{where}.options.{digit}")
            )
            for digit, opt in options_raw.items()
        },
        timeout=TimeoutCfg(
            seconds=_as_float(timeout_raw.get("seconds"), 8.0),
            prompt=timeout_raw.get("prompt") or None,
            max_retries=_as_int(timeout_raw.get("max_retries"), 1),
            fail_action=(
                _action_from_dict(fail_action, f"{where}.timeout.fail_action")
                if fail_action
                else None
            ),
        ),
        invalid=InvalidCfg(
            prompt=invalid_raw.get("prompt") or None,
            max_retries=_as_int(invalid_raw.get("max_retries"), 1),
        ),
        on_exit=(
            _action_from_dict(body["on_exit"], f"{where}.on_exit") if body.get("on_exit") else None
        ),
    )


def _time_route_from_dict(data: Any) -> TimeRoute | None:
    if not data:
        return None
    if not isinstance(data, dict):
        raise FlowError(["time_route: must be an object"])
    return TimeRoute(
        prompt=str(data.get("prompt", "business-hours") or "business-hours"),
        timezone=str(data.get("timezone", "UTC") or "UTC"),
        business_menu=str(data.get("business_menu", "") or ""),
        after_hours_menu=str(data.get("after_hours_menu", "") or ""),
        hours=dict(data.get("hours") or {}),
    )


def _action_to_dict(action: Action | None) -> dict[str, Any] | None:
    if action is None:
        return None
    out: dict[str, Any] = {"action": action.type}
    for key, value in (
        ("label", action.label),
        ("endpoint", action.endpoint),
        ("target", action.target),
        ("mailbox", action.mailbox),
        ("greeting", action.greeting),
        ("variable", action.variable),
        ("business_menu", action.business_menu),
        ("after_hours_menu", action.after_hours_menu),
    ):
        if value:
            out[key] = value
    if action.prompt:
        out["prompt"] = action.prompt
    if action.type == "collect":
        out.setdefault("min_digits", action.min_digits)
        out.setdefault("max_digits", action.max_digits)
    if action.hours:
        out["hours"] = action.hours
    if action.after is not None:
        out["after"] = _action_to_dict(action.after)
    if action.next is not None:
        out["next"] = _action_to_dict(action.next)
    return out


def _menu_to_dict(menu: Menu) -> dict[str, Any]:
    return {
        "prompt": menu.prompt,
        "timeout": {
            "seconds": menu.timeout.seconds,
            "prompt": menu.timeout.prompt,
            "max_retries": menu.timeout.max_retries,
            "fail_action": _action_to_dict(menu.timeout.fail_action),
        },
        "invalid": {"prompt": menu.invalid.prompt, "max_retries": menu.invalid.max_retries},
        "options": {d: _action_to_dict(o.action) for d, o in menu.options.items()},
        "on_exit": _action_to_dict(menu.on_exit),
    }


def _time_route_to_dict(route: TimeRoute | None) -> dict[str, Any] | None:
    if route is None:
        return None
    out: dict[str, Any] = {"prompt": route.prompt, "timezone": route.timezone}
    if route.business_menu:
        out["business_menu"] = route.business_menu
    if route.after_hours_menu:
        out["after_hours_menu"] = route.after_hours_menu
    if route.hours:
        out["hours"] = route.hours
    return out


def validate(
    data: dict[str, Any] | Flow,
    *,
    max_depth: int = 3,
    max_options: int = 5,
    available_sounds: set[str] | None = None,
) -> list[str]:
    """Return a list of human readable problems (empty means the flow is OK)."""
    try:
        flow = data if isinstance(data, Flow) else Flow.from_dict(data)
    except FlowError as exc:
        return exc.errors

    errors: list[str] = []
    if flow.version != FLOW_VERSION:
        errors.append(f"unsupported flow version {flow.version} (expected {FLOW_VERSION})")
    if flow.start_menu not in flow.menus:
        errors.append(f"start_menu {flow.start_menu!r} is not defined in menus")

    for name, menu in flow.menus.items():
        if not NAME_RE.match(name):
            errors.append(f"menu name {name!r} must match {NAME_RE.pattern}")
        if len(menu.options) > max_options:
            errors.append(f"menu {name!r} has {len(menu.options)} options, limit is {max_options}")
        if not menu.options:
            errors.append(f"menu {name!r} has no options")
        for digit, opt in menu.options.items():
            if len(digit) != 1 or digit not in DIGITS:
                errors.append(f"menu {name!r} key {digit!r} must be one of {DIGITS}")
            if opt.action.type in {"submenu", "goto"} and opt.action.target not in flow.menus:
                errors.append(
                    f"menu {name!r} key {digit!r} targets unknown menu {opt.action.target!r}"
                )
            if opt.action.type == "dial" and not opt.action.endpoint:
                errors.append(f"menu {name!r} key {digit!r} is a dial action without endpoint")
            if opt.action.type == "voicemail" and not opt.action.greeting:
                errors.append(f"menu {name!r} key {digit!r} is a voicemail action without greeting")
            if opt.action.type == "collect":
                if opt.action.min_digits < 1:
                    errors.append(f"menu {name!r} key {digit!r} collect min_digits must be >= 1")
                if opt.action.max_digits < opt.action.min_digits:
                    errors.append(f"menu {name!r} key {digit!r} collect max_digits < min_digits")
            if opt.action.type == "time_route":
                if not (opt.action.business_menu and opt.action.after_hours_menu):
                    errors.append(
                        f"menu {name!r} key {digit!r} time_route needs business_menu and "
                        "after_hours_menu"
                    )
                for target in (opt.action.business_menu, opt.action.after_hours_menu):
                    if target not in flow.menus:
                        errors.append(
                            f"menu {name!r} key {digit!r} time_route -> unknown {target!r}"
                        )
        if menu.timeout.seconds <= 0:
            errors.append(f"menu {name!r} timeout.seconds must be > 0")
        if menu.on_exit is not None and menu.on_exit.type in {"submenu", "goto"}:
            if menu.on_exit.target not in flow.menus:
                errors.append(f"menu {name!r} on_exit targets unknown menu {menu.on_exit.target!r}")
        if menu.timeout.fail_action is not None:
            fail = menu.timeout.fail_action
            if fail.type in {"submenu", "goto"} and fail.target not in flow.menus:
                errors.append(f"menu {name!r} timeout.fail_action -> unknown menu {fail.target!r}")

    if flow.time_route is not None:
        for target in (flow.time_route.business_menu, flow.time_route.after_hours_menu):
            if target and target not in flow.menus:
                errors.append(f"time_route -> unknown menu {target!r}")

    errors.extend(_check_depth(flow, max_depth))
    errors.extend(_check_unreachable(flow))
    if available_sounds is not None:
        for prompt in sorted(flow.prompts() - available_sounds):
            errors.append(f"prompt {prompt!r} has no audio file in the sounds folder")
    return errors


def _check_depth(flow: Flow, max_depth: int) -> list[str]:
    errors: list[str] = []
    for name in flow.menus:
        depth = _menu_depth(flow, name, set())
        if depth > max_depth:
            errors.append(f"menu {name!r} is nested {depth} deep, limit is {max_depth}")
    return errors


def _menu_depth(flow: Flow, name: str, seen: set[str]) -> int:
    if name in seen:
        return 0
    seen = seen | {name}
    menu = flow.menus[name]
    children: list[str] = []
    for opt in menu.options.values():
        act = opt.action
        if act.type in {"submenu", "goto"}:
            children.append(act.target)
        if act.type == "time_route":
            children.extend([act.business_menu, act.after_hours_menu])
        if act.next is not None and act.next.type in {"submenu", "goto"}:
            children.append(act.next.target)
    if not children:
        return 1
    known = [c for c in children if c in flow.menus]
    return 1 + max((_menu_depth(flow, c, seen) for c in known), default=0)


def _check_unreachable(flow: Flow) -> list[str]:
    reachable: set[str] = set()
    frontier = [flow.start_menu]
    if flow.time_route is not None:
        frontier.extend(
            m for m in (flow.time_route.business_menu, flow.time_route.after_hours_menu) if m
        )
    while frontier:
        name = frontier.pop()
        if name in reachable or name not in flow.menus:
            continue
        reachable.add(name)
        menu = flow.menus[name]
        acts = [o.action for o in menu.options.values()]
        for act in [*acts, menu.on_exit, menu.timeout.fail_action]:
            if act is None:
                continue
            targets = []
            if act.type in {"submenu", "goto"}:
                targets.append(act.target)
            if act.type == "time_route":
                targets.extend([act.business_menu, act.after_hours_menu])
            for nested in (act.next, act.after):
                if nested is not None and nested.type in {"submenu", "goto"}:
                    targets.append(nested.target)
            frontier.extend(t for t in targets if t)
    return [
        f"menu {name!r} is not reachable from start_menu {flow.start_menu!r}"
        for name in sorted(set(flow.menus) - reachable)
    ]


def menu_tree(flow: Flow) -> dict[str, Any]:
    """Nested dict describing the flow, used by the builder's tree view."""
    return {"name": flow.start_menu, "children": _tree_children(flow, flow.start_menu, 0)}


def _tree_children(flow: Flow, name: str, depth: int) -> list[dict[str, Any]]:
    if depth > 4 or name not in flow.menus:
        return []
    menu = flow.menus[name]
    children: list[dict[str, Any]] = []
    for digit, opt in sorted(menu.options.items()):
        act = opt.action
        node = {
            "digit": digit,
            "action": act.type,
            "label": act.label or act.describe(),
            "children": [],
        }
        if act.type in {"submenu", "goto"}:
            node["children"] = _tree_children(flow, act.target, depth + 1)
        children.append(node)
    return children
