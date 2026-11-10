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


def claude_env(name, value):
    return claude_server(lambda s: s["env"].update({name: value}))


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
        self.assert_fails(claude_env("CLUSTERCONTROL_MCP_TOKEN", "planted-token-${user_config.mcp_token}"),
                          "must be exactly")

    def test_literal_env_value(self):
        self.assert_fails(claude_env("X_API_KEY", "planted"), "must be exactly")

    def test_env_reference_to_an_undeclared_option(self):
        self.assert_fails(claude_env("CLUSTERCONTROL_MCP_TOKEN", "${user_config.missing_token}"),
                          "undeclared userConfig option")

    def test_token_option_not_sensitive(self):
        self.assert_fails(edit(CLAUDE_MANIFEST, lambda d: d["userConfig"]["mcp_token"].update(sensitive=False)),
                          "must be sensitive")

    def test_token_option_with_a_default(self):
        self.assert_fails(edit(CLAUDE_MANIFEST, lambda d: d["userConfig"]["mcp_token"].update(default="planted")),
                          "must not have a default")

    # What the Claude Code plugin runs. Anthropic's directory refuses a server whose url it cannot read for
    # certain, and every customer has a url of their own, so the plugin declares none and runs the bridge.

    def test_remote_url(self):
        self.assert_fails(claude_server(lambda s: s.update(url="https://cc.example.com/mcp")),
                          "must not declare 'url'")

    def test_headers_block(self):
        self.assert_fails(claude_server(lambda s: s.update(headers={"Authorization": "Bearer planted"})),
                          "must not declare 'headers'")

    def test_command_other_than_node(self):
        self.assert_fails(claude_server(lambda s: s.update(command="npx")), "command must be 'node'")

    def test_argument_besides_the_bridge(self):
        self.assert_fails(claude_server(lambda s: s["args"].append("--planted")), "exactly one argument")

    def test_bridge_path_outside_the_plugin(self):
        self.assert_fails(claude_server(lambda s: s.update(args=["${CLAUDE_PLUGIN_ROOT}/../../tools/planted.mjs"])),
                          "inside the plugin")

    def test_bridge_file_missing(self):
        self.assert_fails(claude_server(lambda s: s.update(args=["${CLAUDE_PLUGIN_ROOT}/bridge/missing.mjs"])),
                          "does not exist")

    def test_env_name_the_bridge_does_not_read(self):
        def rename(server):
            server["env"]["CLUSTERCONTROL_TOKEN"] = server["env"].pop("CLUSTERCONTROL_MCP_TOKEN")
        self.assert_fails(claude_server(rename), "the bridge does not read")

    def test_variable_the_bridge_reads_and_the_server_does_not_set(self):
        self.assert_fails(claude_server(lambda s: s["env"].pop("CLUSTERCONTROL_MCP_URL")),
                          "the bridge reads CLUSTERCONTROL_MCP_URL")

    def test_no_env_block(self):
        self.assert_fails(claude_server(lambda s: s.pop("env")), "needs an env block")

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
