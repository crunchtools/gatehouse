"""Temporary test file — delete after verification."""
import subprocess
def run(cmd: str) -> str:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False).stdout  # noqa: S602, S603, S607
