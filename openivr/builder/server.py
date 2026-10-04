"""FastAPI application: the web IVR builder.

Server-side rendered forms (Jinja2 + Tailwind CSS CDN) with a 7-step wizard:
   1. Trunk: SIP trunk configuration (providers, credentials, match IPs)
   2. Endpoints: softphone and IAX user accounts
   3. Extensions: dialplan extensions (single, linear, ringall)
   4. Dialplan: IVR menus, prompts, DTMF options
   5. SMTP: optional mail delivery and alerts
   6. Permissions & Voicemail: filesystem grants, recordings, voicemail
   7. Finish & Publish: review, finalize JSON, build confs and reload Asterisk

Every page needs a login (bcrypt password in config.yaml, signed session
cookie). ``OPENIVR_BUILDER_TOKEN`` stays supported for scripts and tests, and a
builder without credentials falls back to localhost-only access.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import anyio
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, SmtpCfg, load_config, set_config_value
from ..media import (
    AUDIO_SUFFIXES,
    CONVERTIBLE_SUFFIXES,
    TARGET_SUFFIX,
    available_sounds,
    check_prompt_name,
    convert_audio,
    ffmpeg_binary,
    list_sound_files,
    needs_conversion,
)
from ..notify import Notifier
from .auth import (
    LOGIN_PATH,
    MIN_PASSWORD_LENGTH,
    SESSION_COOKIE,
    UNPROTECTED_PATHS,
    AuthError,
    BuilderAuth,
    load_auth,
    save_credentials,
)
from .providers import catalog_public, countries, provider_by_id, providers
from .publish import publish
from .session import (
    DIALPLAN_ACTIONS,
    DTMF_KEYS,
    VOICEMAIL_FORMATS,
    STEP_LABELS,
    STEP_PATHS,
    STEPS,
    first_incomplete_step,
    load_endpoints,
    load_extensions,
    load_ivr,
    load_permissions,
    load_progress,
    load_trunk,
    mark_step,
    save_endpoints,
    save_extensions,
    save_ivr,
    save_permissions,
    save_progress,
    save_trunk,
    snake_case_stem,
    validate_endpoints,
    validate_extensions,
    validate_ivr_document,
    validate_permissions,
    validate_trunk,
)
from .session import load_smtp as read_smtp
from .session import save_smtp as write_smtp

log = logging.getLogger("openivr.builder")

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def default_root() -> Path:
    return Path(os.environ.get("OPENIVR_ROOT") or Path(__file__).resolve().parents[2])


# --------------------------------------------------------------------- helpers


def prompts_of(cfg: Config) -> list[str]:
    return sorted(available_sounds(cfg.sounds_dir))


def stepper_context(root: Path) -> dict[str, Any]:
    progress = load_progress(root)
    return {
        "steps": STEPS,
        "step_paths": STEP_PATHS,
        "step_labels": STEP_LABELS,
        "current_step": progress.get("current_step", "trunk"),
        "completed": progress.get("completed", []),
        "finished": progress.get("finished", False),
        "published": progress.get("published", False),
        "publish_needed": progress.get("publish_needed", False),
    }


def all_validation_errors(root: Path) -> list[str]:
    errors: list[str] = []
    trunk = load_trunk(root)
    provider = provider_by_id(trunk.get("provider_id", ""))
    errors.extend(validate_trunk(trunk, provider))

    endpoints = load_endpoints(root)
    errors.extend(validate_endpoints(endpoints))

    extensions = load_extensions(root)
    errors.extend(validate_extensions(extensions, endpoints))

    ivr = load_ivr(root)
    ext_numbers = [str(e.get("number", "")) for e in extensions]
    errors.extend(validate_ivr_document(ivr, ext_numbers))

    # permissions/recording/voicemail step
    errors.extend(validate_permissions(load_permissions(root)))
    return errors


# ------------------------------------------------------------------- security


LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOCAL_HOSTS


def token_ok(request: Request) -> bool:
    """OPENIVR_BUILDER_TOKEN keeps working for scripts and tests."""
    token = os.environ.get("OPENIVR_BUILDER_TOKEN", "")
    if not token:
        return False
    supplied = request.query_params.get("token") or request.headers.get("x-openivr-token")
    return secrets.compare_digest(str(supplied or ""), token)


def session_user(request: Request, auth: BuilderAuth) -> str | None:
    if token_ok(request):
        return auth.username or "token"
    return auth.read(request.cookies.get(SESSION_COOKIE))


def state_auth(request: Request) -> BuilderAuth | None:
    """The app's auth state, provisioned on first use (None outside the app)."""
    app = getattr(request, "app", None)
    root = getattr(getattr(app, "state", None), "root", None)
    if app is None or root is None:
        return None
    return get_auth(app, Path(root))


def authorised(request: Request, auth: BuilderAuth | None = None) -> bool:
    """True when the request may drive the builder."""
    if token_ok(request):
        return True
    state = auth or state_auth(request)
    if state is None:
        return is_local(request)
    if session_user(request, state):
        return True
    # No credentials configured at all: keep the historical localhost behaviour.
    return state.local_only and is_local(request)


def guard(request: Request) -> JSONResponse | None:
    """Block writes that are neither from a session nor from localhost."""
    if is_local(request) or authorised(request):
        return None
    return JSONResponse({"error": "forbidden: builder POSTs must come from localhost"}, 403)


def auth_of(request: Request) -> BuilderAuth:
    return get_auth(request.app, request.app.state.root)


def get_auth(app: FastAPI, root: Path) -> BuilderAuth:
    """Auth state for this builder process, provisioned on first use.

    Provisioning only happens here (never at import time) so simply importing
    the module cannot create credentials as a side effect.
    """
    auth = getattr(app.state, "auth", None)
    if auth is None:
        auth = load_auth(root, load_config(root, bootstrap=True))
        app.state.auth = auth
    return auth


# ---------------------------------------------------------------- application


def create_app(root: str | Path | None = None) -> FastAPI:
    root_path = Path(root) if root else default_root()

    app = FastAPI(title="openivr builder", docs_url=None, redoc_url=None)
    app.state.root = root_path
    app.state.keep_open = False
    app.state.server = None
    app.state.auth = None
    # No system.json => the builder runs standalone (no Asterisk on this box).
    app.state.standalone = not (root_path / "system.json").is_file()
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        """Every builder page needs a session (or the API token)."""
        path = request.url.path
        if path in UNPROTECTED_PATHS or path.startswith("/static"):
            return await call_next(request)
        if authorised(request):
            return await call_next(request)
        if path.startswith("/api"):
            return JSONResponse({"error": "unauthorized: log in first"}, 401)
        target = path if request.method == "GET" else "/"
        return RedirectResponse(f"{LOGIN_PATH}?next={quote(target)}", status_code=303)

    def render(
        request: Request, name: str, ctx: dict[str, Any] | None = None, status: int = 200
    ) -> HTMLResponse:
        cfg = load_config(root_path)
        auth = get_auth(app, root_path)
        base: dict[str, Any] = {
            "request": request,
            "root": root_path,
            "config": cfg,
            "sounds": prompts_of(cfg),
            # every template can rely on these; load once per render
            "permissions": load_permissions(root_path),
            "keep_open": app.state.keep_open,
            "standalone": app.state.standalone,
            "auth": auth,
            "current_user": session_user(request, auth),
            "auth_required": not auth.local_only,
            **stepper_context(root_path),
        }
        base.update(ctx or {})
        return templates.TemplateResponse(
            request=request, name=name, context=base, status_code=status
        )

    # ------------------------------------------------------------- Login page

    @app.get(LOGIN_PATH, response_class=HTMLResponse)
    async def login_page(request: Request) -> Any:
        auth = get_auth(app, root_path)
        if auth.local_only:
            return RedirectResponse(STEP_PATHS.get("trunk", "/trunk"), status_code=303)
        return render(request, "login.html", {"error": None})

    @app.post(LOGIN_PATH)
    async def login_submit(request: Request, username: str = Form(""), password: str = Form("")) -> Any:
        auth = get_auth(app, root_path)
        target = request.query_params.get("next") or request.cookies.get("openivr_next") or "/"
        if not auth.check(username, password):
            log.warning("failed builder login for %r", username)
            return render(
                request,
                "login.html",
                {"error": "Wrong username or password", "username": username, "next": target},
                status=401,
            )
        log.info("builder login ok for %r", username)
        response = RedirectResponse(
            target if str(target).startswith("/") else "/", status_code=303
        )
        response.set_cookie(
            SESSION_COOKIE,
            auth.issue(username),
            max_age=auth.session_ttl,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/logout")
    @app.post("/logout")
    async def logout() -> RedirectResponse:
        response = RedirectResponse(LOGIN_PATH, status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # ------------------------------------------------------------- Settings

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Any:
        auth = auth_of(request)
        return render(
            request,
            "settings.html",
            {"message": None, "error": None, "min_password_length": MIN_PASSWORD_LENGTH},
        )

    @app.post("/settings")
    async def settings_save(
        request: Request,
        username: str = Form(""),
        current_password: str = Form(""),
        new_password: str = Form(""),
        new_password_confirm: str = Form(""),
    ) -> Any:
        auth = auth_of(request)
        denied = guard(request)
        if denied:
            return denied
        # Verify against the *stored* username: changing the username field
        # must not be able to skip the current-password check.
        if auth.configured and not auth.check(auth.username, current_password):
            return render(
                request,
                "settings.html",
                {
                    "error": "Current password does not match",
                    "message": None,
                    "username": username,
                    "min_password_length": MIN_PASSWORD_LENGTH,
                },
                status=400,
            )
        if not new_password:
            new_password_confirm = new_password_confirm or new_password
        if new_password and new_password != new_password_confirm:
            return render(
                request,
                "settings.html",
                {
                    "error": "The new passwords do not match",
                    "message": None,
                    "username": username,
                    "min_password_length": MIN_PASSWORD_LENGTH,
                },
                status=400,
            )
        if not username.strip():
            return render(
                request,
                "settings.html",
                {
                    "error": "Username must not be empty",
                    "message": None,
                    "username": auth.username,
                    "min_password_length": MIN_PASSWORD_LENGTH,
                },
                status=400,
            )
        try:
            save_credentials(
                root_path,
                auth,
                username=username,
                password=new_password or None,
            )
        except AuthError as exc:
            return render(
                request,
                "settings.html",
                {
                    "error": str(exc),
                    "message": None,
                    "username": username,
                    "min_password_length": MIN_PASSWORD_LENGTH,
                },
                status=400,
            )
        response = RedirectResponse("/settings?ok=Credentials+updated", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            auth.issue(username),
            max_age=auth.session_ttl,
            httponly=True,
            samesite="lax",
            path="/",
        )
        return response

    # ----------------------------------------------------------- Root router

    @app.get("/")
    async def index() -> RedirectResponse:
        step = first_incomplete_step(root_path)
        target = STEP_PATHS.get(step, "/trunk")
        return RedirectResponse(target, status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        """Overview of the builder state (also the landing page after login)."""
        cfg = load_config(root_path)
        ivr = load_ivr(root_path)
        return render(
            request,
            "index.html",
            {
                "menus": sorted((ivr.get("menus") or {}).keys()),
                "files": list_sound_files(cfg.sounds_dir),
                "smtp": read_smtp(root_path),
                "voicemail": cfg.voicemail.enabled,
                "permissions": load_permissions(root_path),
                "errors": all_validation_errors(root_path),
            },
        )

    # ---------------------------------------------------------- Step 1: Trunk

    @app.get("/trunk", response_class=HTMLResponse)
    async def trunk_page(request: Request) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "trunk"
        save_progress(root_path, progress)

        trunk = load_trunk(root_path)
        pid = trunk.get("provider_id", "")
        provider = provider_by_id(pid) if pid else None
        errors = validate_trunk(trunk, provider) if pid else []

        return render(
            request,
            "trunk.html",
            {
                "countries": countries(),
                "providers": providers(),
                "catalog": catalog_public(),
                "trunk": trunk,
                "errors": errors,
            },
        )

    @app.post("/trunk")
    async def trunk_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()

        country = str(form.get("country") or "").strip()
        provider_id = str(form.get("provider_id") or "").strip()
        host_fqdn = str(form.get("host_fqdn") or "").strip()
        transport = str(form.get("transport") or "transport-udp").strip()
        signaling_port = str(form.get("signaling_port") or "5060").strip()
        auth_type = str(form.get("auth_type") or "").strip()
        match_raw = str(form.get("match_ips") or "").strip()
        match_ips = [line.strip() for line in match_raw.splitlines() if line.strip()]

        fields: dict[str, Any] = {}
        for key, value in form.items():
            if key.startswith("field__"):
                field_id = key.split("__", 1)[1]
                fields[field_id] = str(value).strip()

        provider = provider_by_id(provider_id)
        if not host_fqdn and provider:
            host_fqdn = provider.get("host_fqdn", "")
        if not auth_type and provider:
            auth_type = provider.get("auth_type", "registration")
        if not match_ips and provider:
            match_ips = list(provider.get("match") or [])
        if not signaling_port and provider:
            signaling_port = str(provider.get("signaling_port") or "5060")

        # Auto-complete registration_uri if username and host_fqdn are supplied
        if auth_type == "registration" and not fields.get("registration_uri"):
            uname = fields.get("username")
            if uname and host_fqdn:
                fields["registration_uri"] = f"sip:{uname}@{host_fqdn}"

        trunk_data: dict[str, Any] = {
            "country": country,
            "provider_id": provider_id,
            "name": provider.get("name", provider_id) if provider else provider_id,
            "auth_type": auth_type or "registration",
            "host_fqdn": host_fqdn,
            "transport": transport,
            "signaling_port": signaling_port,
            "match": match_ips,
            "direction": (provider or {}).get("direction", ""),
            "fields": fields,
        }

        errors = validate_trunk(trunk_data, provider)
        if errors:
            return render(
                request,
                "trunk.html",
                {
                    "countries": countries(),
                    "providers": providers(),
                    "catalog": catalog_public(),
                    "trunk": trunk_data,
                    "errors": errors,
                },
                status=400,
            )

        save_trunk(root_path, trunk_data)
        return RedirectResponse("/endpoints?ok=Trunk+configuration+saved", status_code=303)

    # ------------------------------------------------------- Step 2: Endpoints

    @app.get("/endpoints", response_class=HTMLResponse)
    async def endpoints_page(request: Request) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "endpoints"
        save_progress(root_path, progress)

        endpoints = load_endpoints(root_path)
        return render(
            request,
            "endpoints.html",
            {
                "endpoints": endpoints,
                "errors": [],
            },
        )

    @app.post("/endpoints")
    async def endpoints_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()

        usernames = form.getlist("username")
        passwords = form.getlist("password")
        display_names = form.getlist("display_name")
        protocols = form.getlist("protocol")

        endpoints: list[dict[str, Any]] = []
        for i in range(len(usernames)):
            u = str(usernames[i]).strip()
            if not u:
                continue
            p = str(passwords[i]).strip() if i < len(passwords) else ""
            d = str(display_names[i]).strip() if i < len(display_names) else ""
            proto = str(protocols[i]).strip().lower() if i < len(protocols) else "pjsip"
            endpoints.append(
                {
                    "username": u,
                    "password": p,
                    "display_name": d or u,
                    "protocol": proto or "pjsip",
                }
            )

        errors = validate_endpoints(endpoints)
        if errors:
            return render(
                request,
                "endpoints.html",
                {
                    "endpoints": endpoints,
                    "errors": errors,
                },
                status=400,
            )

        save_endpoints(root_path, endpoints)
        return RedirectResponse("/extensions?ok=Endpoints+saved", status_code=303)

    # ------------------------------------------------------ Step 3: Extensions

    @app.get("/extensions", response_class=HTMLResponse)
    async def extensions_page(request: Request) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "extensions"
        save_progress(root_path, progress)

        extensions = load_extensions(root_path)
        endpoints = load_endpoints(root_path)
        return render(
            request,
            "extensions.html",
            {
                "extensions": extensions,
                "endpoints": endpoints,
                "errors": [],
            },
        )

    @app.post("/extensions")
    async def extensions_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()

        # Parse indexed fields ext_number_X, ext_strategy_X, ext_users_X
        indices: set[str] = set()
        for key in form.keys():
            if key.startswith("ext_number_"):
                idx = key.split("_", 2)[2]
                indices.add(idx)

        extensions: list[dict[str, Any]] = []
        if indices:
            sorted_indices = sorted(indices, key=lambda x: int(x) if x.isdigit() else x)
            for idx in sorted_indices:
                num = str(form.get(f"ext_number_{idx}", "")).strip()
                if not num:
                    continue
                strategy = str(form.get(f"ext_strategy_{idx}", "single")).strip()
                users_raw = str(form.get(f"ext_users_{idx}", "")).strip()
                users = [u.strip() for u in users_raw.split(",") if u.strip()]
                extensions.append(
                    {
                        "number": num,
                        "strategy": strategy,
                        "users": users,
                    }
                )
        else:
            numbers = form.getlist("ext_number")
            strategies = form.getlist("ext_strategy")
            users_list = form.getlist("ext_users")
            for i, num_val in enumerate(numbers):
                num = str(num_val).strip()
                if not num:
                    continue
                strat = str(strategies[i]).strip() if i < len(strategies) else "single"
                raw = str(users_list[i]).strip() if i < len(users_list) else ""
                u = [x.strip() for x in raw.split(",") if x.strip()]
                extensions.append({"number": num, "strategy": strat, "users": u})

        endpoints = load_endpoints(root_path)
        errors = validate_extensions(extensions, endpoints)
        if errors:
            return render(
                request,
                "extensions.html",
                {
                    "extensions": extensions,
                    "endpoints": endpoints,
                    "errors": errors,
                },
                status=400,
            )

        save_extensions(root_path, extensions)
        return RedirectResponse("/dialplan?ok=Extensions+saved", status_code=303)

    # -------------------------------------------------------- Step 4: Dialplan

    @app.get("/dialplan", response_class=HTMLResponse)
    async def dialplan_page(request: Request) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "dialplan"
        save_progress(root_path, progress)

        ivr = load_ivr(root_path)
        extensions = load_extensions(root_path)
        menu_names = list((ivr.get("menus") or {}).keys())
        if not menu_names:
            ivr.setdefault("menus", {"main": {"menu_prompt": "", "dtmf_options": {}}})
            menu_names = ["main"]

        return render(
            request,
            "dialplan.html",
            {
                "ivr": ivr,
                "extensions": extensions,
                "menu_names": menu_names,
                "dtmf_keys": DTMF_KEYS,
                "actions": DIALPLAN_ACTIONS,
                "errors": [],
                "sound_files": list_sound_files(load_config(root_path).sounds_dir),
                "ffmpeg_available": bool(ffmpeg_binary()),
                "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
            },
        )

    @app.post("/dialplan")
    async def dialplan_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()

        welcome_prompt = str(form.get("welcome_prompt") or "").strip()
        invalid_prompt = str(form.get("invalid_prompt") or "").strip()
        timeout_prompt = str(form.get("timeout_prompt") or "").strip()
        promotion_prompt = str(form.get("promotion_prompt") or "").strip()
        hold_music = str(form.get("hold_music") or "").strip()
        start_menu = str(form.get("start_menu") or "main").strip()

        try:
            timeout_seconds = float(form.get("timeout_seconds") or 15.0)
        except ValueError:
            timeout_seconds = 15.0

        try:
            invalid_retries = int(form.get("invalid_retries") or 3)
        except ValueError:
            invalid_retries = 3

        try:
            timeout_retries = int(form.get("timeout_retries") or 3)
        except ValueError:
            timeout_retries = 3

        try:
            repeat_max = int(form.get("repeat_max") or 3)
        except ValueError:
            repeat_max = 3

        existing_ivr = load_ivr(root_path)
        menu_names = [
            k.split("__", 1)[1] for k in form.keys() if k.startswith("menu_exists__")
        ] or list((existing_ivr.get("menus") or {}).keys())
        if not menu_names:
            menu_names = ["main"]

        menus: dict[str, Any] = {}
        duplicate_dtmf_errors: list[str] = []
        for mname in menu_names:
            mprompt = str(form.get(f"menu_prompt__{mname}") or "").strip()
            dtmf_options: dict[str, Any] = {}
            row_indexes = sorted(
                {
                    k.split("__", 2)[1]
                    for k in form.keys()
                    if k.startswith("dtmf_key__") and k.endswith(f"__{mname}")
                },
                key=lambda v: int(v) if v.isdigit() else 0,
            )
            for idx in row_indexes:
                dkey = str(form.get(f"dtmf_key__{idx}__{mname}") or "").strip()
                if not dkey or dkey not in DTMF_KEYS:
                    continue
                if dkey in dtmf_options:
                    duplicate_dtmf_errors.append(
                        f"Menu {mname!r}: DTMF key {dkey!r} is used more than once"
                    )
                    continue
                action = str(form.get(f"dtmf_action__{idx}__{mname}") or "hangup").strip()
                desc = str(form.get(f"dtmf_description__{idx}__{mname}") or "").strip()
                opt: dict[str, Any] = {"description": desc, "action": action}
                if action == "dial":
                    ep = str(form.get(f"dtmf_endpoint__{idx}__{mname}") or "").strip()
                    # Per plan, no pjsip/ prefix
                    ep = ep.replace("PJSIP/", "").replace("pjsip/", "")
                    opt["endpoint"] = ep
                elif action in {"submenu", "parent"}:
                    opt["target"] = (
                        form.get(f"dtmf_target__{idx}__{mname}") or ""
                    ).strip()
                elif action == "voicemail":
                    opt["mailbox"] = (
                        str(form.get(f"dtmf_mailbox__{idx}__{mname}") or "").strip()
                        or "default"
                    )
                dtmf_options[dkey] = opt
            menus[mname] = {
                "menu_prompt": mprompt,
                "dtmf_options": dtmf_options,
            }

        ivr_data: dict[str, Any] = {
            "Version": "0.1.0",
            "welcome_prompt": {
                "path": "welcome",
                "prompt": welcome_prompt,
            },
            "promotion_prompt": {
                "path": "",
                "prompt": promotion_prompt,
            },
            "invalid": {
                "prompt": invalid_prompt,
                "repeat_prompt": True,
                "max_retries": invalid_retries,
                "fail_action": "hangup",
            },
            "timeout": {
                "seconds": timeout_seconds,
                "prompt": timeout_prompt,
                "repeat_prompt": True,
                "max_retries": timeout_retries,
                "fail_action": "hangup",
            },
            "repeat": {
                "max_attempts": repeat_max,
                "fallback": "hangup",
            },
            "hold_music": {
                "prompt": hold_music,
            },
            "start_menu": start_menu if start_menu in menus else next(iter(menus), "main"),
            "menus": menus,
        }

        extensions = load_extensions(root_path)
        ext_numbers = [str(e.get("number", "")) for e in extensions]
        errors = validate_ivr_document(ivr_data, ext_numbers) + duplicate_dtmf_errors
        if errors:
            return render(
                request,
                "dialplan.html",
                {
                    "ivr": ivr_data,
                    "extensions": extensions,
                    "menu_names": list(menus.keys()),
                    "dtmf_keys": DTMF_KEYS,
                    "actions": DIALPLAN_ACTIONS,
                    "errors": errors,
                },
                status=400,
            )

        save_ivr(root_path, ivr_data)

        # step order: dialplan -> permissions -> smtp -> finish
        return RedirectResponse("/permissions?ok=Dialplan+configuration+saved", status_code=303)

    @app.post("/dialplan/menu/add")
    async def menu_add(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = str(form.get("menu_name") or "").strip().lower()
        prompt = str(form.get("menu_prompt_new") or "").strip()
        if not name:
            return RedirectResponse("/dialplan", status_code=303)

        ivr = load_ivr(root_path)
        ivr.setdefault("menus", {})
        if name not in ivr["menus"]:
            ivr["menus"][name] = {"menu_prompt": prompt, "dtmf_options": {}}
            save_ivr(root_path, ivr)
        return RedirectResponse("/dialplan", status_code=303)

    @app.post("/dialplan/menu/delete")
    async def menu_delete(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = str(form.get("menu_name") or "").strip()
        ivr = load_ivr(root_path)
        menus = ivr.get("menus", {})
        if len(menus) > 1 and name in menus:
            del menus[name]
            if ivr.get("start_menu") == name:
                ivr["start_menu"] = next(iter(menus))
            save_ivr(root_path, ivr)
        return RedirectResponse("/dialplan", status_code=303)

    # -------------------------------------------- Step 5: Permissions & Voicemail

    @app.get("/permissions", response_class=HTMLResponse)
    async def permissions_page(request: Request) -> Any:
        progress = load_progress(root_path)
        progress["current_step"] = "permissions"
        save_progress(root_path, progress)

        values = load_permissions(root_path)
        cfg = load_config(root_path)
        return render(
            request,
            "permissions.html",
            {
                "values": values,
                "errors": validate_permissions(values),
                "voicemail_formats": sorted(VOICEMAIL_FORMATS),
                "voicemail_dir": str(cfg.voicemail_dir),
                "recordings_dir": str(cfg.recordings_dir),
            },
        )

    @app.post("/permissions")
    async def permissions_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()

        def _on(name: str) -> bool:
            return str(form.get(name) or "").lower() in {"1", "on", "true", "yes", "checked"}

        def _int(name: str, fallback: int) -> int:
            try:
                return int(str(form.get(name) or fallback).strip())
            except (TypeError, ValueError):
                return fallback

        values = {
            "voicemail_enabled": _on("voicemail_enabled"),
            "voicemail_dir": str(form.get("voicemail_dir") or "").strip()
            or "data/recordings/voicemail",
            "voicemail_format": str(form.get("voicemail_format") or "wav").lower(),
            "voicemail_max_duration": _int("voicemail_max_duration", 120),
            "recording_enabled": _on("recording_enabled"),
            "recording_dir": str(form.get("recording_dir") or "").strip()
            or "/var/spool/asterisk/monitor",
            "recording_retention_days": _int("recording_retention_days", 30),
        }
        errors = validate_permissions(values)
        cfg = load_config(root_path)
        for key, value in (
            ("voicemail.enabled", values["voicemail_enabled"]),
            ("voicemail.dir", values["voicemail_dir"]),
            ("voicemail.format", values["voicemail_format"]),
            ("voicemail.max_duration", values["voicemail_max_duration"]),
            ("record.enabled", values["recording_enabled"]),
            ("record.mixmonitor_dir", values["recording_dir"]),
            ("record.retention_days", values["recording_retention_days"]),
        ):
            try:
                set_config_value(root_path, key, value)
            except Exception as exc:  # noqa: BLE001 - keep the form usable
                log.error("could not store %s: %s", key, exc)
                errors.append(f"could not save {key} into config.yaml: {exc}")

        if values["voicemail_enabled"]:
            cfg.voicemail_dir.mkdir(parents=True, exist_ok=True)
        if values["recording_enabled"] and not Path(values["recording_dir"]).exists():
            info = f"Recording directory {values['recording_dir']} does not exist yet"
            log.warning(info)

        save_permissions(root_path, values)
        if errors:
            return render(
                request,
                "permissions.html",
                {
                    "values": values,
                    "errors": errors,
                    "voicemail_formats": sorted(VOICEMAIL_FORMATS),
                    "voicemail_dir": str(cfg.voicemail_dir),
                    "recordings_dir": str(cfg.recordings_dir),
                },
                status=400,
            )
        progress = load_progress(root_path)
        progress["current_step"] = "smtp"
        progress["permissions_done"] = True
        save_progress(root_path, progress)
        return RedirectResponse("/smtp?ok=Permissions+saved", status_code=303)

    # ------------------------------------------------------------ Step 6: SMTP

    @app.get("/smtp", response_class=HTMLResponse)
    async def smtp_page(
        request: Request, ok: str | None = None, err: str | None = None
    ) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "smtp"
        save_progress(root_path, progress)

        cfg = load_config(root_path)
        current = dict(read_smtp(root_path))
        current.setdefault("enabled", bool(cfg.smtp.enabled))
        current.setdefault("host", cfg.smtp.host)
        current.setdefault("port", cfg.smtp.port)
        current.setdefault("starttls", cfg.smtp.starttls)
        current.setdefault("username", cfg.smtp.username)
        current.setdefault("from_addr", cfg.smtp.from_addr)
        current.setdefault("alerts_to", ", ".join(cfg.smtp.alerts_to))

        return render(
            request,
            "smtp.html",
            {
                "current": current,
                "ok": ok,
                "err": err,
            },
        )

    @app.post("/smtp")
    async def smtp_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        smtp = {
            "enabled": bool(form.get("enabled")),
            "host": str(form.get("host") or "").strip(),
            "port": int(form.get("port") or 587),
            "starttls": bool(form.get("starttls")),
            "username": str(form.get("username") or "").strip(),
            "password": str(form.get("password") or ""),
            "from_addr": str(form.get("from_addr") or "").strip(),
            "alerts_to": [
                r.strip()
                for r in str(form.get("alerts_to") or "").replace(";", ",").split(",")
                if r.strip()
            ],
        }

        if smtp["enabled"] and not smtp["host"]:
            return render(
                request,
                "smtp.html",
                {"current": smtp, "err": "SMTP host is required when SMTP is enabled"},
                status=400,
            )

        write_smtp(root_path, smtp)
        mark_step(root_path, "smtp")

        if bool(form.get("test")):
            cfg = load_config(root_path)
            merged = {**asdict(cfg.smtp), **smtp}
            ok, detail = await anyio.to_thread.run_sync(Notifier(SmtpCfg(**merged)).test)
            target = f"/smtp?ok=Connection {'test succeeded' if ok else 'FAILED'}: {detail}"
            return RedirectResponse(target, status_code=303)

        return RedirectResponse("/finish?ok=SMTP+settings+saved", status_code=303)

    # -------------------------------------------------- Step 6: Finish & Build

    @app.get("/finish", response_class=HTMLResponse)
    async def finish_page(request: Request) -> HTMLResponse:
        progress = load_progress(root_path)
        progress["current_step"] = "finish"
        save_progress(root_path, progress)

        trunk = load_trunk(root_path)
        endpoints = load_endpoints(root_path)
        extensions = load_extensions(root_path)
        ivr = load_ivr(root_path)
        smtp = read_smtp(root_path)
        errors = all_validation_errors(root_path)

        return render(
            request,
            "finish.html",
            {
                "trunk": trunk,
                "endpoints": endpoints,
                "extensions": extensions,
                "ivr": ivr,
                "smtp": smtp,
                "errors": errors,
                "finished": progress.get("finished", False),
                "published": progress.get("published", False),
                "publish_needed": progress.get("publish_needed", False),
            },
        )

    @app.post("/finish")
    async def finish_action(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied

        errors = all_validation_errors(root_path)
        if errors:
            trunk = load_trunk(root_path)
            endpoints = load_endpoints(root_path)
            extensions = load_extensions(root_path)
            ivr = load_ivr(root_path)
            smtp = read_smtp(root_path)
            return render(
                request,
                "finish.html",
                {
                    "trunk": trunk,
                    "endpoints": endpoints,
                    "extensions": extensions,
                    "ivr": ivr,
                    "smtp": smtp,
                    "errors": errors,
                    "finished": False,
                },
                status=400,
            )

        progress = load_progress(root_path)
        progress["finished"] = True
        progress["publish_needed"] = True
        done = set(progress.get("completed") or [])
        done.add("finish")
        progress["completed"] = sorted(done, key=lambda s: STEPS.index(s) if s in STEPS else 99)
        save_progress(root_path, progress)

        return RedirectResponse(
            "/finish?ok=JSON+configuration+finalized.+Now+click+Build+&+Deploy+to+activate.",
            status_code=303,
        )

    @app.post("/publish")
    async def publish_action(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied

        errors = all_validation_errors(root_path)
        if errors:
            trunk = load_trunk(root_path)
            endpoints = load_endpoints(root_path)
            extensions = load_extensions(root_path)
            ivr = load_ivr(root_path)
            smtp = read_smtp(root_path)
            return render(
                request,
                "finish.html",
                {
                    "trunk": trunk,
                    "endpoints": endpoints,
                    "extensions": extensions,
                    "ivr": ivr,
                    "smtp": smtp,
                    "errors": errors,
                    "finished": False,
                },
                status=400,
            )

        result = publish(root_path, standalone=app.state.standalone)
        progress = load_progress(root_path)
        if result.get("copy_error"):
            progress["published"] = False
            progress["publish_needed"] = True
            save_progress(root_path, progress)
            if "application/json" in request.headers.get("accept", ""):
                return JSONResponse(result, status_code=207)
            return render(
                request,
                "done.html",
                {
                    "message": "Configuration was generated but could not be copied to Asterisk.",
                    "publish_info": result,
                },
                status=207,
            )

        progress["published"] = True
        progress["publish_needed"] = False
        save_progress(root_path, progress)

        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(result)

        msg = f"Generated {len(result['files'])} Asterisk configuration files."
        return render(
            request,
            "done.html",
            {
                "message": msg,
                "publish_info": result,
            },
        )

    @app.get("/done", response_class=HTMLResponse)
    async def done_page(request: Request) -> HTMLResponse:
        return render(
            request,
            "done.html",
            {
                "message": "Asterisk PBX and IVR configuration successfully applied.",
                "publish_info": None,
            },
        )

# ------------------------------------------------------- Prompts (media)

    @app.post("/dialplan/upload")
    async def media_upload(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
        wants_json = "application/json" in request.headers.get("accept", "")

        def _fail(msg: str) -> Any:
            if wants_json:
                return JSONResponse({"ok": False, "error": msg}, status_code=400)
            return RedirectResponse(f"/dialplan?err={msg}", status_code=303)

        if upload is None or not upload.filename:
            return _fail("Choose an audio file first")

        original = Path(upload.filename).name
        suffix = Path(original).suffix.lower()
        if suffix not in AUDIO_SUFFIXES and suffix not in CONVERTIBLE_SUFFIXES:
            return _fail(
                f"Unsupported audio format {suffix or 'none'} "
                f"(use {', '.join(sorted(AUDIO_SUFFIXES | CONVERTIBLE_SUFFIXES))})"
            )

        # Plan specifies snake_case naming for audio uploads
        stem = snake_case_stem(original)
        convert = needs_conversion(suffix)
        # Converted uploads always land as .wav (Asterisk's most portable
        # format); files Asterisk already understands keep their own suffix.
        name = f"{stem}{TARGET_SUFFIX if convert else suffix}"
        problem = check_prompt_name(name)
        if problem:
            return _fail(problem)

        cfg = load_config(root_path)
        cfg.sounds_dir.mkdir(parents=True, exist_ok=True)
        target = cfg.sounds_dir / name
        tmp = cfg.sounds_dir / f".upload-{name}"

        size = 0
        try:
            with tmp.open("wb") as fh:
                while chunk := await upload.read(1 << 16):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError(f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    fh.write(chunk)
            if size == 0:
                raise ValueError("Uploaded file is empty")
        except ValueError as exc:
            tmp.unlink(missing_ok=True)
            return _fail(str(exc))
        finally:
            await upload.close()

        note = ""
        if convert:
            source = cfg.sounds_dir / f".source-{stem}{suffix}"
            tmp.rename(source)
            message = convert_audio(source, stem, cfg.sounds_dir)
            source.unlink(missing_ok=True)
            if not target.is_file():
                return _fail(message)
            note = f"stored {name} ({message})"
        else:
            tmp.replace(target)
            note = f"stored {name}"

        log.info("Builder saved prompt %s (%d bytes, %s)", name, size, note)
        prompt = Path(name).stem if name.endswith(TARGET_SUFFIX) else name
        if wants_json:
            return JSONResponse({"ok": True, "name": name, "prompt": prompt, "size": size})
        return RedirectResponse(f"/dialplan?ok={note.replace(' ', '+')}", status_code=303)

    @app.post("/dialplan/delete")
    async def media_delete(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = Path(str(form.get("name") or "")).name
        cfg = load_config(root_path)
        if not name:
            return RedirectResponse("/dialplan", status_code=303)

        for candidate in (cfg.sounds_dir / name, cfg.sounds_dir / f"{name}.wav"):
            if candidate.is_file():
                candidate.unlink()
                log.info("Builder deleted prompt %s", candidate.name)
                return RedirectResponse(f"/dialplan?ok=Deleted+{candidate.name}", status_code=303)
        return RedirectResponse("/dialplan?err=File+not+found", status_code=303)

    # ------------------------------------------------------------- API routes

    @app.get("/api/catalog")
    async def api_catalog() -> JSONResponse:
        return JSONResponse(catalog_public())

    @app.get("/api/providers")
    async def api_providers(country: str | None = None) -> JSONResponse:
        return JSONResponse(providers(country=country))

    @app.get("/api/provider/{provider_id}")
    async def api_provider(provider_id: str) -> JSONResponse:
        p = provider_by_id(provider_id)
        if not p:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(p)

    @app.get("/status")
    async def status() -> JSONResponse:
        cfg = load_config(root_path)
        progress = load_progress(root_path)
        trunk = load_trunk(root_path)
        endpoints = load_endpoints(root_path)
        extensions = load_extensions(root_path)
        ivr = load_ivr(root_path)
        return JSONResponse(
            {
                "root": str(root_path),
                "progress": progress,
                "trunk": trunk.get("provider_id"),
                "endpoints_count": len(endpoints),
                "extensions_count": len(extensions),
                "menus": list((ivr.get("menus") or {}).keys()),
                "prompts_count": len(list_sound_files(cfg.sounds_dir)),
                "validation_errors": all_validation_errors(root_path),
                "smtp_configured": bool(read_smtp(root_path)),
            }
        )

    @app.post("/shutdown")
    async def shutdown(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        server = app.state.server
        if server is not None:
            loop = asyncio.get_running_loop()
            loop.create_task(_stop_server(server))
        return render(request, "done.html", {"message": "Builder stopped on request"})

    return app


async def _stop_server(server) -> None:
    await anyio.sleep(0.8)
    server.should_exit = True


# ----------------------------------------------------------------------- serve


def serve(
    host: str = "127.0.0.1",
    port: int = 8090,
    root: str | Path | None = None,
    keep_open: bool = False,
    standalone: bool | None = None,
) -> None:
    """Run the builder with uvicorn (blocking)."""
    import uvicorn

    application = create_app(root)
    application.state.keep_open = keep_open
    if standalone is not None:
        application.state.standalone = standalone
    server = uvicorn.Server(
        uvicorn.Config(application, host=host, port=port, log_level="info", access_log=False)
    )
    application.state.server = server
    server.run()


app = create_app()
