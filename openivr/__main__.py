"""Command line interface: ``openivr <command>``.

Commands
--------
``run``        start the IVR (default)
``verify``     check config, flow, sounds, ARI, ports - the step run_ivr.sh runs
``builder``    start the FastAPI IVR builder (normally run_ivr.sh does this)
``originate``  place a single outgoing test call
``flow``       validate / show / render a flow document
``sounds``     list the available prompts
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Config, ConfigError, load_config
from .flow import Flow, FlowError, menu_tree, validate
from .media import available_sounds, list_sound_files

BANNER_ROWS: tuple[str, ...] = (
    "██████╗ ██████╗ ███████╗███╗   ██╗      ██╗██╗   ██╗██████╗",
    "██╔═══██╗██╔══██╗██╔════╝████╗  ██║      ██║██║   ██║██╔══██╗",
    "██║   ██║██████╔╝█████╗  ██╔██╗ ██║█████╗██║██║   ██║██████╔╝",
    "██║   ██║██╔═══╝ ██╔══╝  ██║╚██╗██║╚════╝██║╚██╗ ██╔╝██╔══██╗",
    "╚██████╔╝██║     ███████╗██║ ╚████║      ██║ ╚████╔╝ ██║  ██║",
    " ╚═════╝ ╚═╝     ╚══════╝╚═╝  ╚═══╝      ╚═╝  ╚═══╝  ╚═╝  ╚═╝",
)


def gradient_banner() -> str:
    """The standard banner, each row shaded from indigo (top) to cyan (bottom)."""
    top, bottom = (99, 68), (88, 215)
    last = max(len(BANNER_ROWS) - 1, 1)
    lines = []
    for index, row in enumerate(BANNER_ROWS):
        share = index / last
        red = round(top[0] + (bottom[0] - top[0]) * share)
        green = round(top[1] + (bottom[1] - top[1]) * share)
        lines.append(f"\033[38;2;{red};{green};255m\033[1m{row}\033[0m")
    return "\n" + "\n".join(lines)


BANNER = "\n" + "\n".join(BANNER_ROWS)


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True

    @property
    def mark(self) -> str:
        return "OK  " if self.ok else ("FAIL" if self.fatal else "WARN")


ARI_PREFIX = "/ari"


def ari_url(cfg: Config, path: str = "") -> str:
    """Full ARI REST URL for *path* (``asterisk/info``, ``applications`` ...).

    asyncari appends the ``/ari`` prefix to ``ari.base_url`` itself when it
    loads the Swagger document, so the configured base URL stays prefix-free.
    """
    base = (cfg.ari.base_url or "http://127.0.0.1:8088").rstrip("/")
    if not base.endswith(ARI_PREFIX):
        base += ARI_PREFIX
    return f"{base}/{path.lstrip('/')}" if path else base


def _log_setup_quiet(cfg: Config) -> None:
    import logging

    logging.getLogger().addHandler(logging.NullHandler())


def cmd_run(args: argparse.Namespace) -> int:
    import anyio

    from .logging_setup import setup_logging
    from .runner import Service

    cfg = load_config(args.root)
    log = setup_logging(cfg)
    print(gradient_banner() if sys.stdout.isatty() else BANNER)
    log.info("openivr %s starting (root=%s)", _version(), cfg.root)
    log.info("config sources: %s", ", ".join(cfg.sources))

    try:
        anyio.run(Service(cfg).run)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        log.info("interrupted")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    checks: list[Check] = []
    try:
        cfg = load_config(args.root)
    except ConfigError as exc:
        print(f"FAIL  configuration: {exc}")
        return 1
    _log_setup_quiet(cfg)
    checks.append(Check("configuration", True, f"loaded from {', '.join(cfg.sources)}"))

    flow = None
    try:
        flow = Flow.load(cfg.flow_path)
        checks.append(Check("flow file", True, f"{cfg.flow_path} ({len(flow.menus)} menus)"))
    except FlowError as exc:
        checks.append(Check("flow file", False, "; ".join(exc.errors)))
        flow = None

    sounds = available_sounds(cfg.sounds_dir)
    checks.append(
        Check(
            "sounds",
            bool(sounds),
            f"{len(sounds)} prompt(s) in {cfg.sounds_dir}",
            fatal=False,
        )
    )
    if flow is not None:
        errors = validate(
            flow,
            max_depth=cfg.ivr.max_menu_depth,
            max_options=cfg.ivr.max_options_per_menu,
            available_sounds=sounds or None,
        )
        missing_media = [e for e in errors if "no audio file" in e]
        hard = [e for e in errors if e not in missing_media]
        checks.append(
            Check("flow validation", not hard, "; ".join(hard) or "no structural problems")
        )
        checks.append(
            Check(
                "flow media",
                not missing_media,
                "; ".join(missing_media) or "every prompt has audio",
                fatal=False,
            )
        )

    checks.append(
        Check(
            "ari credentials",
            bool(cfg.ari.password),
            f"{cfg.ari.username}@{(cfg.ari.base_url or '').rstrip('/')}",
        )
    )
    checks.extend(_ari_checks(cfg))
    checks.extend(_port_checks(cfg))
    checks.append(
        Check(
            "cdr",
            cfg.cdr.enabled,
            f"backend={cfg.cdr.backend} file={cfg.cdr.csv_file}"
            if cfg.cdr.backend in {"csv", "both"}
            else f"backend={cfg.cdr.backend}",
            fatal=False,
        )
    )
    if cfg.smtp.enabled:
        from .notify import Notifier

        ok, detail = Notifier(cfg.smtp).test()
        checks.append(Check("smtp", ok, detail, fatal=False))
    else:
        checks.append(Check("smtp", True, "disabled", fatal=False))

    width = max(len(c.name) for c in checks)
    fatal = False
    print("\nopenivr verification")
    print("-" * (width + 60))
    for c in checks:
        print(f"{c.mark}  {c.name:<{width}}  {c.detail}")
        if not c.ok and c.fatal:
            fatal = True
    print("-" * (width + 60))
    print("RESULT:", "FAILED" if fatal else "OK")
    return 1 if fatal else 0


def _ari_checks(cfg: Config) -> list[Check]:
    out: list[Check] = []
    if not cfg.ari.password:
        return out
    url = ari_url(cfg, "asterisk/info")
    token = base64.b64encode(f"{cfg.ari.username}:{cfg.ari.password}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - local ARI
            data = json.loads(resp.read().decode())
        out.append(
            Check(
                "ari http",
                True,
                f"asterisk {data.get('system', '?')} {data.get('asterisk_version', '?')}",
            )
        )
    except urllib.error.HTTPError as exc:
        out.append(Check("ari http", False, f"HTTP {exc.code} from {url}"))
    except Exception as exc:  # noqa: BLE001
        out.append(Check("ari http", False, f"{type(exc).__name__}: {exc}"))
    return out


def _port_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    parsed = urllib.parse.urlparse(cfg.ari.base_url or "http://127.0.0.1:8088")
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 8088)
    try:
        with socket.create_connection((host, port), timeout=4):
            checks.append(Check("ari port", True, f"{host}:{port} open"))
    except OSError as exc:
        checks.append(Check("ari port", False, str(exc)))
    return checks


def cmd_builder(args: argparse.Namespace) -> int:
    cfg = load_config(args.root)
    if args.host is None:
        args.host = "127.0.0.1"
    if args.port is None:
        args.port = 8090
    print(f"openivr builder on http://{args.host}:{args.port} (config: {', '.join(cfg.sources)})")
    from .builder.server import serve

    serve(host=args.host, port=args.port, root=cfg.root, keep_open=args.keep_open)
    return 0


def cmd_originate(args: argparse.Namespace) -> int:
    import anyio
    import asyncari

    from .logging_setup import setup_logging

    cfg = load_config(
        args.root, overrides={"ari": {"password": args.password}} if args.password else None
    )
    log = setup_logging(cfg)

    async def main() -> int:
        from .originate import originate_simple

        async with asyncari.connect(
            base_url=cfg.ari.base_url,
            apps=[cfg.app.stasis_app],
            username=cfg.ari.username,
            password=cfg.require_ari_password(),
        ) as client:
            res = await originate_simple(
                client,
                endpoint=args.to,
                app=args.app or cfg.app.stasis_app,
                caller_id=args.caller_id,
                timeout=args.timeout,
                variables=dict(pair.split("=", 1) for pair in args.set) if args.set else None,
            )
            log.info(
                "originate %s -> %s (%s)",
                res.endpoint,
                "ok" if res.ok else "failed",
                res.reason or res.channel_id,
            )
            return 0 if res.ok else 1

    return anyio.run(main)


def cmd_flow(args: argparse.Namespace) -> int:
    cfg = load_config(args.root)
    _log_setup_quiet(cfg)
    path = Path(args.file) if args.file else cfg.flow_path
    try:
        flow = Flow.load(path)
    except FlowError as exc:
        for err in exc.errors:
            print(f"FAIL  {err}")
        return 1
    if args.action == "show":
        print(json.dumps(flow.to_dict(), indent=2))
        return 0
    if args.action == "tree":
        print(json.dumps(menu_tree(flow), indent=2))
        return 0
    sounds = available_sounds(cfg.sounds_dir)
    errors = validate(
        flow,
        max_depth=cfg.ivr.max_menu_depth,
        max_options=cfg.ivr.max_options_per_menu,
        available_sounds=sounds or None,
    )
    for err in errors:
        print(f"FAIL  {err}")
    if errors:
        return 1
    print(f"OK    {path} is valid ({len(flow.menus)} menus, {len(sounds)} prompts)")
    return 0


def cmd_sounds(args: argparse.Namespace) -> int:
    cfg = load_config(args.root)
    files = list_sound_files(cfg.sounds_dir)
    if args.json:
        print(json.dumps(files, indent=2))
        return 0
    if not files:
        print(f"no audio files in {cfg.sounds_dir} - see {cfg.sounds_dir}/README.md")
        return 1
    print(f"{len(files)} prompt(s) in {cfg.sounds_dir}:")
    for f in files:
        print(f"  {f['name']:<28} {f['suffix']:<5} {f['size']:>9,} bytes")
    return 0


def _version() -> str:
    from . import __version__

    return __version__


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="openivr", description="Open source IVR on Asterisk ARI")
    p.add_argument("--root", default=None, help="project root (default: package location)")
    sub = p.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="start the IVR (default)")
    run_p.set_defaults(func=cmd_run)

    ver_p = sub.add_parser("verify", help="verify config/flow/sounds/ARI")
    ver_p.set_defaults(func=cmd_verify)

    bld_p = sub.add_parser("builder", help="start the web IVR builder")
    bld_p.add_argument("--host", default=None)
    bld_p.add_argument("--port", type=int, default=None)
    bld_p.add_argument("--keep-open", action="store_true", help="do not stop after a build")
    bld_p.set_defaults(func=cmd_builder)

    org_p = sub.add_parser("originate", help="place a single outgoing call")
    org_p.add_argument("--to", required=True, help="dial string, e.g. PJSIP/1001 or SIP/trunk/…")
    org_p.add_argument("--app", default=None)
    org_p.add_argument("--caller-id", default=None)
    org_p.add_argument("--timeout", type=int, default=30)
    org_p.add_argument("--password", default=None, help="override the ARI password")
    org_p.add_argument("--set", action="append", help="channel variable K=V (repeatable)")
    org_p.set_defaults(func=cmd_originate)

    flow_p = sub.add_parser("flow", help="work with the flow document")
    flow_p.add_argument("action", choices=["validate", "show", "tree"])
    flow_p.add_argument("--file", default=None)
    flow_p.set_defaults(func=cmd_flow, command="flow")

    snd_p = sub.add_parser("sounds", help="list available prompts")
    snd_p.add_argument("--json", action="store_true")
    snd_p.set_defaults(func=cmd_sounds)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        args = parser.parse_args(["run", *(argv or [])])
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
