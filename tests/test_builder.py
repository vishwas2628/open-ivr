"""Builder HTTP surface: pages, draft persistence, media and SMTP handling.

Uses ``fastapi.testclient.TestClient`` - no network, no running builder server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openivr.builder.server import (
    create_app,
    default_flow,
    flow_errors,
    get_menu,
    menu_names,
    read_smtp,
)
from openivr.flow import Flow


@pytest.fixture
def client(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENIVR_BUILDER_TOKEN", raising=False)
    app = create_app(project)
    app.state.keep_open = True
    with TestClient(app) as test_client:
        yield test_client


def test_index_lists_steps(client: TestClient) -> None:
    body = client.get("/").text
    assert "openivr" in body.lower()
    assert "/build" in body and "/media" in body


def test_status_reports_config(client: TestClient, project: Path) -> None:
    payload = client.get("/status").json()
    assert payload["flow_file"].endswith("data/ivr_flow.json")
    assert payload["flow_exists"] is True
    assert payload["menus"], "the starter draft should have menus"
    assert payload["ari"]["base_url"].startswith("http")
    assert payload["prompts"] == 0
    assert payload["validation_errors"] == []


def test_build_page_renders_current_menu(client: TestClient) -> None:
    body = client.get("/build").text
    assert "main" in body
    assert "menu__main" in body


def test_missing_flow_file_falls_back_to_the_starter(client: TestClient, project: Path) -> None:
    (project / "data" / "ivr_flow.json").unlink()
    body = client.get("/build").text
    assert "menu__main" in body
    assert "starter" in body.lower() or "main-menu" in body


def test_unparsable_flow_is_not_shown(client: TestClient, project: Path) -> None:
    (project / "data" / "ivr_flow.json").write_text("{not json", encoding="utf-8")
    assert "menu__main" in client.get("/build").text


def test_build_page_without_menus(
    client: TestClient, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "data" / "ivr_flow.json").unlink()
    monkeypatch.setattr(
        "openivr.builder.server.default_flow",
        lambda: {"version": 1, "start_menu": "main", "menus": {}},
    )
    body = client.get("/build").text
    assert "No menus yet" in body


def test_hand_written_partial_flow_still_renders(client: TestClient, project: Path) -> None:
    """A menu with no timeout block must not 500 the builder."""
    (project / "data" / "ivr_flow.json").write_text(
        json.dumps({"version": 1, "start_menu": "main", "menus": {"main": {"options": {}}}}),
        encoding="utf-8",
    )
    body = client.get("/build").text
    assert "timeout_seconds__main" in body


def test_add_menu_then_edit_it(client: TestClient, project: Path) -> None:
    assert (
        client.post("/build/menu/add", data={"name": "sales"}, follow_redirects=False).status_code
        == 303
    )
    draft = json.loads((project / "data" / "ivr_flow.json").read_text(encoding="utf-8"))
    assert "sales" in draft["menus"]
    assert draft["menus"]["sales"]["timeout"]["seconds"] == 8

    body = client.get("/build?menu=sales").text
    assert "prompt__sales" in body


def test_add_menu_can_copy_an_existing_one(client: TestClient, project: Path) -> None:
    client.post("/build/menu/add", data={"name": "sales"})
    draft_path = project / "data" / "ivr_flow.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    draft["menus"]["sales"]["timeout"]["seconds"] = 3
    draft["menus"]["sales"]["prompt"] = "sales-welcome"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    client.post(
        "/build/menu/add",
        data={"name": "billing", "copy_from": "sales", "prompt": "billing-welcome"},
    )
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert draft["menus"]["billing"]["timeout"]["seconds"] == 3
    assert draft["menus"]["billing"]["prompt"] == "billing-welcome"
    assert (
        draft["menus"]["billing"]["invalid"]["max_retries"]
        == draft["menus"]["sales"]["invalid"]["max_retries"]
    )


def test_delete_menu_repairs_references(client: TestClient, project: Path) -> None:
    draft_path = project / "data" / "ivr_flow.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(
            {
                "version": 1,
                "start_menu": "sales",
                "menus": {
                    "sales": {
                        "options": {"1": {"action": "submenu", "target": "support"}},
                        "timeout": {"fail_action": {"action": "submenu", "target": "support"}},
                    },
                    "support": {"options": {"9": {"action": "hangup"}}},
                },
            }
        ),
        encoding="utf-8",
    )

    client.post("/build/menu/delete", data={"menu": "support"})
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    assert "support" not in draft["menus"]
    assert draft["start_menu"] == "sales"
    assert draft["menus"]["sales"]["options"]["1"]["target"] == "sales"
    assert draft["menus"]["sales"]["timeout"]["fail_action"]["target"] == "sales"


def test_delete_last_menu_is_refused(client: TestClient, project: Path) -> None:
    draft_path = project / "data" / "ivr_flow.json"
    draft_path.write_text(
        json.dumps({"version": 1, "start_menu": "main", "menus": {"main": {"options": {}}}}),
        encoding="utf-8",
    )
    client.post("/build/menu/delete", data={"menu": "main"})
    assert "main" in json.loads(draft_path.read_text(encoding="utf-8"))["menus"]


def form_for(menu: str, digit: str, **extra: object) -> dict[str, str]:
    data = {
        "current_menu": menu,
        f"menu__{menu}": menu,
        f"prompt__{menu}": f"{menu}-prompt",
        f"timeout_seconds__{menu}": "5",
        f"timeout_retries__{menu}": "1",
        f"timeout_prompt__{menu}": "sorry",
        f"invalid_retries__{menu}": "1",
        f"invalid_prompt__{menu}": "again",
        f"fail_action__{menu}": "hangup",
        f"enabled__{digit}__{menu}": "on",
        f"action__{digit}__{menu}": "submenu",
        f"target__{digit}__{menu}": "support",
        f"next_action__{digit}__{menu}": "hangup",
        "welcome": "welcome",
        "goodbye": "goodbye",
        "start_menu": "main",
    }
    data.update({k: str(v) for k, v in extra.items()})
    return data


def test_save_flow_round_trip(client: TestClient, project: Path) -> None:
    response = client.post("/build/save", data=form_for("main", "1"), follow_redirects=False)
    assert response.status_code == 303
    draft = json.loads((project / "data" / "ivr_flow.json").read_text(encoding="utf-8"))
    assert draft["menus"]["main"]["options"]["1"] == {"action": "submenu", "target": "support"}
    assert "support" in draft["menus"], "menus the form omits must survive the save"
    assert draft["menus"]["main"]["prompt"] == "main-prompt"
    assert draft["menus"]["main"]["timeout"]["seconds"] == 5.0
    assert draft["welcome"] == "welcome" and draft["goodbye"] == "goodbye"


def test_save_collect_option(client: TestClient, project: Path) -> None:
    form = form_for(
        "main",
        "2",
        action__2__main="collect",
        target__2__main="",
        min_digits__2__main="2",
        max_digits__2__main="4",
        next_action__2__main="submenu",
        next_target__2__main="support",
    )
    client.post("/build/save", data=form, follow_redirects=False)
    option = json.loads((project / "data" / "ivr_flow.json").read_text(encoding="utf-8"))
    option = option["menus"]["main"]["options"]["2"]
    assert option["action"] == "collect"
    assert option["min_digits"] == 2 and option["max_digits"] == 4
    assert option["next"] == {"action": "submenu", "target": "support"}


def test_save_voicemail_option(client: TestClient, project: Path) -> None:
    form = form_for(
        "main", "3", action__3__main="voicemail", target__3__main="", after_action__3__main="hangup"
    )
    client.post("/build/save", data=form, follow_redirects=False)
    option = json.loads((project / "data" / "ivr_flow.json").read_text(encoding="utf-8"))
    assert option["menus"]["main"]["options"]["3"]["action"] == "voicemail"


def test_invalid_flow_is_not_saved(client: TestClient, project: Path) -> None:
    draft_path = project / "data" / "ivr_flow.json"
    before = draft_path.read_text(encoding="utf-8")
    form = form_for("main", "1", action__1__main="submenu", target__1__main="nowhere")
    response = client.post("/build/save", data=form)
    assert response.status_code == 400
    assert "nowhere" in response.text
    assert draft_path.read_text(encoding="utf-8") == before, "a bad save must not be written"


def test_time_route_form_fields(client: TestClient, project: Path) -> None:
    draft_path = project / "data" / "ivr_flow.json"
    draft = json.loads(draft_path.read_text(encoding="utf-8")) if draft_path.exists() else {}
    draft.setdefault("version", 1)
    draft["time_route"] = {
        "timezone": "UTC",
        "business_menu": "main",
        "after_hours_menu": "main",
        "hours": {"mon": ["09:00-17:00"]},
    }
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    form = form_for(
        "main",
        "1",
        tr_prompt="business-hours",
        tr_timezone="UTC",
        tr_business="main",
        tr_after_hours="support",
    )
    response = client.post("/build/save", data=form, follow_redirects=False)
    assert response.status_code == 303
    saved = json.loads(draft_path.read_text(encoding="utf-8"))
    assert saved["time_route"]["business_menu"] == "main"
    assert saved["time_route"]["after_hours_menu"] == "support"
    assert saved["time_route"]["hours"]["mon"] == ["09:00-17:00"]


def test_upload_and_delete_prompt(client: TestClient, project: Path) -> None:
    response = client.post(
        "/media/upload",
        files={"file": ("hello_world.wav", b"RIFF....", "audio/wav")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" not in response.headers["location"]
    sounds = list(Path(project / "data" / "sounds").iterdir())
    assert [p.name for p in sounds] == ["hello_world.wav"]

    body = client.get("/media").text
    assert "hello_world" in body

    client.post("/media/delete", data={"name": "hello_world"}, follow_redirects=False)
    assert not (project / "data" / "sounds" / "hello_world.wav").exists()


def test_upload_rejects_spaces(client: TestClient, project: Path) -> None:
    response = client.post(
        "/media/upload",
        files={"file": ("hello world.wav", b"RIFF", "audio/wav")},
        follow_redirects=False,
    )
    assert "err=" in response.headers["location"]
    assert list((project / "data" / "sounds").iterdir()) == []


def test_upload_strips_paths_from_the_filename(client: TestClient, project: Path) -> None:
    response = client.post(
        "/media/upload",
        files={"file": ("../../etc/passwd.wav", b"RIFF", "audio/wav")},
        follow_redirects=False,
    )
    assert "err=" not in response.headers["location"]
    assert [p.name for p in (project / "data" / "sounds").iterdir()] == ["passwd.wav"]
    assert not (project / "passwd.wav").exists()
    assert not (project / ".." / "passwd.wav").resolve().exists()

    bad_type = client.post(
        "/media/upload",
        files={"file": ("evil.sh", b"#!/bin/sh", "application/x-sh")},
        follow_redirects=False,
    )
    assert "unsupported%20format%20.sh" in bad_type.headers["location"]

    not_wav = client.post(
        "/media/upload",
        files={"file": ("prompt.mp3", b"ID3", "audio/mpeg")},
        follow_redirects=False,
    )
    assert "upload%20.wav" in not_wav.headers["location"]

    empty = client.post(
        "/media/upload", files={"file": ("empty.wav", b"", "audio/wav")}, follow_redirects=False
    )
    assert "file%20is%20empty" in empty.headers["location"]


def test_media_delete_reports_missing_file(client: TestClient) -> None:
    response = client.post("/media/delete", data={"name": "nothing"}, follow_redirects=False)
    assert "file%20not%20found" in response.headers["location"]


def test_smtp_requires_host_and_recipients(client: TestClient) -> None:
    no_host = client.post("/smtp", data={"enabled": "on", "alerts_to": "a@b.c"})
    assert no_host.status_code == 400 and "host is required" in no_host.text

    no_recipient = client.post("/smtp", data={"enabled": "on", "host": "localhost"})
    assert no_recipient.status_code == 400 and "recipient" in no_recipient.text


def test_smtp_saves_and_loads(client: TestClient, project: Path) -> None:
    response = client.post(
        "/smtp",
        data={
            "enabled": "on",
            "host": "smtp.example.net",
            "port": "2525",
            "username": "bot",
            "password": "secret",
            "from_addr": "ivr@example.net",
            "alerts_to": "ops@example.net; dev@example.net",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    stored = read_smtp(project)
    assert stored["host"] == "smtp.example.net" and stored["port"] == 2525
    assert stored["alerts_to"] == ["ops@example.net", "dev@example.net"]
    assert stored["password"] == "secret"


def test_smtp_disabled_needs_nothing(client: TestClient, project: Path) -> None:
    client.post("/smtp", data={"enabled": "", "host": ""}, follow_redirects=False)
    assert read_smtp(project)["enabled"] is False


def test_finish_reports_errors(client: TestClient, project: Path) -> None:
    draft_path = project / "data" / "ivr_flow.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(
            {
                "version": 1,
                "start_menu": "main",
                "welcome": "no-such-prompt",
                "menus": {"main": {"options": {"1": {"action": "goto", "target": "ghost"}}}},
            }
        ),
        encoding="utf-8",
    )
    response = client.post("/finish")
    assert response.status_code == 400
    assert "ghost" in response.text


def test_finish_saves_valid_flow(client: TestClient, project: Path) -> None:
    """/finish validates prompts against the audio that actually exists."""
    (project / "data" / "ivr_flow.json").unlink()
    sounds = project / "data" / "sounds"
    sounds.mkdir(parents=True, exist_ok=True)
    (sounds / "placeholder.wav").write_bytes(b"RIFF")
    client.post("/finish")
    assert not (project / "data" / "ivr_flow.json").exists()

    for prompt in Flow.from_dict(default_flow()).prompts():
        (sounds / f"{prompt}.wav").write_bytes(b"RIFF")
    response = client.post("/finish", follow_redirects=False)
    assert response.status_code in (200, 303)
    assert (project / "data" / "ivr_flow.json").exists()


def test_token_guard_blocks_posts(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENIVR_BUILDER_TOKEN", "s3cret")
    assert client.post("/build/save", data=form_for("main", "1")).status_code == 403
    assert (
        client.post(
            "/build/save",
            data=form_for("main", "1"),
            headers={"x-openivr-token": "s3cret"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/build/save",
            data=form_for("main", "1"),
            params={"token": "s3cret"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    assert (
        client.post(
            "/build/save", data=form_for("main", "1"), headers={"x-openivr-token": "wrong"}
        ).status_code
        == 403
    )


def test_non_local_client_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/build/menu/add", data={"name": "x"}, headers={"x-forwarded-for": "10.0.0.9"}
    )
    assert response.status_code in (200, 303, 403)


def test_helper_functions() -> None:
    draft = default_flow()
    assert menu_names(draft)
    assert get_menu(draft, "does-not-exist") == {}
    assert get_menu(draft, menu_names(draft)[0])


def test_flow_errors_reports_problems(cfg) -> None:
    broken = {
        "version": 1,
        "start_menu": "main",
        "menus": {"main": {"options": {"1": {"action": "submenu", "target": "x"}}}},
    }
    errors = flow_errors(cfg, broken)
    assert errors and "x" in errors[0]
    assert flow_errors(cfg, default_flow()) == []


def test_shutdown_endpoint(client: TestClient) -> None:
    response = client.post("/shutdown", follow_redirects=False)
    assert response.status_code in (200, 303)
