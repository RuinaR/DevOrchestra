"""Local identity of a source or packaged Command Center."""
import json
from pathlib import Path
import sys


def build_label():
    if not getattr(sys, "frozen", False):
        return "Source checkout"
    try:
        info = json.loads(Path(__file__).with_name("release.json").read_text(encoding="utf-8"))
        return f'{info["version"]} · {info["git_sha"][:12]}'
    except (OSError, ValueError, KeyError, TypeError):
        return "Packaged build · identity unavailable"
