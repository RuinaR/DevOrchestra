"""Versioned public defaults shared by source runs and packaged releases."""
from hashlib import sha256
from pathlib import Path

VERSION = "public-v4"
ROOT = Path(__file__).resolve().parent.parent / "defaults"
SOURCES = {
    "global": "global/AGENTS.md",
    "project": "project/AGENTS.template.md",
    **{role: f"roles/{role}.md" for role in (
        "manager-planning", "developer", "reviewer", "verifier", "manager-final",
    )},
}


def _load(relative):
    path = ROOT / relative
    data = path.read_bytes()
    return {"path": path, "version": VERSION, "sha256": sha256(data).hexdigest(),
            "text": data.decode("utf-8").strip()}


_BUNDLE = {section: _load(relative) for section, relative in SOURCES.items()}


def public_instruction(section):
    return _BUNDLE[section].copy()


COMMON = public_instruction("global")["text"]
PROJECT = public_instruction("project")["text"]
ROLES = {role: public_instruction(role)["text"] for role in (
    "manager-planning", "developer", "reviewer", "verifier",
)}


def defaults_text():
    return "\n\n".join(public_instruction(section)["text"] for section in SOURCES)
