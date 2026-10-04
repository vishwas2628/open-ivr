"""The YAML-aware config editor used by the installer and the builder."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from openivr.config import (
    bootstrap_config,
    config_path,
    load_config,
    read_config_value,
    set_config_value,
)


def _seed(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text(
        "# openivr configuration\n"
        "app:\n"
        "  stasis_app: openivr        # the ARI application name\n"
        "  answer_delay: 0.4\n"
        "\n"
        "ari:\n"
        "  base_url: http://127.0.0.1:8088\n"
        "  username: openivr\n"
        "  password: \"\"\n"
        "\n"
        "cdr:\n"
        "  backend: csv\n"
        "  csv_file: data/logs/cdr.csv\n",
        encoding="utf-8",
    )
    return root / "config.yaml"


def test_bootstrap_creates_a_private_config(tmp_path: Path) -> None:
    path = bootstrap_config(tmp_path)
    assert path == config_path(tmp_path)
    assert path.is_file()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_bootstrap_never_overwrites(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    bootstrap_config(tmp_path)
    assert path.read_text() == _seed(tmp_path).read_text()


def test_read_config_value(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert read_config_value(tmp_path, "ari.username") == "openivr"
    assert read_config_value(tmp_path, "app.answer_delay") == 0.4  # typed
    assert read_config_value(tmp_path, "ari.password") == ""
    assert read_config_value(tmp_path, "does.not.exist") in (None, "")


def test_set_existing_scalar(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    set_config_value(tmp_path, "ari.username", "openivr-user")
    assert load_config(tmp_path, bootstrap=False).ari.username == "openivr-user"
    # the comment on an untouched line survives
    assert "the ARI application name" in path.read_text()


def test_set_is_idempotent(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    set_config_value(tmp_path, "ari.username", "one")
    first = path.read_text()
    set_config_value(tmp_path, "ari.username", "one")
    assert path.read_text() == first
    # ...and changing back works too
    set_config_value(tmp_path, "ari.username", "two")
    set_config_value(tmp_path, "ari.username", "one")
    assert path.read_text() == first


def test_set_new_key_in_existing_block(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    set_config_value(tmp_path, "ari.password", "s3cret")
    set_config_value(tmp_path, "ari.verify_ssl", "false")
    cfg = load_config(tmp_path, bootstrap=False)
    assert cfg.ari.password == "s3cret"
    assert cfg.ari.base_url == "http://127.0.0.1:8088"  # untouched
    assert "s3cret" in path.read_text()


def test_set_creates_missing_nested_blocks(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    set_config_value(tmp_path, "builder.username", "admin")
    set_config_value(tmp_path, "builder.session_ttl", "7200")
    assert "builder:" in path.read_text()
    cfg = load_config(tmp_path, bootstrap=False)
    assert cfg.builder.username == "admin"
    assert cfg.builder.session_ttl == 7200


def test_set_keeps_file_mode_private(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    os.chmod(path, 0o600)
    set_config_value(tmp_path, "ari.password", "s3cret")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_set_handles_four_space_indentation(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("ari:\n    username: openivr\n    password: \"\"\n", encoding="utf-8")
    set_config_value(path.parent, "ari.password", "hunter2")
    assert load_config(tmp_path, bootstrap=False).ari.password == "hunter2"
    # the existing indentation style is kept and no tabs sneak in
    assert "\n\t" not in path.read_text()
    assert path.read_text().splitlines()[2].startswith("    password:")


def test_set_quotes_and_special_values(tmp_path: Path) -> None:
    path = _seed(tmp_path)
    for value in ("with space", "yes", "1234", "#hash", "", "a: b"):
        set_config_value(tmp_path, "ari.password", value)
        cfg = load_config(tmp_path, bootstrap=False)
        assert cfg.ari.password == value, value


def test_new_top_level_block_is_created(tmp_path: Path) -> None:
    """Unknown blocks are appended: the installer may add keys later."""
    path = _seed(tmp_path)
    set_config_value(tmp_path, "voicemail.enabled", "true")
    assert "voicemail:" in path.read_text()
    assert load_config(tmp_path, bootstrap=False).voicemail.enabled is True