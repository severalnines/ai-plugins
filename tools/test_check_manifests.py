#!/usr/bin/env python3
"""Planted-defect tests for check-manifests.py.

Each case copies the repository, plants one defect, and runs the checker on the copy. A defect the checker
lets through is a gap in the one check that keeps credentials out of this public repository, so every case
here must fail the check, and the unchanged copy must pass it.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MCP = "claude/clustercontrol-mcp/.mcp.json"
CLAUDE_MANIFEST = "claude/clustercontrol-mcp/.claude-plugin/plugin.json"
CODEX_MCP = "codex/clustercontrol-mcp/.mcp.json"
CODEX_MANIFEST = "codex/clustercontrol-mcp/.codex-plugin/plugin.json"


def edit(rel, change):
    def apply(root):
        path = root / rel
        doc = json.loads(path.read_text())
        change(doc)
        path.write_text(json.dumps(doc, indent=2))
    return apply


def claude_server(change):
    return edit(CLAUDE_MCP, lambda d: change(d["mcpServers"]["clustercontrol"]))


def codex_server(change):
    return edit(CODEX_MCP, lambda d: change(d["mcpServers"]["clustercontrol"]))


def claude_header(value):
    return claude_server(lambda s: s["headers"].update(Authorization=value))


class CheckManifestsTest(unittest.TestCase):
    def run_checker(self, plant):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "repo"
            shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            plant(copy)
            return subprocess.run([sys.executable, "-I", str(copy / "tools" / "check-manifests.py")],
                                  capture_output=True, text=True)

    def assert_fails(self, plant, expect):
        result = self.run_checker(plant)
        self.assertEqual(result.returncode, 1, f"defect not caught:\n{result.stdout}")
        self.assertIn(expect, result.stdout)

    def test_unchanged_repository_passes(self):
        result = self.run_checker(lambda root: None)
        self.assertEqual(result.returncode, 0, result.stdout)

    # Credentials in the Claude Code plugin.

    def test_literal_text_beside_the_token_reference(self):
        self.assert_fails(claude_header("Bearer planted-token-${user_config.mcp_token}"),
                          "must be exactly")

    def test_header_reference_to_an_undeclared_option(self):
        self.assert_fails(claude_header("Bearer ${user_config.missing_token}"), "undeclared userConfig option")

    def test_token_option_not_sensitive(self):
        self.assert_fails(edit(CLAUDE_MANIFEST, lambda d: d["userConfig"]["mcp_token"].update(sensitive=False)),
                          "must be sensitive")

    def test_token_option_with_a_default(self):
        self.assert_fails(edit(CLAUDE_MANIFEST, lambda d: d["userConfig"]["mcp_token"].update(default="planted")),
                          "must not have a default")

    def test_literal_url(self):
        self.assert_fails(claude_server(lambda s: s.update(url="https://cc.example.com/mcp")),
                          "url must be exactly")

    # Credentials in the Codex plugin.

    def test_codex_literal_bearer_token(self):
        self.assert_fails(codex_server(lambda s: s.update(bearer_token="planted")), "literal bearer_token")

    def test_codex_headers_block(self):
        self.assert_fails(codex_server(lambda s: s.update(headers={"Authorization": "Bearer planted"})),
                          "headers block")

    # Keeping the two plugins in step.

    def test_version_mismatch(self):
        self.assert_fails(edit(CODEX_MANIFEST, lambda d: d.update(version="9.9.9")), "version differs")

    def test_version_in_a_marketplace_entry(self):
        self.assert_fails(edit(".claude-plugin/marketplace.json", lambda d: d["plugins"][0].update(version="1.0.0")),
                          "sets a version")

    def test_claude_timeout_below_the_longest_approval_window(self):
        self.assert_fails(claude_server(lambda s: s.update(timeout=300000)), "must exceed 1800000")


if __name__ == "__main__":
    unittest.main()
