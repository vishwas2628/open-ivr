"""openivr - an open-source IVR core built on Asterisk ARI."""

import sys
from . import core

# Backwards compatibility alias
sys.modules[f"{__name__}.ari"] = core

__version__ = "0.1.0"

__all__ = ["__version__", "core"]
