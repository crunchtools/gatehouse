"""Shared fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _no_user_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the CLI from loading the developer's own gatehouse.env.

    A seat whose env file sets GATEHOUSE_LLM_BASE_URL would otherwise leak it
    into every test that runs after the first CLI test.
    """
    monkeypatch.setattr("gatehouse.cli.ENV_FILE", tmp_path / "gatehouse.env")


@pytest.fixture(autouse=True)
def _no_repo_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test outside the checkout, so this repo's own ledger stays out.

    A local review reads gatehouse-findings.toml from the working directory;
    from the repository root, every entry judged here was a finding the
    review tests did not expect.
    """
    monkeypatch.chdir(tmp_path)
