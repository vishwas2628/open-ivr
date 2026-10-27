"""Entry point for python -m openivr.core."""

import sys
from .service import run_app

if __name__ == "__main__":
    sys.exit(run_app())
