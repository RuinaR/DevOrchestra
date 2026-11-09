"""Read-only publication gate for tracked and staged private paths."""
from pathlib import Path
import subprocess


def check_index(root):
    def paths(*args):
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", *args],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        return result.stdout.split(b"\0")[:-1]

    if any(b".ai-local" in path.lower().split(b"/") for path in paths("--cached")):
        return False
    return not paths("--cached", "--ignored", "--exclude-standard")


if __name__ == "__main__":
    try:
        clean = check_index(Path(__file__).resolve().parent.parent)
    except (OSError, subprocess.SubprocessError):
        clean = False
    print("PUBLIC_INDEX_OK" if clean else "PUBLIC_INDEX_BLOCKED")
    raise SystemExit(0 if clean else 1)
