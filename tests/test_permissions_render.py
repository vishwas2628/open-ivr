"""Permissions, voicemail rendering and the standalone (no-Asterisk) publish path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openivr.builder.publish import publish
from openivr.builder.render import (
    ASTERISK_VM_DIR,
    fill_tokens,
    render_all,
    render_extension,
    render_voicemail,
)
from openivr.builder.server import create_app, get_auth
from openivr.builder.session import (
    DEFAULT_PERMISSIONS,
    VOICEMAIL_FORMATS,
    load_permissions,
    save_permissions,
    save_smtp,
    validate_permissions,
)


def _seed(root: Path) -> None:
    from openivr.builder.session import save_endpoints, save_extensions, save_trunk

    save_trunk(
        root,
        {
            "country": "IN",
            "provider_id": "bsnl_wings",
            "name": "BSNL Wings",
            "auth_type": "registration",
            "host_fqdn": "ims.bsnl.in",
            "transport": "transport-udp",
            "match": ["117.239.0.0/16"],
            "fields": {
                "inbound_number": "919999999999",
                "username": "user",
                "password": "pwd",
                "registration_uri": "sip:user@ims.bsnl.in",
            },
        },
    )
    save_endpoints(root, [{"username": "alice", "password": "pass1", "protocol": "pjsip"}])
    save_extensions(root, [{"number": "1001", "strategy": "single", "users": ["alice"]}])


# ------------------------------------------------------------- permissions


def test_default_permissions(project: Path) -> None:
    values = load_permissions(project)
    for key, default in DEFAULT_PERMISSIONS.items():
        assert values[key] == default
    assert validate_permissions(values) == []


def test_save_and_reload_permissions(project: Path) -> None:
    save_permissions(
        project,
        {
            "recording_enabled": True,
            "recording_dir": "/var/spool/asterisk/monitor",
            "recording_retention_days": 14,
            "voicemail_enabled": True,
            "voicemail_dir": "data/recordings/voicemail",
            "voicemail_format": "ulaw",
            "voicemail_max_duration": 90,
        },
    )
    again = load_permissions(project)
    assert again["recording_enabled"] is True
    assert again["voicemail_format"] == "ulaw"
    stored = json.loads((project / "data" / "permissions.json").read_text())
    assert stored["permissions"]["recording_retention_days"] == 14


@pytest.mark.parametrize(
    ("override", "feature"),
    [
        ({"recording_dir": ""}, "recording"),
        ({"recording_retention_days": 0}, "recording"),
        ({"recording_retention_days": -3}, "recording"),
        ({"voicemail_dir": ""}, "voicemail"),
        ({"voicemail_format": "mp3"}, "voicemail"),
        ({"voicemail_max_duration": 0}, "voicemail"),
        ({"voicemail_max_duration": 5000}, "voicemail"),
    ],
)
def test_validate_permissions_rejects_bad_values(
    project: Path, override: dict, feature: str
) -> None:
    values = dict(DEFAULT_PERMISSIONS)
    values[f"{feature}_enabled"] = True
    values.update(override)
    assert validate_permissions(values) != [], override


def test_validate_permissions_ignores_disabled_features(project: Path) -> None:
    """A blank directory is fine while the feature is switched off."""
    values = dict(DEFAULT_PERMISSIONS)
    values["recording_enabled"] = False
    values["voicemail_enabled"] = False
    values["recording_dir"] = ""
    values["voicemail_dir"] = ""
    values["recording_retention_days"] = 0
    values["voicemail_max_duration"] = 0
    assert validate_permissions(values) == []


def test_voicemail_formats_include_asterisk_native() -> None:
    for fmt in ("wav", "ulaw", "alaw", "gsm", "sln", "sln16"):
        assert fmt in VOICEMAIL_FORMATS
    assert "mp3" not in VOICEMAIL_FORMATS


# ------------------------------------------------------------------ render


def test_render_extension_records_only_when_enabled() -> None:
    item = {"number": "1001", "strategy": "single", "users": ["alice"]}
    plain = render_extension(item)
    assert "MixMonitor" not in plain
    recorded = render_extension(item, record={"enabled": True, "dir": "/var/spool/asterisk/monitor"})
    assert "MixMonitor" in recorded
    assert "/var/spool/asterisk/monitor" in recorded
    assert recorded.index("MixMonitor") < recorded.index("Hangup")


def test_render_voicemail_substitutes_tokens_and_keeps_comments() -> None:
    permissions = dict(DEFAULT_PERMISSIONS)
    permissions.update(
        {
            "voicemail_enabled": True,
            "voicemail_format": "ulaw",
            "voicemail_max_duration": 45,
            "voicemail_dir": "data/recordings/voicemail",
        }
    )
    text = render_voicemail(
        permissions,
        {"enabled": True, "host": "smtp.example.com", "port": 587, "from_address": "ivr@x.test"},
    )
    assert "format = ulaw" in text
    assert "maxsecs = 45" in text
    assert f"maildir = {ASTERISK_VM_DIR}" in text
    assert "smtpserver = smtp.example.com" in text
    # every token outside the documentation comments was filled
    body = [ln for ln in text.splitlines() if not ln.lstrip().startswith((";", "#"))]
    assert "{{" not in "\n".join(body)
    # the skeleton's token documentation survives untouched
    assert "{{format}}" in text


def test_render_voicemail_without_smtp_drops_the_relay() -> None:
    """No SMTP configured: the lines are dropped, voicemail still works."""
    permissions = dict(DEFAULT_PERMISSIONS)
    permissions["voicemail_enabled"] = True
    text = render_voicemail(permissions, {"enabled": False})
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith((";", "#")))
    assert "smtpserver" not in body
    assert "smtpfrom" not in body
    assert "[general]" in text and "[default]" in text


def test_fill_tokens_skips_comment_lines() -> None:
    text = "; docs {{token}}\nreal = {{token}}\n# also {{token}}\n"
    out = fill_tokens(text, {"token": "value"})
    assert out == "; docs {{token}}\nreal = value\n# also {{token}}\n"


def test_render_all_includes_voicemail_and_queues(project: Path) -> None:
    _seed(project)
    save_permissions(project, {**DEFAULT_PERMISSIONS, "voicemail_enabled": True})
    from openivr.builder.session import save_extensions

    save_extensions(
        project,
        [
            {"number": "1001", "strategy": "ringall", "users": ["alice"]},
            {"number": "1002", "strategy": "single", "users": ["alice"]},
        ],
    )
    files = render_all(project)
    assert "voicemail.conf" in files
    assert "extensions/1001.conf" in files and "queues/1001.conf" in files
    assert "extensions/1002.conf" in files and "queues/1002.conf" not in files
    assert "endpoints/alice.conf" in files
    assert "endpoints/trunks/bsnl_wings.conf" in files


# --------------------------------------------------------------- publishing


def test_publish_renders_only_in_standalone_mode(project: Path) -> None:
    _seed(project)
    result = publish(project, standalone=True)
    assert result["standalone"] is True
    assert result["copied"] == []
    assert result["copy_error"] == ""
    assert result["dest"] == result["staging"]
    staged = sorted(p.name for p in (project / "data" / "asterisk-build").iterdir())
    assert "voicemail.conf" in staged
    assert "extensions" in staged


def test_publish_copies_to_a_custom_destination(project: Path) -> None:
    _seed(project)
    dest = project / "custom-etc"
    result = publish(project, dest=dest, reload=False)
    assert result["copy_error"] == ""
    assert result["reload_detail"] == "skipped"
    assert (dest / "extensions" / "1001.conf").is_file()
    assert (dest / "voicemail.conf").is_file()


def _seed_ivr(root: Path) -> None:
    from openivr.builder.session import save_ivr

    save_ivr(
        root,
        {
            "welcome_prompt": {"path": "", "prompt": "welcome"},
            "invalid": {"prompt": "invalid", "max_retries": 3, "fail_action": "hangup"},
            "timeout": {
                "seconds": 15,
                "prompt": "timeout",
                "repeat_prompt": True,
                "max_retries": 3,
                "fail_action": "hangup",
            },
            "repeat": {"max_attempts": 3, "fallback": "hangup"},
            "hold_music": {"prompt": ""},
            "start_menu": "main",
            "menus": {
                "main": {
                    "menu_prompt": "main",
                    "dtmf_options": {
                        "1": {"description": "Alice", "action": "dial", "endpoint": "1001"}
                    },
                }
            },
        },
    )


def test_standalone_builder_flow(project: Path) -> None:
    """No system.json: the wizard works and deploy stays local."""
    app = create_app(project)
    assert app.state.standalone is True
    auth = get_auth(app, project)
    with TestClient(app) as client:
        assert client.post(
            "/login",
            data={"username": auth.username, "password": auth.generated_password},
            follow_redirects=False,
        ).status_code == 303
        # the banner explains why deploy is inert
        assert "Standalone mode" in client.get("/trunk").text
        _seed(project)
        _seed_ivr(project)
        body = client.post("/publish", follow_redirects=False).text
        assert "Configuration Generated" in body
        assert "Generated Files" in body
    assert not (project / "system.json").exists()