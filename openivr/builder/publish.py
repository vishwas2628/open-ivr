"""Render the builder JSON into Asterisk config files and deploy them.

The deploy path never calls sudo. It is three separable stages:

    render -> stage (data/asterisk-build) -> copy (/etc/asterisk) -> reload

The installer grants the asterisk group write access on the conf dir once
(``sudo ./system/install.sh --grant-permissions``), after which a member of the
asterisk group can publish from the browser or from ``make deploy``.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

from .render import render_all

log = logging.getLogger("openivr.builder")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
RELOAD_SCRIPT = SCRIPTS_DIR / "asterisk_reload.sh"
DEPLOY_SCRIPT = SCRIPTS_DIR / "deploy_asterisk.sh"

ASTERISK_USER = os.environ.get("OPENIVR_ASTERISK_USER", "asterisk")
ASTERISK_GROUP = os.environ.get("OPENIVR_ASTERISK_GROUP", "asterisk")


def asterisk_etc() -> Path:
    return Path(os.environ.get("OPENIVR_ASTERISK_ETC") or "/etc/asterisk")


def staging_dir(root: Path) -> Path:
    path = root / "data" / "asterisk-build"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_rendered(files: dict[str, str], dest: Path) -> list[str]:
    """Write rendered conf bodies into ``dest`` (used for the staging dir)."""
    written: list[str] = []
    for rel, body in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        try:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        except OSError:
            pass
        written.append(str(target))
    return written


def is_real_etc(target: Path) -> bool:
    """True only for a real /etc/asterisk-style directory.

    ``OPENIVR_ASTERISK_ETC`` (or ``--dest``) can point anywhere; only paths
    under /etc are treated as the system directory, so tests and staging
    targets are written straight from Python without the deploy script or the
    "run the permission grant" hints.
    """
    try:
        resolved = target.resolve()
    except OSError:  # pragma: no cover - unreadable path
        return False
    return resolved.parent == Path("/etc")


def _writable_dir(path: Path) -> bool:
    return path.is_dir() and os.access(path, os.W_OK)


def deploy_error_hint(target: Path) -> str:
    root = Path(__file__).resolve().parents[2]
    return (
        f"{target} is not writable by the current user ({os.environ.get('USER') or 'unknown'}). "
        f"Run the one-time grant: sudo {root / 'system' / 'install.sh'} --grant-permissions "
        f"(and make sure you are in the {ASTERISK_GROUP} group)."
    )


def copy_rendered(stage: Path, target: Path, files: dict[str, str]) -> tuple[list[str], str]:
    """Copy staged files into the Asterisk conf dir without sudo.

    The Asterisk conf dir goes through scripts/deploy_asterisk.sh so ownership
    and modes stay consistent; any other destination (tests, staging) is
    written directly.
    """
    copied: list[str] = []
    error = ""

    # Only the real conf dir needs the deploy script (ownership/modes); a
    # custom destination (tests, staging) is written straight from Python so a
    # missing directory is created instead of failing.
    if is_real_etc(target) and DEPLOY_SCRIPT.is_file():
        try:
            proc = subprocess.run(
                ["bash", str(DEPLOY_SCRIPT), str(stage), str(target)],
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            proc = None
            error = f"deploy_asterisk.sh failed: {exc}"
        if proc is not None:
            detail = (proc.stdout or proc.stderr or "").strip()
            if proc.returncode == 0:
                return [str(target / rel) for rel in sorted(files)], ""
            error = f"deploy_asterisk.sh exit {proc.returncode}: {detail[:500]}"

    if not error:
        try:
            copied = write_rendered(files, target)
        except OSError as exc:
            error = str(exc)

    if error:
        return [], error
    return copied, ""


def publish(
    root: Path,
    *,
    dest: Path | None = None,
    reload: bool = True,
    standalone: bool = False,
) -> dict[str, Any]:
    """Render the builder's JSON into Asterisk config files and deploy them.

    ``standalone`` (no ``system.json``: the builder is running on a machine
    without Asterisk) keeps everything local - the files are rendered into the
    staging directory, nothing is copied to ``/etc/asterisk`` and no reload is
    attempted. The caller gets the same dict, so the UI shows the result either
    way.
    """
    files = render_all(root)
    stage = staging_dir(root)
    write_rendered(files, stage)
    target = dest or asterisk_etc()

    copied: list[str] = []
    copy_error = ""

    if standalone:
        log.info("standalone mode: rendered %d file(s) into %s", len(files), stage)
        return {
            "files": sorted(files),
            "staging": str(stage),
            "dest": str(stage),
            "copied": [],
            "copy_error": "",
            "reload_ok": False,
            "reload_detail": "standalone: nothing deployed to Asterisk",
            "standalone": True,
        }

    copied, copy_error = copy_rendered(stage, target, files)
    if copy_error:
        if is_real_etc(target):
            copy_error = f"{copy_error}. {deploy_error_hint(target)}"
        log.warning(
            "Could not write %s: %s - left the rendered files in %s",
            target,
            copy_error,
            stage,
        )

    reload_ok = False
    reload_detail = "skipped"
    if reload and is_real_etc(target) and not copy_error:
        reload_ok, reload_detail = run_reload()

    return {
        "files": sorted(files),
        "staging": str(stage),
        "dest": str(target),
        "copied": copied,
        "copy_error": copy_error,
        "reload_ok": reload_ok,
        "reload_detail": reload_detail,
        "standalone": False,
    }


def run_reload() -> tuple[bool, str]:
    """Reload Asterisk (no sudo - membership in the asterisk group is enough)."""
    script = RELOAD_SCRIPT
    if not script.is_file():
        return False, f"reload script not found: {script}"
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
    ok = proc.returncode == 0
    if not ok:
        detail = f"{detail} - reloading needs the asterisk group (newgrp asterisk)"
    return ok, detail[:500]