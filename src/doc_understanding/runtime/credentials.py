"""Load an existing local credential only when an enabled service explicitly asks."""

import os

from .paths import ROOT


def model_key():
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    for name in (".env.local", ".env"):
        path = ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("OPENAI_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
                break
        if key:
            return key
    return None
