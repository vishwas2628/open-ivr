"""Configuration loading for openivr.

Precedence (later wins)::

    dataclass defaults
        -> config.yaml
        -> system.json          (root, written by system/ installer scripts)
        -> data/smtp.json       (written by the builder)
        -> OPENIVR_* environment variables

Every path in the config is resolved against the project root so the app can
be started from any working directory (systemd, cron, a shell, the builder).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

ENV_PREFIX = "OPENIVR_"

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_FILE = "config.yaml"
SYSTEM_FILE = "system.json"
SMTP_FILE = "data/smtp.json"


class ConfigError(RuntimeError):
    """Raised when the configuration cannot be loaded or is inconsistent."""


@dataclass(slots=True)
class AppCfg:
    name: str = "openivr"
    stasis_app: str = "openivr"
    answer_delay: float = 0.4
    max_call_seconds: float = 3600.0
    company_id: str = "openivr"


@dataclass(slots=True)
class AriCfg:
    base_url: str = "http://127.0.0.1:8088"
    username: str = "openivr"
    password: str = ""
    reconnect_initial: float = 2.0
    reconnect_max: float = 60.0


@dataclass(slots=True)
class PathsCfg:
    project_root: str = "."
    flows: str = "data/flows"
    flow_file: str = "data/ivr_flow.json"
    sounds: str = "data/sounds"
    recordings: str = "data/recordings"
    logs: str = "data/logs"
    asterisk_spool: str = "/var/spool/asterisk/recording"
    asterisk_sounds: str = "/var/lib/asterisk/sounds/custom"


@dataclass(slots=True)
class LoggingCfg:
    level: str = "INFO"
    file: str = "data/logs/openivr.log"
    max_bytes: int = 4 * 1024 * 1024
    backup_count: int = 5
    console: bool = True
    color: bool = True
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(module)s:%(lineno)d | %(message)s"
    datefmt: str = "%Y-%m-%d %H:%M:%S"


@dataclass(slots=True)
class IvrCfg:
    prompt_timeout: float = 8.0
    invalid_attempts: int = 2
    timeout_attempts: int = 2
    max_menu_depth: int = 3
    max_options_per_menu: int = 5
    interdigit_timeout: float = 4.0
    goodbye_prompt: str = "goodbye"


@dataclass(slots=True)
class DialCfg:
    mode: str = "dialplan"
    context: str = "openivr-dial"
    extension: str = "s"
    priority: int = 1
    originate_timeout: float = 30.0
    dial_timeout: float = 45.0


@dataclass(slots=True)
class VoicemailCfg:
    enabled: bool = True
    dir: str = "data/recordings/voicemail"
    format: str = "wav"
    max_duration: int = 120
    silence_timeout: int = 4
    beep: bool = True
    terminate_on: str = "any"
    min_duration: int = 1
    greeting_prompt: str = "vm-greeting"
    notify: bool = True


@dataclass(slots=True)
class PostgresCfg:
    host: str = "127.0.0.1"
    port: int = 5432
    dbname: str = "ivrdb"
    user: str = "ivr"
    password: str = ""


@dataclass(slots=True)
class CdrCfg:
    enabled: bool = True
    backend: str = "csv"
    csv_file: str = "data/logs/cdr.csv"
    postgres: PostgresCfg = field(default_factory=PostgresCfg)


@dataclass(slots=True)
class SmtpCfg:
    enabled: bool = False
    host: str = ""
    port: int = 587
    starttls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    alerts_to: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RecordCfg:
    ivr_leg: bool = False
    dir: str = "data/recordings/ivr"


@dataclass(slots=True)
class HealthCfg:
    interval: float = 30.0


@dataclass(slots=True)
class Config:
    app: AppCfg = field(default_factory=AppCfg)
    ari: AriCfg = field(default_factory=AriCfg)
    paths: PathsCfg = field(default_factory=PathsCfg)
    logging: LoggingCfg = field(default_factory=LoggingCfg)
    ivr: IvrCfg = field(default_factory=IvrCfg)
    dial: DialCfg = field(default_factory=DialCfg)
    voicemail: VoicemailCfg = field(default_factory=VoicemailCfg)
    cdr: CdrCfg = field(default_factory=CdrCfg)
    smtp: SmtpCfg = field(default_factory=SmtpCfg)
    record: RecordCfg = field(default_factory=RecordCfg)
    health: HealthCfg = field(default_factory=HealthCfg)

    root: Path = field(default=DEFAULT_ROOT, init=False)

    _loaded: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def sources(self) -> list[str]:
        """Files that contributed to this configuration, in load order."""
        return list(self._loaded)

    def path(self, value: str | os.PathLike[str]) -> Path:
        """Resolve *value* against the project root."""
        p = Path(value).expanduser()
        return p if p.is_absolute() else (self.root / p)

    @property
    def sounds_dir(self) -> Path:
        return self.path(self.paths.sounds)

    @property
    def flow_path(self) -> Path:
        return self.path(self.paths.flow_file)

    @property
    def recordings_dir(self) -> Path:
        return self.path(self.paths.recordings)

    @property
    def voicemail_dir(self) -> Path:
        return self.path(self.voicemail.dir)

    @property
    def record_dir(self) -> Path:
        return self.path(self.record.dir)

    @property
    def logs_dir(self) -> Path:
        return self.path(self.paths.logs)

    def ensure_dirs(self) -> None:
        for p in (
            self.sounds_dir,
            self.recordings_dir,
            self.voicemail_dir,
            self.logs_dir,
            self.path(self.paths.flows),
            self.path(self.record.dir),
        ):
            p.mkdir(parents=True, exist_ok=True)

    def require_ari_password(self) -> str:
        if not self.ari.password:
            raise ConfigError(
                "No ARI password configured. Run system/install.sh (writes system.json) "
                "or set OPENIVR_ARI_PASSWORD."
            )
        return self.ari.password


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except PermissionError as exc:
        raise ConfigError(
            f"Cannot read {path}: permission denied. The installer writes it as "
            f"root - hand it back to your user with:\n"
            f"    sudo chown $(id -un):$(id -gn) {path} && sudo chmod 640 {path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object, got {type(data).__name__}")
    return data


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a YAML mapping, got {type(data).__name__}")
    return data


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge *extra* into *base* (returns a new dict)."""
    out = dict(base)
    for key, value in extra.items():
        if value is None:
            continue
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = _merge(current, value)
        else:
            out[key] = value
    return out


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    if lowered in {"null", "none", ""}:
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.startswith("[") or value.startswith("{"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


SECTION_ALIASES: dict[str, str] = {
    "ari": "ari",
    "app": "app",
    "path": "paths",
    "paths": "paths",
    "log": "logging",
    "logging": "logging",
    "ivr": "ivr",
    "dial": "dial",
    "vm": "voicemail",
    "voicemail": "voicemail",
    "cdr": "cdr",
    "db": "cdr.postgres",
    "postgres": "cdr.postgres",
    "mail": "smtp",
    "smtp": "smtp",
    "record": "record",
    "recording": "record",
    "health": "health",
}


def _env_overrides() -> dict[str, Any]:
    """Build a config dict from ``OPENIVR_<SECTION>_<KEY>`` env vars.

    ``OPENIVR_LOG_LEVEL`` and ``OPENIVR_LOGGING_LEVEL`` are the same thing, and
    ``OPENIVR_DB_PASSWORD`` lands in ``cdr.postgres.password``. Unknown prefixes
    fall back to the ``app`` section and are ignored unless they match a field.
    """
    out: dict[str, Any] = {}
    for raw_key, raw_val in os.environ.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        parts = [p.lower() for p in raw_key[len(ENV_PREFIX) :].split("_") if p]
        if not parts:
            continue
        head = parts[0]
        section = SECTION_ALIASES.get(head, "app")
        key = "_".join(parts[1:]) or head
        out.setdefault(section, {})[key] = _coerce(raw_val)
    return out


def _apply(obj: Any, data: dict[str, Any]) -> Any:
    """Recursively apply a plain dict onto a dataclass instance."""
    if not is_dataclass(obj):
        return obj
    known = {f.name: f for f in fields(obj)}
    for key, value in data.items():
        norm = key.replace("-", "_")
        if "." in norm:
            head, _, rest = norm.partition(".")
            nested = getattr(obj, head, None)
            if rest and is_dataclass(nested) and isinstance(value, dict):
                _apply(nested, {rest: value})
            continue
        fld = known.get(norm)
        if fld is None:
            continue
        current = getattr(obj, fld.name)
        if is_dataclass(current) and isinstance(value, dict):
            setattr(obj, fld.name, _apply(current, value))
        elif fld.type in ("bool", bool) and isinstance(value, str):
            setattr(obj, fld.name, _coerce(value))
        elif isinstance(current, float) and isinstance(value, int) and not isinstance(value, bool):
            setattr(obj, fld.name, float(value))
        elif isinstance(current, int) and isinstance(value, str) and fld.type in (int, "int"):
            setattr(obj, fld.name, int(_coerce(value)))
        else:
            setattr(obj, fld.name, value)
    return obj


def load_config(
    root: str | os.PathLike[str] | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> Config:
    """Build a :class:`Config` from YAML + JSON sources + env + *overrides*."""
    root_path = Path(root or os.environ.get("OPENIVR_ROOT") or DEFAULT_ROOT).resolve()
    cfg = Config()
    cfg.root = root_path
    cfg.paths.project_root = str(root_path)

    data: dict[str, Any] = {}
    loaded: list[str] = []

    for name, reader in (
        (CONFIG_FILE, _read_yaml),
        (SYSTEM_FILE, _read_json),
        (SMTP_FILE, _read_json),
    ):
        path = root_path / name
        chunk = reader(path)
        if chunk:
            data = _merge(data, chunk)
            loaded.append(name)

    data = _merge(data, _env_overrides())
    if overrides:
        data = _merge(data, overrides)

    _apply(cfg, data)
    cfg._loaded = loaded or ["defaults"]
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.dial.mode not in {"dialplan", "originate"}:
        raise ConfigError(f"dial.mode must be 'dialplan' or 'originate', got {cfg.dial.mode!r}")
    if cfg.cdr.backend not in {"csv", "none"}:
        raise ConfigError(f"cdr.backend must be 'csv' or 'none' (postgres/both removed - use Asterisk cdr.conf), got {cfg.cdr.backend!r}")
    if cfg.voicemail.terminate_on not in {"none", "any", "*", "#"}:
        raise ConfigError(
            f"voicemail.terminate_on must be none|any|*|#, got {cfg.voicemail.terminate_on!r}"
        )
    if cfg.ivr.max_options_per_menu < 1:
        raise ConfigError("ivr.max_options_per_menu must be >= 1")
    if cfg.ivr.max_menu_depth < 1:
        raise ConfigError("ivr.max_menu_depth must be >= 1")
    if cfg.smtp.enabled and not cfg.smtp.host:
        raise ConfigError("smtp.enabled is true but smtp.host is empty")
    # cdr.postgres retained for schema provisioning (60-database.sh) but not used by openivr
