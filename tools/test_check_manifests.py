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


SETUP = "claude/clustercontrol-mcp-setup"
SETUP_MANIFEST = f"{SETUP}/.claude-plugin/plugin.json"


def setup_file(rel, content):
    """Plant a file in the setup plugin; `content` is text or bytes."""
    def apply(root):
        path = root / SETUP / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return apply


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

    # The setup plugin is the one submitted to Anthropic's plugin directory. From an organization that is
    # not Verified, the directory refuses a plugin with a server that runs on the user's computer, with
    # a program of any kind, or with a file that is not text or an image. Each case below was a refusal
    # there, or would be one.

    def test_setup_plugin_with_a_server_file(self):
        self.assert_fails(setup_file(".mcp.json", '{"mcpServers": {"x": {"command": "node", "args": ["x.mjs"]}}}'),
                          "must not define an MCP server")

    def test_setup_plugin_with_a_server_in_its_manifest(self):
        self.assert_fails(edit(SETUP_MANIFEST, lambda d: d.update(mcpServers={"x": {"url": "https://cc.example.com/mcp"}})),
                          "must not define an MCP server")

    def test_setup_plugin_with_hooks(self):
        self.assert_fails(setup_file("hooks/hooks.json", '{"hooks": {}}'), "must not have hooks")

    def test_setup_plugin_with_a_script(self):
        self.assert_fails(setup_file("scripts/check.sh", "echo planted\n"), "file type")

    def test_setup_plugin_with_a_shebang_in_a_text_file(self):
        self.assert_fails(setup_file("skills/connect/notes.md", "#!/bin/sh\necho planted\n"), "starts with #!")

    def test_setup_plugin_with_a_binary_named_as_text(self):
        self.assert_fails(setup_file("skills/connect/notes.md", b"planted\x00binary"), "NUL byte")

    def test_setup_plugin_with_text_that_is_not_utf8(self):
        self.assert_fails(setup_file("skills/connect/notes.md", b"caf\xe9\n"), "not UTF-8")

    def test_setup_plugin_with_a_package_manager_file(self):
        self.assert_fails(setup_file("package.json", '{"name": "planted"}'), "package manager")

    def test_setup_plugin_without_a_skill(self):
        self.assert_fails(lambda root: shutil.rmtree(root / SETUP / "skills"), "needs at least one skill")

    def test_setup_plugin_skill_without_a_description(self):
        self.assert_fails(setup_file("skills/connect/SKILL.md", "---\nname: connect\n---\n\n# Connect\n"),
                          "needs a description")

    def test_setup_plugin_with_a_second_svg(self):
        # The icon may be an SVG, because it is text. Any other SVG is a file type the directory refuses.
        self.assert_fails(setup_file("diagram.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>\n"), "file type")

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

    def test_header_besides_authorization(self):
        self.assert_fails(claude_server(lambda s: s["headers"].update({"X-Api-Key": "planted"})),
                          "headers may hold only Authorization")

    def test_literal_url(self):
        self.assert_fails(claude_server(lambda s: s.update(url="https://cc.example.com/mcp")),
                          "url must be exactly")

    # Credentials in the Codex plugin.

    def test_codex_literal_bearer_token(self):
        self.assert_fails(codex_server(lambda s: s.update(bearer_token="planted")), "literal bearer_token")

    def test_codex_headers_block(self):
        self.assert_fails(codex_server(lambda s: s.update(headers={"Authorization": "Bearer planted"})),
                          "headers block")

    def test_codex_missing_url(self):
        self.assert_fails(codex_server(lambda s: s.pop("url")), "absolute http(s) URL")

    def test_codex_url_with_userinfo(self):
        self.assert_fails(codex_server(lambda s: s.update(url="https://admin:planted@cc.example.com/mcp")),
                          "must not carry credentials")

    def test_codex_url_with_query_token(self):
        self.assert_fails(codex_server(lambda s: s.update(url="https://cc.example.com/mcp?token=planted")),
                          "query string")

    def test_codex_plain_http_to_a_remote_host(self):
        self.assert_fails(codex_server(lambda s: s.update(url="http://cc.example.com:3000/mcp")),
                          "plain http only to loopback")

    def test_codex_literal_http_headers(self):
        self.assert_fails(codex_server(lambda s: s.update(http_headers={"Authorization": "Bearer planted"})),
                          "literal http_headers")

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
