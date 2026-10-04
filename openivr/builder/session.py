"""Persisted builder draft: trunk, endpoints, extensions, IVR, progress."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .defaults import default_ivr_document

log = logging.getLogger("openivr.builder")

MAX_ENDPOINTS = 50
# SIP/PJSIP user parts are very often just digits (1001, 2001, ...), so a
# leading digit is allowed - it must match the HTML pattern on the endpoints page.
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
EXT_RE = re.compile(r"^[1-9][0-9]{0,5}$")
STEPS = ("trunk", "endpoints", "extensions", "dialplan", "permissions", "smtp", "finish")
STEP_PATHS = {
    "trunk": "/trunk",
    "endpoints": "/endpoints",
    "extensions": "/extensions",
    "dialplan": "/dialplan",
    "permissions": "/permissions",
    "smtp": "/smtp",
    "finish": "/finish",
}
STEP_LABELS = {
    "trunk": "Trunk",
    "endpoints": "Endpoints",
    "extensions": "Extensions",
    "dialplan": "Dialplan",
    "permissions": "Permissions",
    "smtp": "SMTP",
    "finish": "Finish",
}

DTMF_KEYS = list("1234567890*#")
VOICEMAIL_FORMATS = {"wav", "ulaw", "alaw", "gsm", "sln", "sln16"}
DIALPLAN_ACTIONS = (
    "dial",
    "voicemail",
    "submenu",
    "repeat",
    "goback",
    "parent",
    "hangup",
)


def data_dir(root: Path) -> Path:
    path = root / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Ignoring unreadable %s: %s", path, exc)
        return fallback


def _write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    tmp.replace(path)
    return path


def load_progress(root: Path) -> dict[str, Any]:
    data = _read_json(data_dir(root) / "builder_progress.json", {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("current_step", "trunk")
    data.setdefault("completed", [])
    data.setdefault("finished", False)
    data.setdefault("published", False)
    data.setdefault("publish_needed", False)
    if data["current_step"] not in STEPS:
        data["current_step"] = "trunk"
    return data


def save_progress(root: Path, data: dict[str, Any]) -> Path:
    return _write_json(data_dir(root) / "builder_progress.json", data)


def mark_step(root: Path, step: str, *, completed: bool = True) -> dict[str, Any]:
    progress = load_progress(root)
    progress["current_step"] = step
    done = list(progress.get("completed") or [])
    if completed and step not in done:
        done.append(step)
    progress["completed"] = done
    if step != "finish":
        progress["publish_needed"] = True
    save_progress(root, progress)
    return progress


def load_trunk(root: Path) -> dict[str, Any]:
    data = _read_json(data_dir(root) / "trunk.json", {})
    return data if isinstance(data, dict) else {}


def save_trunk(root: Path, data: dict[str, Any]) -> Path:
    mark_step(root, "trunk")
    return _write_json(data_dir(root) / "trunk.json", data)


def load_endpoints(root: Path) -> list[dict[str, Any]]:
    data = _read_json(data_dir(root) / "endpoints.json", {"endpoints": []})
    if isinstance(data, list):
        return data
    items = data.get("endpoints") if isinstance(data, dict) else None
    return list(items) if isinstance(items, list) else []


def save_endpoints(root: Path, endpoints: list[dict[str, Any]]) -> Path:
    mark_step(root, "endpoints")
    return _write_json(data_dir(root) / "endpoints.json", {"endpoints": endpoints})


def load_extensions(root: Path) -> list[dict[str, Any]]:
    data = _read_json(data_dir(root) / "extensions.json", {"extensions": []})
    if isinstance(data, list):
        return data
    items = data.get("extensions") if isinstance(data, dict) else None
    return list(items) if isinstance(items, list) else []


def save_extensions(root: Path, extensions: list[dict[str, Any]]) -> Path:
    mark_step(root, "extensions")
    return _write_json(data_dir(root) / "extensions.json", {"extensions": extensions})


def load_ivr(root: Path) -> dict[str, Any]:
    data = _read_json(data_dir(root) / "ivr_flow.json", None)
    if isinstance(data, dict) and (data.get("menus") or data.get("welcome_prompt")):
        return data
    return default_ivr_document()


def save_ivr(root: Path, data: dict[str, Any]) -> Path:
    mark_step(root, "dialplan")
    return _write_json(data_dir(root) / "ivr_flow.json", data)


def save_smtp(root: Path, smtp: dict[str, Any]) -> Path:
    mark_step(root, "smtp")
    return _write_json(data_dir(root) / "smtp.json", {"smtp": smtp})


def load_smtp(root: Path) -> dict[str, Any]:
    data = _read_json(data_dir(root) / "smtp.json", {})
    if not isinstance(data, dict):
        return {}
    smtp = data.get("smtp", data)
    return smtp if isinstance(smtp, dict) else {}


DEFAULT_PERMISSIONS: dict[str, Any] = {
    "voicemail_enabled": True,
    "voicemail_dir": "data/recordings/voicemail",
    "voicemail_format": "wav",
    "voicemail_max_duration": 120,
    "recording_enabled": False,
    "recording_dir": "/var/spool/asterisk/monitor",
    "recording_retention_days": 30,
}


def load_permissions(root: Path) -> dict[str, Any]:
    """Voicemail + recording settings, seeded from config.yaml defaults."""
    data = _read_json(data_dir(root) / "permissions.json", None)
    values = dict(DEFAULT_PERMISSIONS)
    if isinstance(data, dict) and isinstance(data.get("permissions"), dict):
        values.update({k: v for k, v in data["permissions"].items() if k in values})
    return values


def save_permissions(root: Path, data: dict[str, Any]) -> Path:
    cleaned = {k: data[k] for k in DEFAULT_PERMISSIONS if k in data}
    mark_step(root, "permissions")
    return _write_json(data_dir(root) / "permissions.json", {"permissions": cleaned})


def validate_permissions(data: dict[str, Any]) -> list[str]:
    """Check the permissions step; a disabled feature is never validated."""
    errors: list[str] = []
    if data.get("voicemail_enabled"):
        duration = data.get("voicemail_max_duration")
        try:
            duration = int(duration)
        except (TypeError, ValueError):
            errors.append("Voicemail max duration must be a number of seconds")
        else:
            if duration < 10 or duration > 600:
                errors.append("Voicemail max duration must be between 10 and 600 seconds")
        fmt = str(data.get("voicemail_format") or "").lower()
        if fmt not in VOICEMAIL_FORMATS:
            errors.append(f"Voicemail format must be one of {', '.join(sorted(VOICEMAIL_FORMATS))}")
        if not str(data.get("voicemail_dir") or "").strip():
            errors.append("Voicemail directory is required while voicemail is enabled")
    if data.get("recording_enabled") and not str(data.get("recording_dir") or "").strip():
        errors.append("Recording directory is required while call recording is enabled")
    if data.get("recording_enabled"):
        try:
            days = int(data.get("recording_retention_days"))
        except (TypeError, ValueError):
            errors.append("Recording retention must be a number of days")
        else:
            if days < 1 or days > 3650:
                errors.append("Recording retention must be between 1 and 3650 days")
    return errors


def snake_case_stem(name: str) -> str:
    stem = Path(name).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "_", stem).strip("_")
    return slug or "prompt"


def validate_trunk(data: dict[str, Any], provider: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    if not data.get("country"):
        errors.append("Choose a country")
    if not data.get("provider_id"):
        errors.append("Choose a SIP provider")
        return errors
    if provider is None:
        errors.append(f"Unknown provider {data.get('provider_id')!r}")
        return errors
    values = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    for field in provider.get("fields") or []:
        if field.get("required") and not str(values.get(field["id"], "")).strip():
            errors.append(f"{field['label']} is required")
    host = str(data.get("host_fqdn") or values.get("host_fqdn") or provider.get("host_fqdn") or "").strip()
    if not host:
        errors.append("Host / registrar FQDN is required")
    port = str(data.get("signaling_port") or provider.get("signaling_port") or "5060").strip()
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        errors.append("Signaling port must be a number between 1 and 65535")
    elif provider.get("signaling_port") and port != str(provider.get("signaling_port")):
        errors.append(f"Signaling port must be {provider.get('signaling_port')} for this provider")
    auth = data.get("auth_type") or provider.get("auth_type")
    if auth == "registration" or provider.get("registration_uri_required"):
        uri = str(values.get("registration_uri") or data.get("registration_uri") or "").strip()
        if not uri:
            errors.append("Registration URI is required for this provider")
    if auth in ("registration", "credentials"):
        if not str(values.get("username") or data.get("username") or "").strip():
            errors.append("SIP username is required for this authentication method")
        if not str(values.get("password") or data.get("password") or "").strip():
            errors.append("SIP password is required for this authentication method")
    return errors


def validate_endpoints(endpoints: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if not endpoints:
        errors.append("Add at least one endpoint (softphone / IAX user)")
        return errors
    if len(endpoints) > MAX_ENDPOINTS:
        errors.append(f"At most {MAX_ENDPOINTS} endpoints")
    seen: set[str] = set()
    for item in endpoints:
        user = str(item.get("username") or "").strip()
        if not USERNAME_RE.match(user):
            errors.append(
                f"Invalid username {user!r} (start with a letter or digit, then . _ - is allowed)"
            )
            continue
        if user in seen:
            errors.append(f"Duplicate username {user!r}")
        seen.add(user)
        if not str(item.get("password") or "").strip():
            errors.append(f"Password required for {user}")
    return errors


def validate_extensions(extensions: list[dict[str, Any]], endpoints: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    users = {str(e.get("username") or "") for e in endpoints}
    if not extensions:
        errors.append("Add at least one extension")
        return errors
    seen: set[str] = set()
    for item in extensions:
        number = str(item.get("number") or "").strip()
        if not EXT_RE.match(number):
            errors.append(f"Extension {number!r} must be 1–999999 (no leading zero)")
            continue
        if number in seen:
            errors.append(f"Duplicate extension {number}")
        seen.add(number)
        strategy = str(item.get("strategy") or "single")
        if strategy not in {"single", "linear", "ringall"}:
            errors.append(f"Extension {number}: strategy must be single, linear, or ringall")
        assigned = [str(u) for u in (item.get("users") or []) if str(u).strip()]
        if not assigned:
            errors.append(f"Extension {number} needs at least one user")
        if strategy == "single" and len(assigned) != 1:
            errors.append(f"Extension {number} (single) must have exactly one user")
        for user in assigned:
            if user not in users:
                errors.append(f"Extension {number} references unknown user {user!r}")
    return errors


def validate_ivr_document(data: dict[str, Any], extension_numbers: list[str] | None = None) -> list[str]:
    errors: list[str] = []
    welcome = data.get("welcome_prompt") or {}
    if isinstance(welcome, dict) and not str(welcome.get("prompt") or "").strip():
        errors.append("Welcome prompt is required")
    elif not welcome:
        errors.append("Welcome prompt is required")
    for key, label in (("invalid", "Invalid prompt"), ("timeout", "Timeout prompt"), ("repeat", "Repeat settings")):
        if not data.get(key):
            errors.append(f"{label} are required")
    timeout = data.get("timeout") or {}
    if isinstance(timeout, dict):
        try:
            seconds = float(timeout.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            errors.append("Timeout seconds must be greater than 0")
    menus = data.get("menus")
    if not isinstance(menus, dict) or not menus:
        errors.append("Add at least one menu")
        return errors
    start = str(data.get("start_menu") or "")
    if start not in menus:
        errors.append("Start menu must be one of the configured menus")
    known_ext = set(extension_numbers or [])
    for name, body in menus.items():
        if not isinstance(body, dict):
            errors.append(f"Menu {name!r} is invalid")
            continue
        if not str(body.get("menu_prompt") or "").strip():
            errors.append(f"Menu {name!r} needs a menu prompt")
        options = body.get("dtmf_options") or {}
        if not isinstance(options, dict) or not options:
            errors.append(f"Menu {name!r} needs at least one DTMF option")
            continue
        if len(options) > 12:
            errors.append(f"Menu {name!r} has more than 12 DTMF options")
        for digit, opt in options.items():
            if str(digit) not in DTMF_KEYS:
                errors.append(f"Menu {name!r}: {digit!r} is not a valid key (0-9 * #)")
            if not isinstance(opt, dict):
                continue
            action = str(opt.get("action") or "")
            if action not in DIALPLAN_ACTIONS:
                errors.append(f"Menu {name!r} key {digit}: unknown action {action!r}")
            if action == "dial":
                endpoint = str(opt.get("endpoint") or "").strip()
                if not endpoint:
                    errors.append(f"Menu {name!r} key {digit}: dial needs an extension")
                elif known_ext and endpoint not in known_ext:
                    errors.append(f"Menu {name!r} key {digit}: unknown extension {endpoint}")
            if action == "submenu" and str(opt.get("target") or "") not in menus:
                errors.append(f"Menu {name!r} key {digit}: submenu target is missing")
            if action == "voicemail" and not str(opt.get("mailbox") or "").strip():
                errors.append(f"Menu {name!r} key {digit}: voicemail needs a mailbox")
            if action == "parent" and opt.get("target") and str(opt.get("target")) not in menus:
                errors.append(f"Menu {name!r} key {digit}: parent target is unknown")
    return errors


def first_incomplete_step(root: Path) -> str:
    trunk = load_trunk(root)
    endpoints = load_endpoints(root)
    extensions = load_extensions(root)
    if validate_trunk(trunk, None if not trunk else {"id": trunk.get("provider_id"), "fields": [], "auth_type": trunk.get("auth_type"), "host_fqdn": trunk.get("host_fqdn"), "registration_uri_required": bool(trunk.get("registration_uri"))}):
        # cheap: missing country/provider is enough to stay on trunk
        if not trunk.get("provider_id") or not trunk.get("country"):
            return "trunk"
    if not endpoints:
        return "endpoints" if trunk.get("provider_id") else "trunk"
    if not extensions:
        return "extensions"
    ivr = load_ivr(root)
    if validate_ivr_document(ivr, [str(e.get("number")) for e in extensions]):
        return "dialplan"
    progress = load_progress(root)
    if not progress.get("finished"):
        if "dialplan" not in (progress.get("completed") or []):
            return "dialplan"
        if "permissions" not in (progress.get("completed") or []):
            return "permissions"
        return "smtp" if "smtp" not in (progress.get("completed") or []) else "finish"
    return "finish"
