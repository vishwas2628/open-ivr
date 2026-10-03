"""Render Asterisk conf fragments from builder JSON."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .session import load_endpoints, load_extensions, load_trunk

SKELETON_ROOT = Path(__file__).resolve().parents[2] / "system" / "asterisk"
SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def trunk_name(trunk: dict[str, Any]) -> str:
    raw = str(trunk.get("name") or trunk.get("provider_id") or "trunk")
    slug = SAFE.sub("_", raw).strip("_").lower() or "trunk"
    return slug[:32]


def fill_tokens(text: str, mapping: dict[str, str]) -> str:
    out = text
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


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
    username = str(fields.get("username") or trunk.get("username") or "").strip()
    password = str(fields.get("password") or trunk.get("password") or "").strip()
    client_uri = str(fields.get("registration_uri") or trunk.get("registration_uri") or "").strip()
    if client_uri and not client_uri.lower().startswith("sip:"):
        client_uri = f"sip:{client_uri}"
    if not client_uri and username and host:
        client_uri = f"sip:{username}@{host}"
    transport = str(trunk.get("transport") or "transport-udp")
    inbound = str(trunk.get("inbound_context") or "from-trunk")
    matches = [m for m in (trunk.get("match") or []) if str(m).strip()]
    mapping = {
        "trunk_username": username,
        "trunk_password": password,
        "host_fqdn": host,
        "trunk_transport": transport,
        "inbound_context": inbound,
        "client_uri": client_uri,
        "trunk_ip_1": matches[0] if len(matches) > 0 else "",
        "trunk_ip_2": matches[1] if len(matches) > 1 else "",
        "trunk_ip_3": matches[2] if len(matches) > 2 else "",
    }
    body = fill_tokens(text, mapping).replace("TRUNK", name)
    extra = matches[3:]
    if extra:
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
    if auth_type == "ip" and not str(fields.get("registration_uri") or "").strip():
        body = _drop_section(body, f"{name}-reg")
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


def render_extension(item: dict[str, Any], *, ring_timeout: str = "${RING_TIMEOUT}") -> str:
    number = str(item["number"])
    strategy = str(item.get("strategy") or "single")
    users = [str(u) for u in (item.get("users") or [])]
    lines = [
        _header("extension", number).rstrip("\n"),
        "[extensions]",
        f"exten => {number},1,NoOp(openivr ext {number} {strategy})",
    ]
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


def render_all(root: Path) -> dict[str, str]:
    """Return mapping of relative path (under asterisk conf dir) -> contents."""
    trunk = load_trunk(root)
    endpoints = load_endpoints(root)
    extensions = load_extensions(root)
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
        files[f"extensions/{number}.conf"] = render_extension(item)
        if str(item.get("strategy")) == "ringall":
            files[f"queues/{number}.conf"] = render_queue(item)
    return files
