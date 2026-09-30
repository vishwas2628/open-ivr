"""Web IVR builder (FastAPI, server-side rendered)."""

from .server import app, create_app, serve

__all__ = ["app", "create_app", "serve"]
