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
EXAMPLE_CONFIG_FILE = "example.config.yaml"
SYSTEM_FILE = "system.json"
SMTP_FILE = "data/smtp.json"

SECRET_MODE = 0o600


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
class BuilderCfg:
    """Credentials of the web builder (all secrets, all in ``config.yaml``)."""

    company_name: str = ""
    username: str = ""
    password_hash: str = ""
    session_ttl: int = 7200
    session_secret: str = ""


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
    enabled: bool = False
    ivr_leg: bool = False
    dir: str = "data/recordings/ivr"
    mixmonitor_dir: str = "/var/spool/asterisk/monitor"
    retention_days: int = 30


@dataclass(slots=True)
class HealthCfg:
    interval: float = 30.0


@dataclass(slots=True)
class Config:
    app: AppCfg = field(default_factory=AppCfg)
    builder: BuilderCfg = field(default_factory=BuilderCfg)
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
                "No ARI password configured. Run system/install.sh (writes system.json "
                "and config.yaml) or set OPENIVR_ARI_PASSWORD."
            )
        return self.ari.password


# --------------------------------------------------------------------- secrets
#
# config.yaml holds every secret (ARI, builder login, database, SMTP). It is
# git-ignored, mode 0600, and copied from example.config.yaml on first run.
# The helpers below create it and write single values into it while keeping the
# comments and layout of the file intact.


def config_path(root: str | os.PathLike[str]) -> Path:
    return Path(root) / CONFIG_FILE


def example_config_path(root: str | os.PathLike[str]) -> Path:
    return Path(root) / EXAMPLE_CONFIG_FILE


def _harden(path: Path) -> None:
    """Keep secret files readable by their owner only."""
    try:
        path.chmod(SECRET_MODE)
    except OSError:
        pass


def bootstrap_config(root: str | os.PathLike[str]) -> Path:
    """Make sure ``<root>/config.yaml`` exists (copy of the template), mode 0600.

    Returns the path of the config file. Safe to call repeatedly: an existing
    config.yaml is only re-chmod'ed, never overwritten.
    """
    path = config_path(root)
    if not path.exists():
        template = example_config_path(root)
        text = ""
        if template.is_file():
            text = template.read_text(encoding="utf-8")
        else:
            text = (
                "# AUTO-GENERATED by openivr - copy example.config.yaml for the full\n"
                "# commented template. This file holds secrets: never commit it.\n"
                "app:\n  stasis_app: openivr\n\n"
                "builder:\n  company_name: \"\"\n  username: admin\n  password_hash: \"\"\n"
                f"  session_ttl: 7200\n  session_secret: \"\"\n\n"
                "ari:\n  base_url: http://127.0.0.1:8088\n  username: openivr\n  password: \"\"\n"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _harden(path)
    return path


def _yaml_scalar(value: Any) -> str:
    """Render *value* as a single-line YAML scalar (JSON strings are valid YAML)."""
    if value is None:
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value))


def _key_of(line: str) -> str:
    return line.split(":", 1)[0].strip()


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_for(lines: list[str], path: list[str]) -> tuple[int, int, int] | None:
    """Line range holding the children of the nested key *path*.

    Returns ``None`` when any element of *path* is missing, otherwise
    ``(lo, hi, indent)``: the slice where children live (empty when the key has
    none yet) and the indentation those children are written with.
    """
    lo, hi = 0, len(lines)
    indent = 0
    for name in path:
        found: int | None = None
        for i in range(lo, hi):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _indent_of(line) != indent or _key_of(line) != name:
                continue
            found = i
            break
        if found is None:
            return None
        child_lo, child_hi = found + 1, len(lines)
        for j in range(child_lo, child_hi):
            if not lines[j].strip():
                continue
            if _indent_of(lines[j]) <= indent:
                child_hi = j
                break
        lo, hi = child_lo, child_hi
        indent += 2
    # Respect a file that indents differently than the usual two spaces.
    if not path:
        return lo, hi, 0
    for i in range(lo, hi):
        line = lines[i]
        if line.strip() and not line.lstrip().startswith("#"):
            indent = _indent_of(line)
            break
    return lo, hi, indent


def set_config_value(root: str | os.PathLike[str], key: str, value: Any) -> Path:
    """Write a single ``section.key`` value into ``config.yaml``, keeping comments.

    Used by ``system/steps/30-ari.sh`` (ARI password) and
    ``60-database.sh`` (database password) instead of sed-ing the file.
    """
    parts = [p for p in key.split(".") if p]
    if not parts:
        raise ConfigError("set_config_value needs a key like ari.password")
    path = bootstrap_config(root)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    leaf = parts[-1]
    scalar = _yaml_scalar(value)

    block = _block_for(lines, parts[:-1])
    if block is not None:
        lo, hi, child_indent = block
        for i in range(lo, hi):
            line = lines[i]
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if _indent_of(line) == child_indent and _key_of(line) == leaf:
                head = line[: line.index(":")]
                lines[i] = f"{head}: {scalar}\n"
                break
        else:
            insert_at = hi
            while insert_at > lo and not lines[insert_at - 1].strip():
                insert_at -= 1
            lines.insert(insert_at, f"{' ' * child_indent}{leaf}: {scalar}\n")
    else:
        depth = 0
        for candidate in range(len(parts) - 1, 0, -1):
            if _block_for(lines, parts[:candidate]) is not None:
                depth = candidate
                break
        insert_at = _block_for(lines, parts[:depth])[1] if depth else len(lines)
        if depth == 0 and (not lines or lines[-1].strip()):
            lines.append("\n")
        while insert_at > 0 and not lines[insert_at - 1].strip():
            insert_at -= 1
        intermediates = parts[depth:-1]
        for offset, name in enumerate(intermediates):
            lines.insert(insert_at + offset, f"{'  ' * (depth + offset)}{name}:\n")
        lines.insert(
            insert_at + len(intermediates),
            f"{'  ' * (depth + len(intermediates))}{leaf}: {scalar}\n",
        )

    path.write_text("".join(lines), encoding="utf-8")
    _harden(path)
    return path


def read_config_value(root: str | os.PathLike[str], key: str, default: Any = None) -> Any:
    """Read one ``section.key`` value from ``config.yaml`` without loading it all."""
    data = _read_yaml(config_path(root))
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


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
    "builder": "builder",
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
    bootstrap: bool = False,
) -> Config:
    """Build a :class:`Config` from YAML + JSON sources + env + *overrides*.

    With ``bootstrap=True`` a missing ``config.yaml`` is created from
    ``example.config.yaml`` first (mode 0600) - the CLI and the builder use it
    so a fresh checkout works without a manual copy step.
    """
    root_path = Path(root or os.environ.get("OPENIVR_ROOT") or DEFAULT_ROOT).resolve()
    if bootstrap:
        bootstrap_config(root_path)
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
