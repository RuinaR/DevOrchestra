"""Read-only hints for the project picker. Runtime approval remains authoritative."""
from configparser import ConfigParser, Error as ConfigError
from pathlib import Path
import re

from .lease import Blocked
from .ledger import check_path


def inspect_folder(folder):
    """Use bounded root metadata only. Do not contact a remote or run project code."""
    root = Path(folder)
    check_path(root)
    if not root.is_dir():
        raise ValueError("기존 프로젝트 폴더를 선택하세요.")
    markers = {
        "pyproject.toml": "Python · pyproject.toml",
        "package.json": "JavaScript/TypeScript · package.json",
        "Cargo.toml": "Rust · Cargo.toml",
        "go.mod": "Go · go.mod",
        "CMakeLists.txt": "C/C++ · CMake",
        "project.godot": "Godot · project.godot",
        "ProjectSettings": "Unity · ProjectSettings",
        "pom.xml": "Java · Maven",
        "build.gradle": "JVM · Gradle",
        "README.md": "README.md",
        "AGENTS.md": "AGENTS.md",
        "tests": "tests 폴더",
        "test": "test 폴더",
        "main.py": "실행 후보 main.py",
        "index.html": "실행 후보 index.html",
    }
    hints = [label for name, label in markers.items() if (root / name).exists()]
    git = root / ".git"
    check_path(git)
    if not git.exists():
        return "folder", "", "", hints
    if not git.is_dir():
        return "git", "", "", hints
    try:
        for name in ("config", "HEAD"):
            check_path(git / name)
        config = ConfigParser(interpolation=None)
        with (git / "config").open(encoding="utf-8") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            return "git", "", "", hints
        config.read_string(data)
        with (git / "HEAD").open(encoding="ascii") as stream:
            head = stream.read(512).strip()
        origin = config['remote "origin"']
        url = origin.get("url", "")
        branch = head.removeprefix("ref: refs/heads/")
        if (head.startswith("ref: refs/heads/") and
                re.fullmatch(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_.-]+", branch)
                and re.fullmatch(r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+", url)
                and origin.get("pushurl", url) == url
                and not any(name.lower().startswith(("include", "url ")) for name in config.sections())):
            return "github", url, branch, hints
    except (Blocked, OSError, UnicodeError, ConfigError, KeyError):
        pass
    return "git", "", "", hints
