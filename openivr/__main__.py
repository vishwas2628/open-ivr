"""Command line interface: ``openivr <command>``.

Commands
--------
``run``        start the IVR (default)
``verify``     check config, flow, sounds, ARI, ports ('make verify')
``builder``    start the FastAPI IVR builder ('make builder')
``publish``    render the Asterisk configs and deploy them (make deploy)
``config``     inspect / bootstrap config.yaml and write secrets into it
``creds``      reset the builder login (new random password, printed once)
``originate``  place a single outgoing test call
``flow``       validate / show / render a flow document
``sounds``     list the available prompts
"""

from __future__ import annotations

import argparse
import base64
import json
import os
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

DEFAULT_ROOT_ARG = Path(__file__).resolve().parent.parent

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

    cfg = load_config(args.root, bootstrap=True)
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
        cfg = load_config(args.root, bootstrap=True)
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


def cmd_config(args: argparse.Namespace) -> int:
    """config show|path|bootstrap|get|set - the YAML-aware config.yaml editor.

    The installer uses ``config set`` (30-ari.sh, 60-database.sh) so secrets
    land in config.yaml without hand-editing YAML or losing comments.
    """
    from .config import (
        bootstrap_config,
        config_path,
        read_config_value,
        set_config_value,
    )

    root = Path(args.root) if args.root else Path(os.environ.get("OPENIVR_ROOT") or DEFAULT_ROOT_ARG)
    root = root.resolve()

    if args.action == "path":
        print(config_path(root))
        return 0

    if args.action == "bootstrap":
        path = bootstrap_config(root)
        print(f"config.yaml ready: {path} (mode 0600)")
        return 0

    if args.action == "show":
        cfg = load_config(root, bootstrap=True)
        print(f"root      : {cfg.root}")
        print(f"sources   : {', '.join(cfg.sources)}")
        print(f"config.yaml: {config_path(root)}")
        print(f"ari       : {cfg.ari.username}@{cfg.ari.base_url} "
              f"({'password set' if cfg.ari.password else 'NO PASSWORD'})")
        print(f"builder   : {cfg.builder.username or '(unset)'} "
              f"({'password set' if cfg.builder.password_hash else 'no password'}) "
              f"company={cfg.builder.company_name or '(unset)'}")
        print(f"database  : {cfg.cdr.postgres.user}@{cfg.cdr.postgres.host}"
              f"/{cfg.cdr.postgres.dbname} "
              f"({'password set' if cfg.cdr.postgres.password else 'no password'})")
        print(f"smtp      : {'enabled' if cfg.smtp.enabled else 'disabled'}")
        return 0

    if args.action == "get":
        if not args.items:
            print("config get needs a key (e.g. ari.password)", file=sys.stderr)
            return 2
        value = read_config_value(root, args.items[0], None)
        if value is None or value == "":
            print(f"{args.items[0]} is not set", file=sys.stderr)
            return 1
        print(value if not isinstance(value, (dict, list)) else json.dumps(value))
        return 0

    if args.action == "set":
        if not args.items:
            print("config set needs KEY=VALUE (e.g. ari.password=s3cret)", file=sys.stderr)
            return 2
        for assignment in args.items:
            key, _, raw = assignment.partition("=")
            if not key:
                print(f"invalid assignment {assignment!r}", file=sys.stderr)
                return 2
            parsed: object = raw
            if raw.lower() in {"true", "false"}:
                parsed = raw.lower() == "true"
            elif raw.lstrip("-").isdigit():
                parsed = int(raw)
            elif not raw:
                parsed = ""
            set_config_value(root, key.strip(), parsed)
            print(f"{key.strip()} written to {config_path(root)}")
        return 0

    print(f"unknown config action {args.action!r}", file=sys.stderr)
    return 2


def cmd_publish(args: argparse.Namespace) -> int:
    from .builder.publish import publish

    root = Path(args.root or os.environ.get("OPENIVR_ROOT") or DEFAULT_ROOT_ARG).resolve()
    dest = Path(args.dest).resolve() if args.dest else None
    result = publish(root, dest=dest, reload=not args.no_reload)
    for name in result["files"]:
        print(f"  rendered {name}")
    print(f"staging   : {result['staging']}")
    print(f"deployed  : {result['dest']}")
    if result.get("copy_error"):
        print(f"deploy failed: {result['copy_error']}", file=sys.stderr)
        return 1
    print(f"reload    : {'ok' if result['reload_ok'] else 'skipped/failed'} ({result['reload_detail']})")
    return 0 if result["reload_ok"] or result["reload_detail"] == "skipped" else 1


def cmd_creds(args: argparse.Namespace) -> int:
    """Reset the builder login: new password, printed exactly once."""
    from .builder.auth import (
        AuthError,
        generate_password,
        hash_password,
        load_auth,
        save_credentials,
    )
    from .config import config_path

    root = Path(args.root or os.environ.get("OPENIVR_ROOT") or DEFAULT_ROOT_ARG).resolve()
    cfg = load_config(root, bootstrap=True)
    auth = load_auth(cfg.root, cfg, provision=False)
    password = args.password or generate_password()
    if len(password) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        return 2
    username = args.username or auth.username
    try:
        save_credentials(root, auth, username=username, password_hash=hash_password(password))
    except AuthError as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    print(f"builder login updated in {config_path(root)}")
    print(f"  username: {auth.username}")
    print(f"  password: {password}")
    print("This password is shown only once - store it now.")
    return 0


def cmd_builder(args: argparse.Namespace) -> int:
    cfg = load_config(args.root, bootstrap=True)
    if args.host is None:
        args.host = "127.0.0.1"
    if args.port is None:
        args.port = 8090

    root = cfg.root
    standalone = not (root / "system.json").is_file()
    company = str(getattr(cfg.builder, "company_name", "") or "").strip()
    if args.company:
        company = args.company.strip()
    if not company and sys.stdin.isatty() and not args.no_prompt:
        # plan: ask once for the company name, then remember it in config.yaml
        try:
            answer = input("Company or site name (blank to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer:
            company = answer
            from .config import set_config_value

            set_config_value(root, "builder.company_name", company)

    print(f"openivr builder on http://{args.host}:{args.port} (config: {', '.join(cfg.sources)})")
    if standalone:
        print()
        print("  standalone mode: no system.json found - Asterisk was never installed here")
        print("  everything in the builder works; only 'Deploy to Asterisk' is unavailable")
        print(f"  run 'sudo make install' in {root} to install the system side")
    from .builder.server import serve

    serve(
        host=args.host,
        port=args.port,
        root=root,
        keep_open=args.keep_open,
        standalone=standalone,
    )
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
    bld_p.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    bld_p.add_argument("--port", type=int, default=None, help="TCP port (default 8090)")
    bld_p.add_argument("--keep-open", action="store_true", help="do not stop after a build")
    bld_p.add_argument("--company", default=None, help="company/site name shown in the UI")
    bld_p.add_argument(
        "--no-prompt", action="store_true", help="never ask interactively (scripts, CI)"
    )
    bld_p.set_defaults(func=cmd_builder)

    pub_p = sub.add_parser("publish", help="render + deploy the Asterisk config")
    pub_p.add_argument("--dest", default=None, help="target conf dir (default /etc/asterisk)")
    pub_p.add_argument("--no-reload", action="store_true", help="copy the files but skip the reload")
    pub_p.set_defaults(func=cmd_publish)

    cfg_p = sub.add_parser("config", help="inspect / edit config.yaml")
    cfg_p.add_argument(
        "action",
        choices=["show", "path", "bootstrap", "get", "set"],
        help="show = summary, path = config.yaml location, bootstrap = create it",
    )
    cfg_p.add_argument("items", nargs="*", help="KEY for 'get', KEY=VALUE pairs for 'set'")
    cfg_p.set_defaults(func=cmd_config)

    cred_p = sub.add_parser("creds", help="reset the builder login")
    cred_p.add_argument("--username", default=None, help="new username (default: keep current)")
    cred_p.add_argument("--password", default=None, help="new password (default: random)")
    cred_p.set_defaults(func=cmd_creds)

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
