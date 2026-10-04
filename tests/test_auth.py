"""Builder authentication: first-run provisioning, login, sessions, settings.

Everything runs offline against a ``tmp_path`` project root.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openivr.builder.auth import (
    DEFAULT_TTL,
    MIN_PASSWORD_LENGTH,
    AuthError,
    BuilderAuth,
    generate_password,
    hash_password,
    load_auth,
    save_credentials,
    verify_password,
)
from openivr.builder.server import SESSION_COOKIE, create_app, get_auth
from openivr.config import load_config, read_config_value


@pytest.fixture()
def app(tmp_path: Path):
    (tmp_path / "data" / "sounds").mkdir(parents=True)
    return create_app(tmp_path)


def _login(client: TestClient, auth: BuilderAuth):
    return client.post(
        "/login",
        data={"username": auth.username, "password": auth.generated_password},
        follow_redirects=False,
    )


# ----------------------------------------------------------------- password


def test_hash_and_verify_roundtrip() -> None:
    digest = hash_password("correct horse battery")
    assert digest.startswith("$2")
    assert "correct horse" not in digest
    assert verify_password("correct horse battery", digest) is True
    assert verify_password("wrong", digest) is False


def test_hash_rejects_short_password() -> None:
    with pytest.raises(AuthError):
        hash_password("x" * (MIN_PASSWORD_LENGTH - 1))


def test_generated_passwords_are_unique_and_long_enough() -> None:
    seen = {generate_password() for _ in range(5)}
    assert len(seen) == 5
    assert all(len(p) >= MIN_PASSWORD_LENGTH for p in seen)


# ------------------------------------------------------------- provisioning


def test_first_run_provisions_credentials(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    assert auth.username  # derived from the company/hostname
    assert auth.generated_password
    assert auth.configured is True
    # the plaintext is gone from disk, only the hash remains
    assert read_config_value(tmp_path, "builder.password_hash").startswith("$2")
    assert auth.generated_password not in (tmp_path / "config.yaml").read_text()


def test_provisioning_happens_once(app, tmp_path: Path) -> None:
    first = get_auth(app, tmp_path)
    password = first.generated_password
    fresh_app = create_app(tmp_path)
    again = get_auth(fresh_app, tmp_path)
    assert again.generated_password == ""
    assert again.check(again.username, password) is True


def test_load_auth_without_provisioning(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, bootstrap=True)
    auth = load_auth(tmp_path, cfg, provision=False)
    assert auth.configured is False
    assert auth.local_only is True  # no credentials, no token -> local fallback
    assert read_config_value(tmp_path, "builder.password_hash") == ""


def test_session_ttl_defaults_and_override(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    assert auth.session_ttl == DEFAULT_TTL

    cfg = load_config(tmp_path)
    cfg.builder.session_ttl = 900
    from openivr.config import set_config_value

    set_config_value(tmp_path, "builder.session_ttl", 900)
    cfg2 = load_config(tmp_path)
    assert load_auth(tmp_path, cfg2, provision=False).session_ttl == 900


# --------------------------------------------------------------------- HTTP


def test_pages_need_a_session(app) -> None:
    with TestClient(app) as client:
        for path in ("/trunk", "/endpoints", "/extensions", "/dialplan", "/permissions", "/smtp", "/finish"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, path
            assert response.headers["location"].startswith("/login"), path
        api = client.get("/api/catalog")
        assert api.status_code == 401
        assert "unauthorized" in api.json()["error"]


def test_login_page_and_static_are_public(app) -> None:
    with TestClient(app) as client:
        assert client.get("/login").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/favicon.ico").status_code in (200, 404)


def test_login_wrong_password(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        bad = client.post("/login", data={"username": auth.username, "password": "nope"})
        assert bad.status_code == 401
        assert "Wrong username or password" in bad.text
        assert SESSION_COOKIE not in client.cookies


def test_login_sets_session_cookie(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        response = _login(client, auth)
        assert response.status_code == 303
        cookie = response.headers.get("set-cookie", "")
        assert SESSION_COOKIE in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie.replace("samesite", "SameSite")
        # a session cookie now opens the wizard
        assert client.get("/trunk").status_code == 200


def test_logout_clears_the_session(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        _login(client, auth)
        assert client.get("/trunk").status_code == 200
        response = client.get("/logout", follow_redirects=False)
        assert response.status_code == 303
        assert client.get("/trunk", follow_redirects=False).status_code == 303


def test_expired_session_is_rejected(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        _login(client, auth)
        # the TTL is server side: shrink the window and let the cookie lapse.
        # itsdangerous compares integer seconds, so sleep past a full second.
        auth.session_ttl = 1
        stale = auth.issue(auth.username)
        time.sleep(2.05)
        client.cookies.set(SESSION_COOKIE, stale)
        assert client.get("/trunk", follow_redirects=False).status_code == 303
        auth.session_ttl = DEFAULT_TTL


def test_tampered_cookie_is_rejected(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, auth.issue(auth.username) + "x")
        assert client.get("/trunk", follow_redirects=False).status_code == 303


# ----------------------------------------------------------------- settings


def test_settings_change_password(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    old = auth.generated_password
    with TestClient(app) as client:
        _login(client, auth)
        response = client.post(
            "/settings",
            data={
                "username": auth.username,
                "current_password": old,
                "new_password": "a-much-better-passphrase",
                "new_password_confirm": "a-much-better-passphrase",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert auth.check(auth.username, "a-much-better-passphrase") is True
        assert auth.check(auth.username, old) is False
        # the new password survives a restart
        assert get_auth(create_app(tmp_path), tmp_path).check(auth.username, "a-much-better-passphrase")


def test_settings_requires_current_password(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    original = auth.username
    with TestClient(app) as client:
        _login(client, auth)
        response = client.post(
            "/settings",
            data={
                "username": "attacker",
                "current_password": "not-my-password",
                "new_password": "",
                "new_password_confirm": "",
            },
        )
        assert response.status_code == 400
        assert auth.username == original


def test_settings_rejects_mismatched_confirmation(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        _login(client, auth)
        response = client.post(
            "/settings",
            data={
                "username": auth.username,
                "current_password": auth.generated_password,
                "new_password": "one-password-here",
                "new_password_confirm": "another-password",
            },
        )
        assert response.status_code == 400
        assert auth.check(auth.username, auth.generated_password) is True


def test_settings_rejects_short_new_password(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with TestClient(app) as client:
        _login(client, auth)
        response = client.post(
            "/settings",
            data={
                "username": auth.username,
                "current_password": auth.generated_password,
                "new_password": "short",
                "new_password_confirm": "short",
            },
        )
        assert response.status_code == 400
        assert str(MIN_PASSWORD_LENGTH) in response.text


def test_save_credentials_requires_a_username(app, tmp_path: Path) -> None:
    auth = get_auth(app, tmp_path)
    with pytest.raises(AuthError):
        save_credentials(tmp_path, auth, username="   ", password=None)


# -------------------------------------------------------------------- CLI


def test_creds_command_resets_the_password(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from openivr.__main__ import main

    assert main(["--root", str(tmp_path), "creds", "--username", "operator"]) == 0
    out = capsys.readouterr().out
    assert "operator" in out
    password = out.split("password:")[1].splitlines()[0].strip()

    cfg = load_config(tmp_path)
    auth = load_auth(tmp_path, cfg, provision=False)
    assert auth.username == "operator"
    assert auth.check("operator", password) is True
    assert auth.generated_password == ""