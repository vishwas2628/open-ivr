"""Render Asterisk conf fragments from builder JSON."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .session import load_endpoints, load_extensions, load_permissions, load_smtp, load_trunk

SKELETON_ROOT = Path(__file__).resolve().parents[2] / "system" / "asterisk"
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def trunk_name(trunk: dict[str, Any]) -> str:
    raw = str(trunk.get("name") or trunk.get("provider_id") or "trunk")
    slug = SAFE.sub("_", raw).strip("_").lower() or "trunk"
    return slug[:32]


COMMENT_PREFIXES = (";", "#")


def fill_tokens(text: str, mapping: dict[str, str]) -> str:
    """Replace {{token}} placeholders, leaving comments untouched.

    Skeletons document their tokens in comments; substituting there would turn
    the documentation into values.
    """
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith(COMMENT_PREFIXES):
            out.append(line)
            continue
        for key, value in mapping.items():
            line = line.replace("{{" + key + "}}", value)
        out.append(line)
    return "".join(out)


def _header(kind: str, name: str) -> str:
    return f"; managed by openivr builder — {kind} {name}\n;\n"


def render_endpoint(user: dict[str, Any], skeleton: Path | None = None) -> str:
    path = skeleton or (SKELETON_ROOT / "endpoints" / ".skel_endpoint.conf")
    text = path.read_text(encoding="utf-8")
    username = str(user["username"])
    password = str(user.get("password") or "")
    display = str(user.get("display_name") or username)
    text = text.replace("USERNAME", username).replace("SECRET_PASSWORD", password)
    text = text.replace(f'callerid = "{username}" <{username}>', f'callerid = "{display}" <{username}>')
    return _header("endpoint", username) + text


def render_iax_user(user: dict[str, Any]) -> str:
    username = str(user["username"])
    password = str(user.get("password") or "")
    display = str(user.get("display_name") or username)
    return (
        _header("iax", username)
        + f"[{username}]\n"
        + "type = friend\n"
        + "host = dynamic\n"
        + f"secret = {password}\n"
        + "context = extensions\n"
        + "disallow = all\n"
        + "allow = ulaw,alaw\n"
        + f'callerid = "{display}" <{username}>\n'
        + "requirecalltoken = no\n"
    )


def render_trunk(trunk: dict[str, Any], skeleton: Path | None = None) -> str:
    path = skeleton or (SKELETON_ROOT / "endpoints" / "trunks" / ".skel_trunk.conf")
    text = path.read_text(encoding="utf-8")
    name = trunk_name(trunk)
    fields = trunk.get("fields") if isinstance(trunk.get("fields"), dict) else {}
    host = str(trunk.get("host_fqdn") or fields.get("host_fqdn") or "").strip()
    host = re.sub(r"^sips?:(?://)?", "", host, flags=re.IGNORECASE).strip("/")
    username = str(fields.get("username") or trunk.get("username") or "").strip()
    password = str(fields.get("password") or trunk.get("password") or "").strip()
    client_uri = str(fields.get("registration_uri") or trunk.get("registration_uri") or "").strip()
    if client_uri and not client_uri.lower().startswith("sip:"):
        client_uri = f"sip:{client_uri}"
    transport = str(trunk.get("transport") or "transport-udp")
    signaling_port = str(trunk.get("signaling_port") or "5060").strip() or "5060"
    proto = "tcp" if transport.endswith("tcp") else "udp"
    has_port = ":" in host.rsplit("/", 1)[-1] and host.rsplit(":", 1)[-1].isdigit()
    host_with_port = host if has_port else f"{host}:{signaling_port}"
    if not client_uri and username and host:
        client_uri = f"sip:{username}@{host_with_port}"
    elif client_uri and re.match(r"sip:[^@]+@[^/;:]+(;|$)", client_uri, re.IGNORECASE):
        client_uri = re.sub(r"(sip:[^@]+@[^/;:]+)", rf"\1:{signaling_port}", client_uri, count=1, flags=re.IGNORECASE)
    if client_uri and ";transport=" not in client_uri:
        client_uri = f"{client_uri};transport={proto}"
    inbound = str(trunk.get("inbound_context") or "from-trunk")
    matches = [m for m in (trunk.get("match") or []) if str(m).strip()]
    mapping = {
        "trunk_username": username,
        "trunk_password": password,
        "host_fqdn": host,
        "host_fqdn_with_port": host_with_port,
        "trunk_transport": transport,
        "inbound_context": inbound,
        "client_uri": client_uri,
        "trunk_ip_1": matches[0] if len(matches) > 0 else "",
        "trunk_ip_2": matches[1] if len(matches) > 1 else "",
        "trunk_ip_3": matches[2] if len(matches) > 2 else "",
    }
    body = fill_tokens(text, mapping).replace("TRUNK", name)
    extra = matches[3:]
    direction = str(trunk.get("direction") or "").strip().lower()
    if extra and direction != "outbound":
        extra_lines = "\n".join(f"match = {cidr}" for cidr in extra)
        body = body.rstrip() + "\n" + extra_lines + "\n"
    # Drop empty match lines so Asterisk does not warn.
    cleaned: list[str] = []
    for line in body.splitlines(True):
        if line.strip().startswith("match =") and not line.split("=", 1)[1].strip():
            continue
        cleaned.append(line)
    body = "".join(cleaned)
    auth_type = str(trunk.get("auth_type") or "registration")
    if auth_type == "ip":
        body = _drop_section(body, f"{name}-auth")
        body = "".join(
            line
            for line in body.splitlines(True)
            if line.strip() not in {f"outbound_auth = {name}-auth", f"auth = {name}-auth"}
        )
    if auth_type != "registration" or not str(client_uri).strip():
        body = _drop_section(body, f"{name}-reg")

    if direction == "inbound":
        body = _drop_section(body, f"{name}-aor")
        body = _drop_section(body, f"{name}-out")
        body = _drop_section(body, f"{name}-reg")
    elif direction == "outbound":
        body = _drop_section(body, f"{name}-in")
        body = _drop_section(body, f"{name}-identify")
    return _header("trunk", name) + body


def _drop_section(text: str, section: str) -> str:
    lines = text.splitlines(True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("[") and line.strip() == f"[{section}]":
            skipping = True
            continue
        if skipping and line.startswith("[") and not line.startswith(f"[{section}]"):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def _tech(item: dict[str, Any], user: str) -> str:
    return "IAX2" if item.get("user_tech", {}).get(user) == "iax" else "PJSIP"


def render_extension(
    item: dict[str, Any],
    *,
    ring_timeout: str = "${RING_TIMEOUT}",
    record: dict[str, Any] | None = None,
) -> str:
    number = str(item["number"])
    strategy = str(item.get("strategy") or "single")
    users = [str(u) for u in (item.get("users") or [])]
    lines = [
        _header("extension", number).rstrip("\n"),
        "[extensions]",
        f"exten => {number},1,NoOp(openivr ext {number} {strategy})",
    ]
    if record and record.get("enabled"):
        directory = str(record.get("dir") or "").strip()
        if directory:
            lines.append(f" same => n,Set(MIXMONITOR_DIR={directory})")
            lines.append(" same => n,MixMonitor(${MIXMONITOR_DIR}/${UNIQUEID},b)")
    if strategy == "ringall":
        lines.append(f" same => n,Queue({number},t,,,{ring_timeout})")
    elif strategy == "linear":
        for index, user in enumerate(users):
            lines.append(f" same => n,Dial({_tech(item, user)}/{user},{ring_timeout})")
            if index < len(users) - 1:
                lines.append(' same => n,GotoIf($["${DIALSTATUS}" = "ANSWER"]?done)')
        lines.append(" same => n(done),Hangup()")
        return "\n".join(lines) + "\n"
    else:
        user = users[0] if users else number
        lines.append(f" same => n,Dial({_tech(item, user)}/{user},{ring_timeout})")
    lines.append(" same => n,Hangup()")
    return "\n".join(lines) + "\n"


def render_queue(item: dict[str, Any]) -> str:
    number = str(item["number"])
    users = [str(u) for u in (item.get("users") or [])]
    lines = [
        _header("queue", number).rstrip("\n"),
        f"[{number}]",
        "strategy = ringall",
        "timeout = 15",
        "retry = 5",
        "wrapuptime = 0",
        "maxlen = 0",
    ]
    for user in users:
        tech = "IAX2" if item.get("user_tech", {}).get(user) == "iax" else "PJSIP"
        lines.append(f"member = {tech}/{user}")
    return "\n".join(lines) + "\n"


def user_tech_map(endpoints: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(item.get("username")): str(item.get("protocol") or "pjsip").lower()
        for item in endpoints
    }


ASTERISK_VM_DIR = "/var/spool/asterisk/voicemail"


def render_voicemail(
    permissions: dict[str, Any],
    smtp: dict[str, Any] | None = None,
    skeleton: Path | None = None,
) -> str:
    """voicemail.conf from the Permissions step + the SMTP settings."""
    path = skeleton or (SKELETON_ROOT / "voicemail.conf")
    text = path.read_text(encoding="utf-8")
    relay = smtp if isinstance(smtp, dict) and smtp.get("enabled") else {}
    # Asterisk needs an absolute spool path; the builder's own copy directory
    # (config.yaml voicemail.dir) is where openivr keeps archived copies.
    maildir = str(permissions.get("voicemail_dir") or "")
    if not maildir.startswith("/"):
        maildir = ASTERISK_VM_DIR
    mapping = {
        "smtp_host": str(relay.get("host") or ""),
        "smtp_port": str(relay.get("port") or 587),
        "smtp_from": str(relay.get("from_address") or relay.get("from") or ""),
        "smtp_user": str(relay.get("username") or ""),
        "smtp_pass": str(relay.get("password") or ""),
        "maxsecs": str(permissions.get("voicemail_max_duration") or 120),
        "format": str(permissions.get("voicemail_format") or "wav"),
        "maildir": maildir,
    }
    body = fill_tokens(text, mapping)
    if not permissions.get("voicemail_enabled"):
        body = "; voicemail disabled in the builder (Permissions step)\n" + body
    cleaned: list[str] = []
    for line in body.splitlines(True):
        # Drop lines whose token resolved to nothing - Asterisk dislikes "key = ".
        if re.match(r"^[A-Za-z0-9_]+\s*=\s*$", line.rstrip("\n")):
            continue
        cleaned.append(line)
    return _header("voicemail", "general") + "".join(cleaned)


def render_all(root: Path) -> dict[str, str]:
    """Return mapping of relative path (under asterisk conf dir) -> contents."""
    trunk = load_trunk(root)
    endpoints = load_endpoints(root)
    extensions = load_extensions(root)
    permissions = load_permissions(root)
    record = {
        "enabled": bool(permissions.get("recording_enabled")),
        "dir": str(permissions.get("recording_dir") or ""),
    }
    tech = user_tech_map(endpoints)
    files: dict[str, str] = {}
    if trunk.get("provider_id"):
        files[f"endpoints/trunks/{trunk_name(trunk)}.conf"] = render_trunk(trunk)
    for user in endpoints:
        name = str(user.get("username") or "")
        proto = str(user.get("protocol") or "pjsip").lower()
        if proto == "iax":
            files[f"iax/{name}.conf"] = render_iax_user(user)
        else:
            files[f"endpoints/{name}.conf"] = render_endpoint(user)
    for ext in extensions:
        item = dict(ext)
        item["user_tech"] = tech
        number = str(item.get("number"))
        files[f"extensions/{number}.conf"] = render_extension(item, record=record)
        if str(item.get("strategy")) == "ringall":
            files[f"queues/{number}.conf"] = render_queue(item)
    files["voicemail.conf"] = render_voicemail(permissions, load_smtp(root))
    return files
