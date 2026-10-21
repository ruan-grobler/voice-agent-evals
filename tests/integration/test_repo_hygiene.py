"""Guards for the public repo: no secrets, only synthetic data, house style in docs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.helpers import ROOT
from voice_agent_evals.cassette import Cassette

pytestmark = pytest.mark.integration

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)
SKIP_DIRS = {".git", ".venv", "reports", ".mypy_cache", ".pytest_cache", ".ruff_cache", "htmlcov"}
SECRET_PATTERNS = {
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI style key": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def project_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not SKIP_DIRS & set(path.relative_to(ROOT).parts):
            files.append(path)
    return files


def test_no_secrets_in_the_working_tree() -> None:
    hits = []
    for path in project_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{path}: {name}" for name, rx in SECRET_PATTERNS.items() if rx.search(text)]
    assert hits == []


def test_env_example_holds_no_values() -> None:
    lines = (ROOT / ".env.example").read_text().splitlines()
    key_line = next(line for line in lines if line.startswith("ANTHROPIC_API_KEY"))
    assert key_line == "ANTHROPIC_API_KEY="
    tracked = subprocess.run(
        ["git", "ls-files", ".env"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert tracked.stdout.strip() == ""


def test_businesses_are_synthetic() -> None:
    for path in (ROOT / "businesses").glob("*.yaml"):
        data = yaml.safe_load(path.read_text())
        assert data["synthetic"] is True
        assert data["email"].endswith(".example.com")
        assert "(fictional)" in data["address"]


def test_phone_numbers_are_clearly_fake() -> None:
    phone = re.compile(r"\+27[\s\d]{9,14}")
    for path in project_files():
        if path.suffix not in {".yaml", ".json", ".md", ".txt"}:
            continue
        for match in phone.finditer(path.read_text(encoding="utf-8")):
            digits = re.sub(r"\D", "", match.group(0))[:11]
            assert digits.startswith("2700000"), f"{path}: {match.group(0)!r}"


def test_emails_are_example_domains() -> None:
    email = re.compile(r"[\w.+-]+@([\w-]+\.)+[a-z]{2,}")
    for path in project_files():
        if path.suffix not in {".yaml", ".json", ".md"}:
            continue
        for match in email.finditer(path.read_text(encoding="utf-8")):
            address = match.group(0)
            assert address.endswith(".example.com") or address == "noreply@anthropic.com", address


def test_docs_have_no_long_dashes() -> None:
    docs = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert EM_DASH not in text and EN_DASH not in text, path


def test_every_cassette_says_where_it_came_from() -> None:
    cassettes = sorted((ROOT / "cassettes").glob("v*/*.json"))
    assert len(cassettes) == 30
    for path in cassettes:
        cassette = Cassette.load(path)
        assert cassette.provenance in {"hand-crafted", "recorded"}
        assert cassette.note
