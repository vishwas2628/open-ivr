"""Write rendered confs into the Asterisk etc dir and reload."""

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

RELOAD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "asterisk_reload.sh"


def asterisk_etc() -> Path:
    return Path(os.environ.get("OPENIVR_ASTERISK_ETC") or "/etc/asterisk")


def staging_dir(root: Path) -> Path:
    path = root / "data" / "asterisk-build"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_rendered(files: dict[str, str], dest: Path) -> list[str]:
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


def _copy_rendered_with_sudo(files: dict[str, str], target: Path, stage: Path) -> tuple[list[str], str]:
    if not shutil.which("sudo"):
        return [], "sudo not available"
    try:
        target_stat = target.stat()
        uid, gid = target_stat.st_uid, target_stat.st_gid
    except OSError:
        uid, gid = 0, 0

    copied: list[str] = []
    errors: list[str] = []
    for rel in sorted(files):
        src = stage / rel
        dst = target / rel
        result = subprocess.run(
            ["sudo", "-n", "install", "-D", "-m", "0640", f"-o{uid}", f"-g{gid}", str(src), str(dst)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            errors.append((result.stderr or result.stdout or f"exit {result.returncode}").strip())
            continue
        copied.append(str(dst))
    if errors:
        return [], "; ".join(errors)[:500]
    return copied, ""


def publish(root: Path, *, dest: Path | None = None, reload: bool = True) -> dict[str, Any]:
    files = render_all(root)
    stage = staging_dir(root)
    write_rendered(files, stage)
    target = dest or asterisk_etc()
    copied: list[str] = []
    copy_error = ""
    try:
        copied = write_rendered(files, target)
    except OSError as exc:
        sudo_copied, sudo_error = _copy_rendered_with_sudo(files, target, stage)
        if sudo_copied:
            copied = sudo_copied
        else:
            copy_error = f"{exc}. sudo fallback: {sudo_error}"
            log.warning(
                "Could not write %s: %s — left files in %s; sudo fallback failed: %s",
                target,
                exc,
                stage,
                sudo_error,
            )
    reload_ok = False
    reload_detail = "skipped"
    if reload and not copy_error and str(target) in {str(asterisk_etc()), "/etc/asterisk"}:
        reload_ok, reload_detail = run_reload()
    return {
        "files": sorted(files),
        "staging": str(stage),
        "dest": str(target),
        "copied": copied,
        "copy_error": copy_error,
        "reload_ok": reload_ok,
        "reload_detail": reload_detail,
    }


def run_reload() -> tuple[bool, str]:
    script = RELOAD_SCRIPT
    if script.is_file():
        command = ["bash", str(script)]
        if os.geteuid() != 0 and shutil.which("sudo"):
            command = ["sudo", "-n", *command]
        try:
            proc = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
            return proc.returncode == 0, detail[:500]
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    asterisk = shutil.which("asterisk")
    if not asterisk:
        return False, "asterisk binary not found"
    command = [asterisk, "-rx", "core reload"]
    if os.geteuid() != 0 and shutil.which("sudo"):
        command = ["sudo", "-n", *command]
    try:
        proc = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        detail = (proc.stdout or proc.stderr or "").strip() or f"exit {proc.returncode}"
        return proc.returncode == 0, detail[:500]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
