"""Builder HTTP surface: stepper wizard, draft persistence, media, publish and SMTP.

Uses ``fastapi.testclient.TestClient`` - no network, no running builder server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openivr.builder.providers import catalog_public, countries, provider_by_id, providers
from openivr.builder.publish import publish
from openivr.builder.render import render_all
from openivr.builder.server import (
    SESSION_COOKIE,
    create_app,
    get_auth,
    guard,
    read_smtp,
    write_smtp,
)
from openivr.builder.session import (
    load_endpoints,
    load_extensions,
    load_ivr,
    load_progress,
    load_trunk,
    save_endpoints,
    save_extensions,
    save_ivr,
    save_trunk,
)


@pytest.fixture
def app(project: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENIVR_BUILDER_TOKEN", raising=False)
    # Ensure project data directory exists
    (project / "data").mkdir(parents=True, exist_ok=True)
    (project / "data" / "sounds").mkdir(parents=True, exist_ok=True)
    application = create_app(project)
    application.state.keep_open = True
    return application


@pytest.fixture
def client(app):
    """A logged-in client: the builder protects every page with a session."""
    auth = get_auth(app, app.state.root)
    with TestClient(app) as test_client:
        login = test_client.post(
            "/login",
            data={"username": auth.username, "password": auth.generated_password},
            follow_redirects=False,
        )
        assert login.status_code == 303, login.text[:400]
        assert SESSION_COOKIE in login.cookies or SESSION_COOKIE in test_client.cookies
        yield test_client


@pytest.fixture
def anon_client(app):
    """A client that is *not* logged in (login/settings/guard tests)."""
    with TestClient(app) as test_client:
        yield test_client


def test_index_redirects_to_trunk(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/trunk"


def test_trunk_page_renders(client: TestClient) -> None:
    response = client.get("/trunk")
    assert response.status_code == 200
    assert "Trunk Configuration" in response.text
    assert "SIP Provider" in response.text
    assert "India" in response.text


def test_api_catalog_and_providers(client: TestClient) -> None:
    cat = client.get("/api/catalog").json()
    assert "countries" in cat and "providers" in cat
    assert len(cat["providers"]) >= 10

    in_providers = client.get("/api/providers?country=IN").json()
    assert any(p["id"] == "bsnl_wings" for p in in_providers)

    bsnl = client.get("/api/provider/bsnl_wings").json()
    assert bsnl["name"] == "BSNL Wings"
    assert bsnl["auth_type"] == "registration"

    missing = client.get("/api/provider/nonexistent")
    assert missing.status_code == 404


def test_trunk_save_valid(client: TestClient, project: Path) -> None:
    form = {
        "country": "IN",
        "provider_id": "bsnl_wings",
        "host_fqdn": "ims.bsnl.in",
        "transport": "transport-udp",
        "auth_type": "registration",
        "match_ips": "117.239.0.0/16\n218.248.0.0/16",
        "field__inbound_number": "919999999999",
        "field__username": "testuser",
        "field__password": "testpassword",
        "field__registration_uri": "sip:testuser@ims.bsnl.in",
    }
    response = client.post("/trunk", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "/endpoints" in response.headers["location"]

    trunk = load_trunk(project)
    assert trunk["provider_id"] == "bsnl_wings"
    assert trunk["fields"]["username"] == "testuser"
    assert len(trunk["match"]) == 2


def test_trunk_save_missing_required_fields(client: TestClient) -> None:
    form = {
        "country": "IN",
        "provider_id": "bsnl_wings",
        # missing credentials
    }
    response = client.post("/trunk", data=form)
    assert response.status_code == 400
    assert "required" in response.text.lower()


def test_endpoints_workflow(client: TestClient, project: Path) -> None:
    # GET empty
    response = client.get("/endpoints")
    assert response.status_code == 200
    assert "Endpoints" in response.text

    # POST valid endpoints
    form = {
        "username": ["alice", "bob"],
        "password": ["secret1", "secret2"],
        "display_name": ["Alice", "Bob"],
        "protocol": ["pjsip", "iax"],
    }
    response = client.post("/endpoints", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "/extensions" in response.headers["location"]

    eps = load_endpoints(project)
    assert len(eps) == 2
    assert eps[0]["username"] == "alice"
    assert eps[1]["protocol"] == "iax"


def test_endpoints_validation_rejects_duplicates(client: TestClient) -> None:
    form = {
        "username": ["alice", "alice"],
        "password": ["sec1", "sec2"],
        "display_name": ["Alice 1", "Alice 2"],
        "protocol": ["pjsip", "pjsip"],
    }
    response = client.post("/endpoints", data=form)
    assert response.status_code == 400
    assert "Duplicate" in response.text


def test_extensions_workflow(client: TestClient, project: Path) -> None:
    # Seed endpoints first
    save_endpoints(
        project,
        [
            {"username": "alice", "password": "p1"},
            {"username": "bob", "password": "p2"},
        ],
    )

    response = client.get("/extensions")
    assert response.status_code == 200
    assert "Extensions" in response.text

    # Post extension mapping
    form = {
        "ext_number_0": "1001",
        "ext_strategy_0": "single",
        "ext_users_0": "alice",
        "ext_number_1": "1002",
        "ext_strategy_1": "linear",
        "ext_users_1": "alice,bob",
    }
    response = client.post("/extensions", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "/dialplan" in response.headers["location"]

    exts = load_extensions(project)
    assert len(exts) == 2
    assert exts[0]["number"] == "1001"
    assert exts[0]["strategy"] == "single"
    assert exts[1]["strategy"] == "linear"
    assert exts[1]["users"] == ["alice", "bob"]


def test_extensions_validation_invalid_number(client: TestClient, project: Path) -> None:
    save_endpoints(project, [{"username": "alice", "password": "p1"}])
    form = {
        "ext_number_0": "invalid_num",
        "ext_strategy_0": "single",
        "ext_users_0": "alice",
    }
    response = client.post("/extensions", data=form)
    assert response.status_code == 400
    assert "1–999999" in response.text


def test_dialplan_workflow(client: TestClient, project: Path) -> None:
    save_endpoints(project, [{"username": "alice", "password": "p1"}])
    save_extensions(project, [{"number": "1001", "strategy": "single", "users": ["alice"]}])

    response = client.get("/dialplan")
    assert response.status_code == 200
    assert "IVR Dialplan" in response.text

    form = {
        "welcome_prompt": "sounds/welcome",
        "invalid_prompt": "sounds/invalid",
        "timeout_prompt": "sounds/timeout",
        "timeout_seconds": "10",
        "invalid_retries": "3",
        "timeout_retries": "3",
        "repeat_max": "3",
        "start_menu": "main",
        "menu_exists__main": "1",
        "menu_prompt__main": "sounds/main_menu",
        "dtmf_key__0__main": "1",
        "dtmf_description__0__main": "Support",
        "dtmf_action__0__main": "dial",
        "dtmf_endpoint__0__main": "1001",
    }
    response = client.post("/dialplan", data=form, follow_redirects=False)
    assert response.status_code == 303
    # permissions is the next wizard step
    assert "/permissions" in response.headers["location"]

    ivr = load_ivr(project)
    assert ivr["welcome_prompt"]["prompt"] == "sounds/welcome"
    assert ivr["menus"]["main"]["dtmf_options"]["1"]["action"] == "dial"
    assert ivr["menus"]["main"]["dtmf_options"]["1"]["endpoint"] == "1001"


def test_dialplan_menu_add_and_delete(client: TestClient, project: Path) -> None:
    client.post("/dialplan/menu/add", data={"menu_name": "sales", "menu_prompt_new": "sounds/sales"})
    ivr = load_ivr(project)
    assert "sales" in ivr["menus"]
    assert ivr["menus"]["sales"]["menu_prompt"] == "sounds/sales"

    client.post("/dialplan/menu/delete", data={"menu_name": "sales"})
    ivr = load_ivr(project)
    assert "sales" not in ivr["menus"]


def test_media_upload_and_delete(client: TestClient, project: Path) -> None:
    # Test file upload with snake_case conversion (prompts live on the dialplan step)
    response = client.post(
        "/dialplan/upload",
        files={"file": ("My Prompt 01.wav", b"RIFF....", "audio/wav")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "my_prompt_01.wav" in response.headers["location"]

    sounds_dir = project / "data" / "sounds"
    assert (sounds_dir / "my_prompt_01.wav").exists()

    # Delete
    response = client.post(
        "/dialplan/delete", data={"name": "my_prompt_01.wav"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert not (sounds_dir / "my_prompt_01.wav").exists()


def test_media_rejects_unknown_extension(client: TestClient, project: Path) -> None:
    response = client.post(
        "/dialplan/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["ok"] is False
    assert not (project / "data" / "sounds" / "notes.txt").exists()


def test_smtp_configuration(client: TestClient, project: Path) -> None:
    response = client.get("/smtp")
    assert response.status_code == 200

    form = {
        "enabled": "1",
        "host": "smtp.mailgun.org",
        "port": "587",
        "starttls": "1",
        "username": "postmaster@example.com",
        "password": "secretpassword",
        "from_addr": "ivr@example.com",
        "alerts_to": "admin@example.com, ops@example.com",
    }
    response = client.post("/smtp", data=form, follow_redirects=False)
    assert response.status_code == 303
    assert "/finish" in response.headers["location"]

    stored = read_smtp(project)
    assert stored["enabled"] is True
    assert stored["host"] == "smtp.mailgun.org"
    assert len(stored["alerts_to"]) == 2


def test_finish_and_publish_flow(
    client: TestClient, app, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Seed full valid setup
    save_trunk(
        project,
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
    save_endpoints(
        project,
        [
            {"username": "alice", "password": "pass1", "protocol": "pjsip"},
        ],
    )
    save_extensions(
        project,
        [
            {"number": "1001", "strategy": "single", "users": ["alice"]},
        ],
    )
    save_ivr(
        project,
        {
            "Version": "0.1.0",
            "welcome_prompt": {"path": "welcome", "prompt": "sounds/welcome"},
            "promotion_prompt": {"path": "", "prompt": ""},
            "invalid": {"prompt": "sounds/invalid", "repeat_prompt": True, "max_retries": 3, "fail_action": "hangup"},
            "timeout": {"seconds": 15, "prompt": "sounds/timeout", "repeat_prompt": True, "max_retries": 3, "fail_action": "hangup"},
            "repeat": {"max_attempts": 3, "fallback": "hangup"},
            "hold_music": {"prompt": ""},
            "start_menu": "main",
            "menus": {
                "main": {
                    "menu_prompt": "sounds/main",
                    "dtmf_options": {
                        "1": {"description": "Sales", "action": "dial", "endpoint": "1001"},
                    },
                }
            },
        },
    )

    # Mock reload script so it doesn't fail on missing asterisk
    monkeypatch.setattr("openivr.builder.publish.run_reload", lambda: (True, "mocked reload ok"))
    # a temp project has no system.json -> make this test the "system installed" path
    app.state.standalone = False

    # Finish page renders summary
    response = client.get("/finish")
    assert response.status_code == 200
    assert "Review &amp; Publish" in response.text
    assert "BSNL Wings" in response.text

    # Post finish (finalize JSON)
    response = client.post("/finish", follow_redirects=False)
    assert response.status_code == 303
    prog = load_progress(project)
    assert prog["finished"] is True

    # Post publish (generate conf files)
    dest_dir = project / "test_etc_asterisk"
    monkeypatch.setenv("OPENIVR_ASTERISK_ETC", str(dest_dir))
    response = client.post("/publish", follow_redirects=False)
    assert response.status_code == 200
    assert "Deployment Report" in response.text

    # Check conf files generated
    assert (dest_dir / "endpoints" / "trunks" / "bsnl_wings.conf").exists()
    assert (dest_dir / "endpoints" / "alice.conf").exists()
    assert (dest_dir / "extensions" / "1001.conf").exists()


def test_status_endpoint(client: TestClient, project: Path) -> None:
    data = client.get("/status").json()
    assert "progress" in data
    assert "endpoints_count" in data
    assert "extensions_count" in data


def test_token_security_guard(app, monkeypatch: pytest.MonkeyPatch) -> None:
    """A remote client needs the API token (or a session) to write."""
    monkeypatch.setenv("OPENIVR_BUILDER_TOKEN", "supersecret")
    remote = ("203.0.113.7", 40000)
    with TestClient(app, client=remote) as client:
        # pages need a session
        page = client.get("/trunk", follow_redirects=False)
        assert page.status_code == 303
        assert page.headers["location"].startswith("/login")

        # ...and writes do too (no token, no session)
        denied = client.post("/dialplan/menu/add", data={"menu_name": "test"}, follow_redirects=False)
        assert denied.status_code == 303
        assert denied.headers["location"].startswith("/login")
        assert "test" not in load_ivr(app.state.root).get("menus", {})

        # the token in a header is accepted
        assert (
            client.post(
                "/dialplan/menu/add",
                data={"menu_name": "test"},
                headers={"x-openivr-token": "supersecret"},
                follow_redirects=False,
            ).status_code
            == 303
        )
        assert "test" in load_ivr(app.state.root).get("menus", {})

        # ...and as a query parameter
        assert (
            client.post(
                "/dialplan/menu/add?token=supersecret",
                data={"menu_name": "test2"},
                follow_redirects=False,
            ).status_code
            == 303
        )

    # ...and a logged-in remote session works without the token
    auth = get_auth(app, app.state.root)
    with TestClient(app, client=remote) as client:
        login = client.post(
            "/login",
            data={"username": auth.username, "password": auth.generated_password},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert client.get("/trunk").status_code == 200
        assert (
            client.post(
                "/dialplan/menu/add", data={"menu_name": "sales"}, follow_redirects=False
            ).status_code
            == 303
        )


def test_guard_rejects_remote_writes_without_token_or_session(app) -> None:
    """The route-level guard is defence in depth behind the middleware."""
    from starlette.requests import Request

    def remote_request() -> Request:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/publish",
            "query_string": b"",
            "headers": [(b"host", b"example.org")],
            "client": ("203.0.113.7", 40000),
            "scheme": "https",
            "server": ("example.org", 443),
            "root_path": "",
            "app": app,
        }
        return Request(scope)

    assert guard(remote_request()).status_code == 403
