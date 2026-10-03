"""SIP trunk provider catalog used by the builder trunk step."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).parent / "data" / "providers.json"


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    with CATALOG_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return {"countries": [], "providers": []}
    return data


def countries() -> list[dict[str, Any]]:
    return list(load_catalog().get("countries") or [])


def providers(*, country: str | None = None) -> list[dict[str, Any]]:
    items = list(load_catalog().get("providers") or [])
    if not country:
        return items
    return [p for p in items if country in (p.get("countries") or []) or "ANY" in (p.get("countries") or [])]


def provider_by_id(provider_id: str) -> dict[str, Any] | None:
    for item in providers():
        if item.get("id") == provider_id:
            return item
    return None


def catalog_public() -> dict[str, Any]:
    """JSON safe for the browser (match IPs stay; they are not secrets)."""
    return {"countries": countries(), "providers": providers()}
