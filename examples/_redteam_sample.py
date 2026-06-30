"""TEMP red-team sample — do not merge. Intentionally insecure."""
import subprocess


def run_user_command(cmd: str):
    # Shell injection: untrusted input straight into a shell.
    return subprocess.run(cmd, shell=True)


API_KEY = "sk-live-9f8e7d6c5b4a3210deadbeefcafef00d"  # hardcoded secret
