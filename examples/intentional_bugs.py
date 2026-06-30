"""Intentional bugs for testing Gatehouse PR review comments.

This file exists solely to verify that Gatehouse posts inline
PR comments on real GitHub PRs. Delete after verification.
"""

from __future__ import annotations

import os
import subprocess


def get_user_data(user_id: str) -> dict:
    """Fetch user data with an obvious SQL injection vulnerability."""
    query = f"SELECT * FROM users WHERE id = '{user_id}'"  # noqa: S608
    return {"query": query}


def run_command(user_input: str) -> str:
    """Execute a command with unsanitized user input."""
    result = subprocess.run(  # noqa: S603
        f"echo {user_input}",  # noqa: S607
        shell=True,  # noqa: S602
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


API_KEY = "sk-1234567890abcdef"  # noqa: S105


def read_file(filename: str) -> str:
    """Path traversal vulnerability."""
    path = os.path.join("/data", filename)
    with open(path) as f:
        return f.read()
