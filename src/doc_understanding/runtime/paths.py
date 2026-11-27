"""Stable project paths shared by local services and isolated parser experiments."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("NH_DOCUMENT_ROOT", Path(__file__).resolve().parents[3])).resolve()
