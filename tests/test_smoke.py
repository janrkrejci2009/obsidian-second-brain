"""Smoke tests for the two highest-risk subsystems: the adapter build pipeline
and the vault health checker. Both run the real scripts via subprocess and only
depend on the Python standard library, so CI needs nothing beyond pytest.

Adapted from the test added by the bmassenz fork (the only fork that shipped
any automated test). See FORK_INSIGHTS.md items #47/#48.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _json_from_stdout(stdout: str) -> dict:
    """vault_health.py prints a couple of human-readable lines before the JSON
    payload even in --json mode. Scan for the first line that opens the object."""
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "{":
            return json.loads("\n".join(lines[index:]))
    raise AssertionError(f"JSON payload not found in stdout:\n{stdout}")


def test_codex_cli_build_generates_expected_files():
    """The codex-cli adapter must emit the AGENTS.md dispatcher and the command
    bodies. This guards the adapter pipeline that every command change depends on."""
    result = subprocess.run(
        ["bash", "scripts/build.sh", "--platform", "codex-cli"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (REPO_ROOT / "dist/codex-cli/AGENTS.md").is_file()
    assert (REPO_ROOT / "dist/codex-cli/.codex/commands/obsidian-save.md").is_file()


def test_vault_health_json_reports_clean_linked_vault(tmp_path):
    """A minimal two-note vault with reciprocal wikilinks should report zero
    issues: no orphans, no broken links, no missing frontmatter."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Home.md").write_text(
        "# Home\n\nSee [[Project Alpha]].\n",
        encoding="utf-8",
    )
    (vault / "Project Alpha.md").write_text(
        "---\n"
        "type: project\n"
        "aliases:\n"
        "  - Project Alpha\n"
        "---\n"
        "# Project Alpha\n\nBack to [[Home]].\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/vault_health.py", "--path", str(vault), "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = _json_from_stdout(result.stdout)
    assert payload["total_notes"] == 2
    assert payload["total_issues"] == 0
    assert payload["counts"]["Broken links"] == 0
    assert payload["counts"]["Orphans"] == 0


def _broken_link_messages(vault: Path) -> list[str]:
    """Run the health check on ``vault`` and return the broken-link messages."""
    result = subprocess.run(
        [sys.executable, "scripts/vault_health.py", "--path", str(vault), "--json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = _json_from_stdout(result.stdout)
    return [i["message"] for i in payload["issues"] if i["type"] == "broken_link"]


def test_broken_link_scan_resolves_case_and_alias_but_flags_genuine(tmp_path):
    """The broken-link scan must (a) resolve case-divergent links, (b) resolve
    links that match a frontmatter alias, and (c) still flag a link whose target
    does not exist. Mirrors the vault_health false-positive acceptance criteria."""
    vault = tmp_path / "vault"
    vault.mkdir()
    knowledge = vault / "Knowledge"
    knowledge.mkdir()

    # Target note with a different filename case and a frontmatter alias.
    (knowledge / "Curador.md").write_text(
        "---\n"
        "type: knowledge\n"
        "aliases:\n"
        "  - Mercado Livre Full\n"
        "---\n"
        "# Curador\n",
        encoding="utf-8",
    )

    # Source note exercising all three cases.
    (vault / "Notes.md").write_text(
        "---\n"
        "type: note\n"
        "---\n"
        "# Notes\n\n"
        "Case-divergent: [[curador]].\n"          # resolves -> Curador.md
        "Via alias: [[Mercado Livre Full]].\n"     # resolves via alias
        "Genuinely missing: [[Nonexistent Note]].\n",  # must stay broken
        encoding="utf-8",
    )

    messages = _broken_link_messages(vault)

    # (a) case-divergent and (b) alias links must NOT be reported.
    assert not any("[[curador]]" in m for m in messages), messages
    assert not any("Mercado Livre Full" in m for m in messages), messages
    # (c) the genuinely broken link must still be reported.
    assert any("[[Nonexistent Note]]" in m for m in messages), messages


def test_broken_link_scan_excludes_meta_files_by_default(tmp_path):
    """Meta files (log.md, Reviews/Saúde do Vault*.md) only echo links as content;
    they must be excluded from the broken-link scan by default, while the same
    broken link in a regular note is still reported."""
    vault = tmp_path / "vault"
    vault.mkdir()
    reviews = vault / "Reviews"
    reviews.mkdir()

    (vault / "log.md").write_text("# Log\n\n[[Ghost Note]] mentioned here.\n", encoding="utf-8")
    (reviews / "Saúde do Vault 2026-06-07.md").write_text(
        "---\ntype: review\n---\n# Health\n\n[[Ghost Note]] was flagged.\n",
        encoding="utf-8",
    )
    (vault / "Real Note.md").write_text(
        "---\ntype: note\n---\n# Real\n\nSee [[Ghost Note]].\n",
        encoding="utf-8",
    )

    messages = _broken_link_messages(vault)

    # The meta files are excluded; only the regular note reports the broken link.
    assert any("Real Note.md" in m for m in messages), messages
    assert not any("log.md" in m for m in messages), messages
    assert not any("Saúde do Vault" in m for m in messages), messages


def test_substitution_check_passes_on_repo():
    """The repo source must be free of banned substitution characters in prose
    (the CI gate). Characters inside code fences/spans are allowed."""
    result = subprocess.run(
        [sys.executable, "scripts/sweep_non_ascii.py", "--check"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_substitution_check_flags_prose_em_dash(tmp_path):
    """--check must fail (exit 1) when a banned character appears in prose, and
    must NOT fail when it only appears inside an inline code span."""
    # Build the em-dash from its code point so this test's own source stays
    # ASCII (the CI gate scans .py files too); the written fixtures get the
    # real character.
    em = "\u2014"
    bad = tmp_path / "bad.md"
    bad.write_text(f"A prose line with an em{em}dash.\n", encoding="utf-8")
    flagged = subprocess.run(
        [sys.executable, "scripts/sweep_non_ascii.py", "--check", str(bad)],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
    assert flagged.returncode == 1, flagged.stdout

    ok = tmp_path / "ok.md"
    ok.write_text(f"A filename in code: `2026-01-01 {em} note.md` is fine.\n", encoding="utf-8")
    passed = subprocess.run(
        [sys.executable, "scripts/sweep_non_ascii.py", "--check", str(ok)],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
    assert passed.returncode == 0, passed.stdout


def test_architect_scan_emits_manifest(tmp_path):
    """architect_scan.py must produce a JSON manifest with the expected shape
    on a minimal project (no network, no install)."""
    proj = tmp_path / "proj"
    (proj / "src" / "billing").mkdir(parents=True)
    (proj / "src" / "billing" / "charge.py").write_text("def charge():\n    pass\n", encoding="utf-8")
    (proj / "pyproject.toml").write_text(
        '[project]\nname = "paymentbot"\ndependencies = ["requests"]\n', encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, "scripts/architect_scan.py", "--path", str(proj)],
        cwd=REPO_ROOT, check=False, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    data = _json_from_stdout(result.stdout)
    assert data["name"] == "paymentbot"
    assert data["kind"] == "python"
    assert any(m["name"] == "billing" for m in data["modules"])
    assert "requests" in data["dependencies"]
    assert any(lang["language"] == "Python" for lang in data["languages"])
