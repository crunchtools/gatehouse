"""gourmand.yml's `Refuse plaintext secrets` step, run against a scratch repo.

The step's script is extracted from the workflow and run as a push from a base
commit to one that adds the lines under test, the way the reusable job runs it.
Refused lines fail the step with an `::error`; a named literal in a test path
only warns (a475).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

GOURMAND = Path(__file__).parent.parent / ".github" / "workflows" / "gourmand.yml"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git needed")


def _script() -> str:
    block = re.search(
        r"- name: Refuse plaintext secrets\n.*?run: \|\n(.*?)\n      - name:",
        GOURMAND.read_text(),
        re.S,
    )
    assert block, "Refuse plaintext secrets step not found"
    return textwrap.dedent(block.group(1))


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _scan(
    tmp_path: Path, files: dict[str, str], base_files: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Commit `files` on top of a base commit and run the step as a push."""
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_AUTHOR_NAME": "Alice",
        "GIT_AUTHOR_EMAIL": "alice@example.com",
        "GIT_COMMITTER_NAME": "Alice",
        "GIT_COMMITTER_EMAIL": "alice@example.com",
    }
    origin = tmp_path / "origin"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=env)
    for name, text in (base_files or {"README.md": "base\n"}).items():
        (repo / name).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True, env=env)
    base = _git(repo, "rev-parse", "HEAD")
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "change"], check=True, env=env)
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True, env=env)
    _git(repo, "remote", "add", "origin", str(origin))
    env |= {
        "EVENT": "push",
        "BEFORE": base,
        "PR_BASE": "",
        "PR_NUMBER": "",
        "DEFAULT_BRANCH": "main",
        "GITHUB_WORKSPACE": str(repo),
    }
    return subprocess.run(
        ["sh", "-c", _script()], check=False, cwd=repo, env=env, capture_output=True, text=True
    )


REFUSED = {
    "vendor shape": ("src/app.py", 'KEY = "ghp_' + "a" * 36 + '"\n'),
    "or fallback": (
        "src/db.js",
        "password: process.env.PGPASSWORD || 'rotv',\n",  # gatehouse: allow-secret
    ),
    "nullish fallback": (
        "src/db.ts",
        "const s = process.env.SESSION_SECRET ?? 'hunter22';\n",  # gatehouse: allow-secret
    ),
    "placeholder fallback": (
        "server.js",
        "secret: process.env.SESSION_SECRET || 'change-this',\n",  # gatehouse: allow-secret
    ),
    "shell default": (
        "run.sh",
        'psql "postgres://rotv:${PGPASSWORD:-rotv}@db/rotv"\n',  # gatehouse: allow-secret
    ),
    "getenv default": (
        "src/config.py",
        'pw = os.environ.get("PGPASSWORD", "imageserver")\n',  # gatehouse: allow-secret
    ),
    "fallback in a test": (
        "tests/db.test.js",
        "password: process.env.PGPASSWORD || 'rotv',\n",  # gatehouse: allow-secret
    ),
    "quoted assignment": (
        "src/settings.py",
        'db_password = "s3cr3t-value"\n',  # gatehouse: allow-secret
    ),
    "json key": (
        "backup/dump.sql",
        '{"refresh_token": "1//0abcdefghij"}\n',  # gatehouse: allow-secret
    ),
    "env file": (".env.test", "PGPASSWORD=rotv\n"),
    "literal password": (
        "src/setup.mjs",
        "const DEFAULT_PASSWORD = 'password';\n",  # gatehouse: allow-secret
    ),
    "compose list": ("compose.yaml", "    environment:\n      - POSTGRES_PASSWORD=rotv\n"),
    "yaml quoted": ("deploy/app.yml", 'API_TOKEN: "abc123def"\n'),  # gatehouse: allow-secret
    "unit environment": ("rotv.container", "Environment=PGPASSWORD=rotv\n"),
    "quoted unit environment": ("rotv.service", 'Environment="PGPASSWORD=rotv"\n'),
    "containerfile env": ("Containerfile", "ENV PGPASSWORD=rotv\n"),
    "suffixed containerfile": ("app.Containerfile", "ARG PGPASSWORD=rotv\n"),
    "podman args": ("rotv.container", "PodmanArgs=--env PGPASSWORD=rotv\n"),
    "placeholder word inside": (
        "src/settings.py",
        'api_key = "myexample-prod-key-9f3a"\n',  # gatehouse: allow-secret
    ),
    "x run inside": (
        "src/settings.py",
        'api_key = "xxxREALSECRETxxx"\n',  # gatehouse: allow-secret
    ),
    "vendor shape in a doc": ("README.md", "Use `ghp_" + "a" * 36 + "`.\n"),
}

PASSED = {
    "env read only": ("src/db.js", "password: process.env.PGPASSWORD,\n"),
    "empty default": ("src/config.py", 'pw = os.environ.get("PGPASSWORD", "")\n'),
    "changeme": ("src/settings.py", 'db_password = "changeme"\n'),
    "angle placeholder": ("deploy/app.yml", "API_TOKEN: <your token>\n"),
    "template": ("deploy/app.yml", 'password: "${DB_PASSWORD}"\n'),
    "actions expression": (".github/workflows/ci.yml", "  token: ${{ secrets.GH_TOKEN }}\n"),
    "file path key": ("src/settings.py", 'TOKEN_FILE = "/run/secrets/token"\n'),
    "boolean": (".env", "REQUIRE_PASSPHRASE=yes\n"),
    "env name in code": ("src/app.py", "GITLAB_TOKEN: Required. GitLab Personal Access Token.\n"),
    "allow comment": ("src/settings.py", 'db_password = "s3cr3t"  # gatehouse: allow-secret\n'),
    "secrets plural": (".github/workflows/ci.yml", "    secrets: inherit\n"),
    "shell reference": (".github/workflows/ci.yml", '        -e GITHUB_TOKEN="$GH_TOKEN" \\\n'),
    "ci fake": ("pyproject.toml", 'env = { GITLAB_TOKEN = "test_token_for_ci" }\n'),
    "field name": ("src/shapes.py", '    STATIC_TOKEN = "static_token"\n'),
    "mask": ("src/Settings.jsx", "const MASKED_SECRET = '••••••';\n"),
    "switch fallback": ("src/reconnect.py", 'r = env.get("RELAY_REQUIRE_PASSPHRASE", "yes")\n'),
    "unset sentinel": ("run.sh", 'TOKEN="${TOKEN:-unset}"\n'),
    "missing sentinel": ("run.sh", ': "${VAULT_TOKEN:-missing}"\n'),
    "not set sentinel": ("src/config.py", 'pw = os.environ.get("PGPASSWORD", "not-set")\n'),
    "placeholder word": (
        ".github/workflows/ci.yml",
        "      boot-env: |\n        EXAMPLE_API_KEY=gate-dummy\n",
    ),
    "change this, with a suffix": (
        "deploy/app.env",
        "SESSION_SECRET=change-this-to-a-random-string\n",
    ),
    "own name": ("src/consts.py", 'TOKEN = "token"\n'),
    "own name, any case": ("src/locales/en.json", '{"password": "Password"}\n'),
    "systemd credential": ("app.container", "Environment=API_TOKEN=%d/api_token\n"),
    "systemd specifier in a unit": ("app.service", "Environment=PGPASSFILE=%h/.pgpass\n"),
    "systemd specifier in a drop-in": (
        "app.service.d/override.conf",
        "Environment=API_TOKEN=%d/api_token\n",
    ),
}


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("src/settings.py", 'password = "%Kq9vLm2Xz8"\n'),  # gatehouse: allow-secret
        (".env", "PGPASSWORD=%Kq9vLm2Xz8\n"),  # gatehouse: allow-secret
        ("app.container", "Environment=API_TOKEN=%Kq9vLm2Xz8\n"),  # gatehouse: allow-secret
        ("app.service", "Environment=API_TOKEN=%dq9vLm2Xz8\n"),  # gatehouse: allow-secret
    ],
)
def test_a_percent_literal_that_is_no_specifier_is_refused(tmp_path, name, text):
    result = _scan(tmp_path, {name: text})
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"::error file={name},line=1::" in result.stdout


@pytest.mark.parametrize("case", REFUSED)
def test_refused(tmp_path, case):
    name, text = REFUSED[case]
    result = _scan(tmp_path, {name: text})
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"::error file={name}," in result.stdout


@pytest.mark.parametrize("case", PASSED)
def test_passed(tmp_path, case):
    name, text = PASSED[case]
    result = _scan(tmp_path, {name: text})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "::error" not in result.stdout
    assert "::warning" not in result.stdout


@pytest.mark.parametrize(
    "name",
    [
        "tests/test_auth.py",
        "backend/tests/x.js",
        "src/auth.test.ts",
        "conftest.py",
        "pkg/x_test.go",
    ],
)
def test_named_literal_in_a_test_only_warns(tmp_path, name):
    result = _scan(tmp_path, {name: 'access_token = "tok123"\n'})  # gatehouse: allow-secret
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"::warning file={name},line=1::" in result.stdout
    assert "::error" not in result.stdout


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("README.md", 'password: "hunter2"\n'),  # gatehouse: allow-secret
        ("docs/setup.txt", "pw = process.env.PGPASSWORD || 'rotv'\n"),  # gatehouse: allow-secret
        (".env.example", "SESSION_SECRET=s3cr3t-value\n"),  # gatehouse: allow-secret
        ("config.yml.sample", 'api_token: "abc123def"\n'),  # gatehouse: allow-secret
    ],
)
def test_prose_about_a_secret_only_warns(tmp_path, name, text):
    result = _scan(tmp_path, {name: text})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"::warning file={name},line=1::" in result.stdout
    assert "::error" not in result.stdout


def test_an_added_line_that_looks_like_a_header_is_read(tmp_path):
    result = _scan(tmp_path, {".env": "++ counter\nPGPASSWORD=rotv\n"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::error file=.env,line=2::plaintext secret in PGPASSWORD" in result.stdout


def test_a_unit_hit_names_the_variable(tmp_path):
    result = _scan(tmp_path, {"rotv.container": 'Environment="PGPASSWORD=rotv"\n'})
    assert "::error file=rotv.container,line=1::plaintext secret in PGPASSWORD" in result.stdout


def test_each_file_is_classified_on_its_own(tmp_path):
    result = _scan(
        tmp_path,
        {
            "README.md": 'password: "hunter2"\n',  # gatehouse: allow-secret
            "src/settings.py": 'password = "hunter2"\n',  # gatehouse: allow-secret
            "tests/test_x.py": 'password = "hunter2"\n',  # gatehouse: allow-secret
        },
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "::warning file=README.md,line=1::" in result.stdout
    assert "::error file=src/settings.py,line=1::plaintext secret in password" in result.stdout
    assert "::warning file=tests/test_x.py,line=1::" in result.stdout


def test_a_hit_names_the_key_and_line_never_the_value(tmp_path):
    result = _scan(
        tmp_path, {"compose.yaml": "services:\n  db:\n    POSTGRES_PASSWORD: hunter22\n"}
    )
    assert result.returncode == 1
    assert (
        "::error file=compose.yaml,line=3::plaintext secret in POSTGRES_PASSWORD" in result.stdout
    )
    assert "hunter22" not in result.stdout + result.stderr


def test_only_added_lines_are_read(tmp_path):
    committed = "PGPASSWORD=rotv\n"  # gatehouse: allow-secret
    result = _scan(tmp_path, {".env": committed + "PGHOST=db\n"}, base_files={".env": committed})
    assert result.returncode == 0
    assert "No plaintext secrets" in result.stdout
