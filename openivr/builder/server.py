"""FastAPI application: the web IVR builder.

Server-side rendered forms (Jinja2), a media uploader for prompts, structural
validation of the flow, and - per ``plan.md`` - a final SMTP step before the
builder shuts itself down so ``run_ivr.sh`` can start the IVR service.

The builder binds to 127.0.0.1 by default; POST requests are only accepted from
loopback clients unless ``OPENIVR_BUILDER_TOKEN`` is set, in which case they
must carry that token.
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

import anyio
from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..config import Config, SmtpCfg, load_config
from ..flow import ACTIONS, Flow, FlowError, menu_tree, validate
from ..media import AUDIO_SUFFIXES, available_sounds, check_prompt_name, list_sound_files
from ..notify import Notifier
from .defaults import DIGIT_SLOTS, default_flow

log = logging.getLogger("openivr.builder")

TEMPLATE_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MENU_FIELDS = (
    "label",
    "endpoint",
    "target",
    "mailbox",
    "greeting",
    "variable",
    "prompt",
    "business_menu",
    "after_hours_menu",
)


def default_root() -> Path:
    return Path(os.environ.get("OPENIVR_ROOT") or Path(__file__).resolve().parents[2])


# --------------------------------------------------------------------- helpers


def load_draft(root: Path) -> dict[str, Any]:
    """The flow being edited: the saved document, or the starter template."""
    path = load_config(root).flow_path
    if path.exists():
        try:
            with path.open(encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and data.get("menus"):
                return normalise_draft(data)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable flow %s: %s", path, exc)
    return normalise_draft(default_flow())


def normalise_draft(data: dict[str, Any]) -> dict[str, Any]:
    """Fill in the menu keys the builder form assumes.

    A hand-edited or half-written flow is kept as-is (including unknown keys) so
    the builder never loses someone's work - it only adds the defaults the form
    and templates would otherwise trip over.
    """
    menus = data.get("menus")
    if not isinstance(menus, dict):
        return data
    for name, body in list(menus.items()):
        if not isinstance(body, dict):
            menus[name] = {"options": {}}
            body = menus[name]
        body.setdefault("prompt", None)
        if not isinstance(body.get("options"), dict):
            body["options"] = {}
        timeout = body.get("timeout")
        if not isinstance(timeout, dict):
            timeout = {}
            body["timeout"] = timeout
        timeout.setdefault("seconds", 8)
        timeout.setdefault("max_retries", 1)
        timeout.setdefault("prompt", "timeout-msg")
        invalid = body.get("invalid")
        if not isinstance(invalid, dict):
            invalid = {}
            body["invalid"] = invalid
        invalid.setdefault("max_retries", 1)
        invalid.setdefault("prompt", "invalid-msg")
    return data


def save_draft(root: Path, data: dict[str, Any]) -> Path:
    path = load_config(root).flow_path
    Flow.from_dict(data).save(path)
    log.info("Builder saved flow to %s", path)
    return path


def write_smtp(root: Path, smtp: dict[str, Any]) -> Path:
    path = root / "data" / "smtp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump({"smtp": smtp}, fh, indent=2)
        fh.write("\n")
    log.info("Builder saved SMTP settings to %s", path)
    return path


def read_smtp(root: Path) -> dict[str, Any]:
    path = root / "data" / "smtp.json"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        smtp = data.get("smtp", data) if isinstance(data, dict) else {}
        return smtp if isinstance(smtp, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def menu_names(data: dict[str, Any]) -> list[str]:
    return list(data.get("menus") or {})


def get_menu(data: dict[str, Any], name: str) -> dict[str, Any]:
    return (data.get("menus") or {}).get(name) or {}


def prompts_of(cfg: Config) -> list[str]:
    return sorted(available_sounds(cfg.sounds_dir))


def flow_errors(cfg: Config, data: dict[str, Any], *, with_media: bool = False) -> list[str]:
    try:
        return validate(
            data,
            max_depth=cfg.ivr.max_menu_depth,
            max_options=cfg.ivr.max_options_per_menu,
            available_sounds=available_sounds(cfg.sounds_dir) or None if with_media else None,
        )
    except FlowError as exc:
        return exc.errors


# ------------------------------------------------------------------- security


def authorised(request: Request) -> bool:
    token = os.environ.get("OPENIVR_BUILDER_TOKEN", "")
    if token:
        supplied = request.query_params.get("token") or request.headers.get("x-openivr-token")
        return secrets.compare_digest(str(supplied or ""), token)
    host = request.client.host if request.client else ""
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def guard(request: Request) -> JSONResponse | None:
    if authorised(request):
        return None
    return JSONResponse({"error": "forbidden: builder POSTs must come from localhost"}, 403)


# ---------------------------------------------------------------- application


def create_app(root: str | Path | None = None) -> FastAPI:
    root_path = Path(root) if root else default_root()

    app = FastAPI(title="openivr builder", docs_url=None, redoc_url=None)
    app.state.root = root_path
    app.state.keep_open = False
    app.state.server = None
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def render(
        request: Request, name: str, ctx: dict[str, Any] | None = None, status: int = 200
    ) -> HTMLResponse:
        cfg = load_config(root_path)
        base: dict[str, Any] = {
            "request": request,
            "root": root_path,
            "config": cfg,
            "sounds": prompts_of(cfg),
            "smtp": read_smtp(root_path),
            "menus": menu_names(load_draft(root_path)),
            "keep_open": app.state.keep_open,
        }
        base.update(ctx or {})
        return templates.TemplateResponse(
            request=request, name=name, context=base, status_code=status
        )

    # ------------------------------------------------------------------ pages

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        cfg = load_config(root_path)
        draft = load_draft(root_path)
        errors = flow_errors(cfg, draft)
        tree = None
        if not errors:
            try:
                tree = menu_tree(Flow.from_dict(draft))
            except FlowError:
                tree = None
        return render(
            request,
            "index.html",
            {
                "files": list_sound_files(cfg.sounds_dir),
                "errors": errors,
                "tree": tree,
                "voicemail": cfg.voicemail.enabled,
                "flow_exists": cfg.flow_path.exists(),
            },
        )

    @app.get("/status")
    async def status() -> JSONResponse:
        cfg = load_config(root_path)
        draft = load_draft(root_path)
        return JSONResponse(
            {
                "root": str(root_path),
                "config_sources": cfg.sources,
                "ari": {
                    "base_url": cfg.ari.base_url,
                    "username": cfg.ari.username,
                    "password_set": bool(cfg.ari.password),
                },
                "flow_file": str(cfg.flow_path),
                "flow_exists": cfg.flow_path.exists(),
                "menus": menu_names(draft),
                "prompts": len(list_sound_files(cfg.sounds_dir)),
                "validation_errors": flow_errors(cfg, draft),
                "voicemail_enabled": cfg.voicemail.enabled,
                "smtp_configured": bool(read_smtp(root_path)),
            }
        )

    # ---------------------------------------------------------------- builder

    @app.get("/build", response_class=HTMLResponse)
    async def build_page(request: Request, menu: str | None = None) -> HTMLResponse:
        cfg = load_config(root_path)
        draft = load_draft(root_path)
        names = menu_names(draft)
        if not names:
            return render(
                request,
                "builder.html",
                {
                    "draft": draft,
                    "names": [],
                    "current": "",
                    "menu": {},
                    "digits": DIGIT_SLOTS,
                    "actions": sorted(ACTIONS),
                    "time_route": None,
                    "errors": ["No menus yet - create the first one below."],
                },
            )
        current = menu if menu in names else (draft.get("start_menu") or names[0])
        if current not in names:
            current = names[0]
        return render(
            request,
            "builder.html",
            {
                "draft": draft,
                "names": names,
                "current": current,
                "menu": get_menu(draft, current),
                "digits": DIGIT_SLOTS,
                "actions": sorted(ACTIONS),
                "time_route": draft.get("time_route"),
                "errors": flow_errors(cfg, draft),
            },
        )

    @app.post("/build/save")
    async def build_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        cfg = load_config(root_path)
        data = flow_from_form(root_path, form)
        errors = flow_errors(cfg, data)
        if errors:
            current = str(form.get("current_menu") or data.get("start_menu", "main"))
            return render(
                request,
                "builder.html",
                {
                    "draft": data,
                    "names": menu_names(data),
                    "current": current,
                    "menu": get_menu(data, current),
                    "digits": DIGIT_SLOTS,
                    "actions": sorted(ACTIONS),
                    "time_route": data.get("time_route"),
                    "errors": errors,
                },
                status=400,
            )
        save_draft(root_path, data)
        return RedirectResponse(
            f"/build?menu={form.get('current_menu') or ''}&ok=1", status_code=303
        )

    @app.post("/build/menu/add")
    async def menu_add(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = str(form.get("name", "")).strip()
        if not name:
            return RedirectResponse("/build", status_code=303)
        data = load_draft(root_path)
        data.setdefault("menus", {})
        if name not in data["menus"]:
            template = get_menu(data, str(form.get("copy_from", "")))
            data["menus"][name] = {
                "prompt": str(form.get("prompt", "") or "").strip() or None,
                "timeout": template.get("timeout")
                or {"seconds": 8, "max_retries": 1, "prompt": "timeout-msg"},
                "invalid": template.get("invalid") or {"max_retries": 1, "prompt": "invalid-msg"},
                "options": {},
            }
            log.info("Builder added menu %r", name)
            save_draft(root_path, data)
        return RedirectResponse(f"/build?menu={name}", status_code=303)

    @app.post("/build/menu/delete")
    async def menu_delete(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = str(form.get("menu", ""))
        data = load_draft(root_path)
        menus = data.get("menus", {})
        if len(menus) > 1 and name in menus:
            del menus[name]
            fallback = data.get("start_menu", "main")
            if fallback == name:
                fallback = next(iter(menus))
            data["start_menu"] = fallback
            for body in menus.values():
                for option in (body.get("options") or {}).values():
                    if option.get("target") == name:
                        option["target"] = fallback
                fail = (body.get("timeout") or {}).get("fail_action")
                if fail and fail.get("target") == name:
                    fail["target"] = fallback
            save_draft(root_path, data)
            log.info("Builder deleted menu %r", name)
        return RedirectResponse("/build", status_code=303)

    # ------------------------------------------------------------------ media

    @app.get("/media", response_class=HTMLResponse)
    async def media_page(
        request: Request, ok: str | None = None, err: str | None = None
    ) -> HTMLResponse:
        cfg = load_config(root_path)
        return render(
            request,
            "media.html",
            {
                "files": list_sound_files(cfg.sounds_dir),
                "ok": ok,
                "err": err,
                "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
                "allowed": ", ".join(sorted(AUDIO_SUFFIXES)),
            },
        )

    @app.post("/media/upload")
    async def media_upload(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        upload: UploadFile | None = form.get("file")  # type: ignore[assignment]
        if upload is None or not upload.filename:
            return RedirectResponse("/media?err=choose an audio file first", status_code=303)
        cfg = load_config(root_path)
        original = Path(upload.filename).name
        suffix = Path(original).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            return RedirectResponse(
                "/media?err="
                + f"unsupported format {suffix or 'unknown'} (allowed: "
                + ", ".join(sorted(AUDIO_SUFFIXES)),
                status_code=303,
            )
        name = f"{Path(original).stem}{suffix}"
        problem = check_prompt_name(name)
        if problem:
            return RedirectResponse(f"/media?err={problem}", status_code=303)
        if suffix != ".wav":
            return RedirectResponse(
                "/media?err=please upload .wav - convert first, e.g. "
                "sox in.wav -r 8000 -c 1 -b 16 out.wav",
                status_code=303,
            )

        cfg.sounds_dir.mkdir(parents=True, exist_ok=True)
        tmp = cfg.sounds_dir / f".upload-{name}"
        size = 0
        try:
            with tmp.open("wb") as fh:
                while chunk := await upload.read(1 << 16):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError(f"file larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
                    fh.write(chunk)
            if size == 0:
                raise ValueError("file is empty")
            tmp.replace(cfg.sounds_dir / name)
        except ValueError as exc:
            tmp.unlink(missing_ok=True)
            return RedirectResponse(f"/media?err={exc}", status_code=303)
        finally:
            await upload.close()
        log.info("Builder stored prompt %s (%d bytes)", name, size)
        return RedirectResponse(f"/media?ok=stored {name} ({size:,} bytes)", status_code=303)

    @app.post("/media/delete")
    async def media_delete(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        name = Path(str(form.get("name", ""))).name
        cfg = load_config(root_path)
        if not name:
            return RedirectResponse("/media", status_code=303)
        for candidate in (cfg.sounds_dir / name, cfg.sounds_dir / f"{name}.wav"):
            if candidate.is_file():
                candidate.unlink()
                log.info("Builder deleted prompt %s", candidate.name)
                return RedirectResponse(f"/media?ok=deleted {candidate.name}", status_code=303)
        return RedirectResponse("/media?err=file not found", status_code=303)

    # ------------------------------------------------------------------- smtp

    @app.get("/smtp", response_class=HTMLResponse)
    async def smtp_page(
        request: Request, next: str | None = None, ok: str | None = None, err: str | None = None
    ) -> HTMLResponse:
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
            request, "smtp.html", {"current": current, "next": next, "ok": ok, "err": err}
        )

    @app.post("/smtp")
    async def smtp_save(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        smtp = {
            "enabled": bool(form.get("enabled")),
            "host": str(form.get("host", "")).strip(),
            "port": int(form.get("port") or 587),
            "starttls": bool(form.get("starttls")),
            "username": str(form.get("username", "")).strip(),
            "password": str(form.get("password", "")),
            "from_addr": str(form.get("from_addr", "")).strip(),
            "alerts_to": [
                r.strip()
                for r in str(form.get("alerts_to", "")).replace(";", ",").split(",")
                if r.strip()
            ],
        }
        nxt = str(form.get("next") or "")

        def fail(message: str):
            return render(
                request,
                "smtp.html",
                {"current": smtp, "next": nxt, "ok": None, "err": message},
                status=400,
            )

        if smtp["enabled"] and not smtp["host"]:
            return fail("host is required when SMTP is enabled")
        if smtp["enabled"] and not smtp["alerts_to"]:
            return fail("at least one recipient is required (service-down alerts)")
        write_smtp(root_path, smtp)
        if bool(form.get("test")):
            cfg = load_config(root_path)
            merged = {**asdict(cfg.smtp), **smtp}
            ok, detail = await anyio.to_thread.run_sync(Notifier(SmtpCfg(**merged)).test)
            target = f"/smtp?ok=test {'passed' if ok else 'FAILED'}: {detail}"
            return RedirectResponse(target + (f"&next={nxt}" if nxt else ""), status_code=303)
        return RedirectResponse(
            "/smtp?ok=SMTP settings saved" + (f"&next={nxt}" if nxt else ""), status_code=303
        )

    # ----------------------------------------------------------------- finish

    @app.get("/finish", response_class=HTMLResponse)
    async def finish_page(request: Request) -> HTMLResponse:
        cfg = load_config(root_path)
        draft = load_draft(root_path)
        return render(request, "finish.html", finish_context(cfg, draft))

    @app.post("/finish")
    async def finish_action(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        cfg = load_config(root_path)
        draft = load_draft(root_path)
        errors = flow_errors(cfg, draft, with_media=True)
        if errors:
            return render(request, "finish.html", finish_context(cfg, draft, errors), status=400)
        save_draft(root_path, draft)
        if cfg.voicemail.enabled and not read_smtp(root_path):
            log.info("Voicemail enabled - asking for SMTP settings before shutdown")
            return RedirectResponse("/smtp?next=/finish", status_code=303)
        return shutdown_response(request, "IVR flow saved - the builder is shutting down")

    @app.post("/shutdown")
    async def shutdown(request: Request) -> Any:
        denied = guard(request)
        if denied:
            return denied
        return shutdown_response(request, "Builder stopped on request")

    def finish_context(
        cfg: Config, draft: dict[str, Any], errors: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "draft": draft,
            "errors": flow_errors(cfg, draft, with_media=True) if errors is None else errors,
            "menus": menu_names(draft),
            "files": list_sound_files(cfg.sounds_dir),
            "smtp_ready": bool(read_smtp(root_path)),
            "voicemail": cfg.voicemail.enabled,
        }

    def shutdown_response(request: Request, message: str):
        if app.state.keep_open:
            cfg = load_config(root_path)
            ctx = finish_context(cfg, load_draft(root_path))
            ctx["ok"] = message + " (keep-open mode: not stopping)"
            return render(request, "finish.html", ctx)
        response = render(request, "done.html", {"message": message})
        server = app.state.server
        if server is not None:
            log.info("Builder shutting down: %s", message)
            loop = asyncio.get_running_loop()
            loop.create_task(_stop_server(server))
        return response

    return app


async def _stop_server(server) -> None:
    await anyio.sleep(0.8)
    server.should_exit = True


# --------------------------------------------------------------- form parsing


def flow_from_form(root: Path, form) -> dict[str, Any]:
    """Rebuild the whole flow document from the submitted builder form."""
    data = load_draft(root)
    existing = data.get("menus") or {}
    menus: dict[str, Any] = {
        name: dict(body) for name, body in existing.items() if isinstance(body, dict)
    }

    names: list[str] = []
    for key, _value in form.multi_items():
        if key.startswith("menu__"):
            name = key.split("__", 1)[1]
            if name not in names:
                names.append(name)

    for name in names:
        menus[name] = {
            "prompt": _text(form, f"prompt__{name}"),
            "timeout": {
                "seconds": _float(form, f"timeout_seconds__{name}", 8.0),
                "max_retries": _int(form, f"timeout_retries__{name}", 1),
                "prompt": _text(form, f"timeout_prompt__{name}"),
            },
            "invalid": {
                "max_retries": _int(form, f"invalid_retries__{name}", 1),
                "prompt": _text(form, f"invalid_prompt__{name}"),
            },
            "options": {},
        }
        fail_type = str(form.get(f"fail_action__{name}") or "hangup")
        if fail_type in {"submenu", "goto", "dial"}:
            fail: dict[str, Any] = {"action": fail_type}
            target = _text(form, f"fail_target__{name}")
            endpoint = _text(form, f"fail_endpoint__{name}")
            if target:
                fail["target"] = target
            if endpoint:
                fail["endpoint"] = endpoint
            menus[name]["timeout"]["fail_action"] = fail

    for name in names:
        options: dict[str, Any] = {}
        for digit in DIGIT_SLOTS:
            if not form.get(f"enabled__{digit}__{name}"):
                continue
            options[digit] = option_from_form(form, name, digit)
        menus[name]["options"] = options

    merged = dict(data)
    merged["menus"] = menus or existing
    merged["version"] = int(data.get("version", 1))
    merged["welcome"] = _text(form, "welcome")
    merged["goodbye"] = _text(form, "goodbye")

    start = _text(form, "start_menu") or str(data.get("start_menu", "main"))
    merged["start_menu"] = (
        start if start in merged["menus"] else next(iter(merged["menus"]), "main")
    )

    route = data.get("time_route")
    if route:
        merged["time_route"] = {
            "prompt": _text(form, "tr_prompt") or route.get("prompt"),
            "timezone": _text(form, "tr_timezone") or route.get("timezone", "UTC"),
            "business_menu": _text(form, "tr_business"),
            "after_hours_menu": _text(form, "tr_after_hours"),
            "hours": route.get("hours") or {},
        }
    return merged


def option_from_form(form, menu: str, digit: str) -> dict[str, Any]:
    kind = str(form.get(f"action__{digit}__{menu}") or "hangup")
    option: dict[str, Any] = {"action": kind}
    for field in MENU_FIELDS:
        value = _text(form, f"{field}__{digit}__{menu}")
        if value:
            option[field] = value
    if kind == "collect":
        option["min_digits"] = _int(form, f"min_digits__{digit}__{menu}", 1)
        option["max_digits"] = _int(form, f"max_digits__{digit}__{menu}", 4)
    next_type = str(form.get(f"next_action__{digit}__{menu}") or "hangup")
    if next_type != "hangup":
        nxt: dict[str, Any] = {"action": next_type}
        target = _text(form, f"next_target__{digit}__{menu}")
        endpoint = _text(form, f"next_endpoint__{digit}__{menu}")
        if target:
            nxt["target"] = target
        if endpoint:
            nxt["endpoint"] = endpoint
        option["next"] = nxt
    if kind == "voicemail":
        after = str(form.get(f"after_action__{digit}__{menu}") or "hangup")
        if after != "hangup":
            after_action: dict[str, Any] = {"action": after}
            after_target = _text(form, f"after_target__{digit}__{menu}")
            if after_target:
                after_action["target"] = after_target
            option["after"] = after_action
    return {k: v for k, v in option.items() if v not in ("", None)}


def _text(form, key: str) -> str:
    return str(form.get(key) or "").strip()


def _int(form, key: str, default: int) -> int:
    raw = _text(form, key)
    try:
        return int(float(raw))
    except ValueError:
        return default


def _float(form, key: str, default: float) -> float:
    raw = _text(form, key)
    try:
        return float(raw)
    except ValueError:
        return default


# ----------------------------------------------------------------------- serve


def serve(
    host: str = "127.0.0.1",
    port: int = 8090,
    root: str | Path | None = None,
    keep_open: bool = False,
) -> None:
    """Run the builder with uvicorn (blocking)."""
    import uvicorn

    application = create_app(root)
    application.state.keep_open = keep_open
    server = uvicorn.Server(
        uvicorn.Config(application, host=host, port=port, log_level="info", access_log=False)
    )
    application.state.server = server
    server.run()


app = create_app()
