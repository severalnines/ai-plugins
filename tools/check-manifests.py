#!/usr/bin/env python3
"""Consistency checks across the Claude Code and Codex plugin manifests.

`claude plugin validate` checks the Claude Code side on its own. Codex has no validate command, and
nothing else checks that the two plugins of one product stay in step, so this script does:

- every manifest and MCP file parses as JSON;
- each marketplace entry points at an existing folder whose manifest has the same name;
- a plugin's two manifests carry the same version, and neither marketplace entry sets one, so users are
  moved by the plugin.json version alone;
- each pair of server definitions has the same server names and the same timeout, and that timeout outlasts
  the longest approval window cmon-mcp allows (MCP_APPROVAL_TTL is at most 30 minutes);
- the Claude Code server takes its URL and token from userConfig, so no credential sits in the repo;
- the Codex server reads its token from an environment variable and carries no literal token or `headers`
  block: Codex ignores `headers`, so a token placed there would silently not be sent.

Exit status: 0 when every check passes, 1 when any fails.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"

# MCP_APPROVAL_TTL accepts 1m to 30m. A client that gives up first cancels the approval request.
LONGEST_APPROVAL_WINDOW_SEC = 30 * 60

problems = []


def problem(message):
    problems.append(message)


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problem(f"{path.relative_to(ROOT)}: missing")
    except json.JSONDecodeError as err:
        problem(f"{path.relative_to(ROOT)}: invalid JSON: {err}")
    return None


def servers_of(mcp_doc):
    if not isinstance(mcp_doc, dict):
        return {}
    servers = mcp_doc.get("mcpServers", mcp_doc)
    return servers if isinstance(servers, dict) else {}


def marketplace_folders(path, codex):
    """Map plugin name -> folder, checking each entry on the way."""
    doc = load_json(path)
    folders = {}
    if not isinstance(doc, dict):
        return folders
    for entry in doc.get("plugins", []):
        name = entry.get("name")
        where = f"{path.relative_to(ROOT)}: plugin {name!r}"
        if "version" in entry:
            problem(f"{where}: sets a version; keep it only in the plugin's plugin.json")
        source = entry.get("source")
        if codex:
            if not (isinstance(source, dict) and source.get("source") == "local"):
                problem(f"{where}: source must be {{\"source\": \"local\", \"path\": ...}}")
                continue
            source = source.get("path")
        if not isinstance(source, str) or not source.startswith("./"):
            problem(f"{where}: source must be a ./ path inside the repository")
            continue
        folder = (ROOT / source).resolve()
        if not folder.is_dir():
            problem(f"{where}: source folder {source} does not exist")
            continue
        folders[name] = folder
    return folders


def check_claude(name, folder):
    manifest = load_json(folder / ".claude-plugin" / "plugin.json")
    servers = servers_of(load_json(folder / ".mcp.json"))
    if not isinstance(manifest, dict):
        return None, {}
    where = f"claude/{name}"
    if manifest.get("name") != name:
        problem(f"{where}: plugin.json name {manifest.get('name')!r} differs from the marketplace entry")
    options = manifest.get("userConfig", {})
    for server, cfg in servers.items():
        url = cfg.get("url", "")
        if not url.startswith("${user_config."):
            problem(f"{where}: server {server!r} url must come from userConfig")
        auth = cfg.get("headers", {}).get("Authorization", "")
        if "${user_config." not in auth:
            problem(f"{where}: server {server!r} Authorization header must come from userConfig")
        for key in [k for k in options if f"${{user_config.{k}}}" in auth]:
            if not options[key].get("sensitive"):
                problem(f"{where}: userConfig {key!r} carries the token and must be sensitive")
        timeout = cfg.get("timeout")
        if not isinstance(timeout, int) or timeout <= LONGEST_APPROVAL_WINDOW_SEC * 1000:
            problem(f"{where}: server {server!r} timeout {timeout!r} ms must exceed {LONGEST_APPROVAL_WINDOW_SEC * 1000}")
    return manifest.get("version"), {s: c.get("timeout") for s, c in servers.items()}


def check_codex(name, folder):
    manifest = load_json(folder / ".codex-plugin" / "plugin.json")
    if not isinstance(manifest, dict):
        return None, {}
    where = f"codex/{name}"
    if manifest.get("name") != name:
        problem(f"{where}: plugin.json name {manifest.get('name')!r} differs from the marketplace entry")
    mcp_path = manifest.get("mcpServers", "./.mcp.json")
    if not isinstance(mcp_path, str):
        problem(f"{where}: mcpServers must name a file, so the definitions stay reviewable on their own")
        return manifest.get("version"), {}
    servers = servers_of(load_json((folder / mcp_path).resolve()))
    for server, cfg in servers.items():
        if "bearer_token" in cfg:
            problem(f"{where}: server {server!r} has a literal bearer_token; use bearer_token_env_var")
        if "headers" in cfg:
            problem(f"{where}: server {server!r} has a headers block, which Codex ignores")
        if not cfg.get("bearer_token_env_var"):
            problem(f"{where}: server {server!r} needs bearer_token_env_var")
        timeout = cfg.get("tool_timeout_sec")
        if not isinstance(timeout, (int, float)) or timeout <= LONGEST_APPROVAL_WINDOW_SEC:
            problem(f"{where}: server {server!r} tool_timeout_sec {timeout!r} must exceed {LONGEST_APPROVAL_WINDOW_SEC}")
    return manifest.get("version"), {
        s: (c["tool_timeout_sec"] * 1000 if isinstance(c.get("tool_timeout_sec"), (int, float)) else None)
        for s, c in servers.items()
    }


def main():
    claude = marketplace_folders(CLAUDE_MARKETPLACE, codex=False)
    codex = marketplace_folders(CODEX_MARKETPLACE, codex=True)
    # A plugin for one tool only is allowed; the cross-tool checks run only for plugins in both.
    for name in sorted(set(claude) | set(codex)):
        claude_version, claude_timeouts = check_claude(name, claude[name]) if name in claude else (None, None)
        codex_version, codex_timeouts = check_codex(name, codex[name]) if name in codex else (None, None)
        if name in claude and name in codex:
            if claude_version != codex_version:
                problem(f"{name}: version differs: Claude Code {claude_version!r}, Codex {codex_version!r}")
            if set(claude_timeouts) != set(codex_timeouts):
                problem(f"{name}: server names differ: Claude Code {sorted(claude_timeouts)}, Codex {sorted(codex_timeouts)}")
            for server in set(claude_timeouts) & set(codex_timeouts):
                if claude_timeouts[server] != codex_timeouts[server]:
                    problem(f"{name}: server {server!r} timeout differs: Claude Code {claude_timeouts[server]} ms, "
                            f"Codex {codex_timeouts[server]} ms")
    if problems:
        for message in problems:
            print(f"FAIL {message}")
        return 1
    print(f"OK {len(set(claude) | set(codex))} plugin(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
