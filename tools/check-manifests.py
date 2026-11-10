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
- the Claude Code server is the bundled bridge and nothing else: `node` with one argument, a file inside the
  plugin, and no `url` or `headers` (Anthropic's directory refuses a url it cannot read for certain, and every
  customer has their own);
- every value the Claude Code server passes to the bridge is exactly one userConfig reference to a declared
  option, the bridge reads each variable the server sets and the server sets each one the bridge reads, and
  the token's option is sensitive and has no default, so no credential sits in the repo;
- the Codex server's literal URL is an absolute http(s) URL with no credentials, query or fragment, and plain
  http only to loopback;
- the Codex server reads its token from an environment variable and carries no literal token, no
  `http_headers` (sent as written) and no `headers` block (ignored by Codex, so a token there would
  silently not be sent).

Exit status: 0 when every check passes, 1 when any fails.
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MARKETPLACE = ROOT / ".claude-plugin" / "marketplace.json"
CODEX_MARKETPLACE = ROOT / ".agents" / "plugins" / "marketplace.json"

# userConfig keys are letters, digits and underscores, not starting with a digit.
USER_CONFIG_REF = re.compile(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}")

# The Claude Code server runs a file of the plugin. Claude Code replaces this prefix with the folder the plugin
# is installed in.
PLUGIN_ROOT_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"
# Fields of a server that Claude Code would connect to by itself.
REMOTE_FIELDS = ("url", "headers", "headersHelper", "authServerMetadataUrl")
# An environment variable with one of these in its name carries a credential.
CREDENTIAL_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|KEY")
BRIDGE_READS = re.compile(r"process\.env\.([A-Z][A-Z0-9_]*)")

# Plain http carries the bearer token in cleartext, which is acceptable only when it never leaves the machine
# (the SSH-tunnel address).
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

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


def bridge_source(where, server, folder, args):
    """Return the text of the bridge file a Claude Code server runs, or None when its arguments are wrong."""
    if not (isinstance(args, list) and len(args) == 1 and isinstance(args[0], str)):
        problem(f"{where}: server {server!r} takes exactly one argument, the bridge file")
        return None
    if not args[0].startswith(PLUGIN_ROOT_PREFIX):
        problem(f"{where}: server {server!r} argument must start with {PLUGIN_ROOT_PREFIX}")
        return None
    path = (folder / args[0][len(PLUGIN_ROOT_PREFIX):]).resolve()
    # Users install the plugin folder alone, so a file outside it does not exist on their machine.
    if not path.is_relative_to(folder):
        problem(f"{where}: server {server!r} bridge file must be inside the plugin folder, not {args[0]}")
        return None
    if not path.is_file():
        problem(f"{where}: server {server!r} bridge file {args[0]} does not exist")
        return None
    return path.read_text(encoding="utf-8")


def check_claude(name, folder):
    manifest = load_json(folder / ".claude-plugin" / "plugin.json")
    servers = servers_of(load_json(folder / ".mcp.json"))
    if not isinstance(manifest, dict):
        return None, {}
    where = f"claude/{name}"
    if manifest.get("name") != name:
        problem(f"{where}: plugin.json name {manifest.get('name')!r} differs from the marketplace entry")
    options = manifest.get("userConfig", {})
    for key, option in options.items():
        # sensitive governs where the client stores the value; a default would still be published here.
        if option.get("sensitive") and option.get("default") not in (None, ""):
            problem(f"{where}: userConfig {key!r} is sensitive and must not have a default")
    for server, cfg in servers.items():
        for field in REMOTE_FIELDS:
            if field in cfg:
                problem(f"{where}: server {server!r} must not declare {field!r}; it runs the bundled bridge, "
                        f"which takes the address from the user's options")
        if cfg.get("command") != "node":
            problem(f"{where}: server {server!r} command must be 'node', not {cfg.get('command')!r}")
        bridge = bridge_source(where, server, folder, cfg.get("args"))
        env = cfg.get("env")
        if not isinstance(env, dict) or not env:
            problem(f"{where}: server {server!r} needs an env block that passes the user's options to the bridge")
            env = {}
        for var, value in env.items():
            # Whole-value matches, not substring tests: text beside a reference is a literal shipped in the repo.
            ref = USER_CONFIG_REF.fullmatch(value) if isinstance(value, str) else None
            if not ref:
                problem(f"{where}: server {server!r} env {var} must be exactly ${{user_config.<option>}}")
            elif ref.group(1) not in options:
                problem(f"{where}: server {server!r} env {var} references undeclared userConfig option "
                        f"{ref.group(1)!r}")
            elif CREDENTIAL_NAME.search(var) and not options[ref.group(1)].get("sensitive"):
                problem(f"{where}: userConfig {ref.group(1)!r} carries the token and must be sensitive")
            if bridge is not None and f"process.env.{var}" not in bridge:
                problem(f"{where}: server {server!r} sets {var}, which the bridge does not read")
        # A variable the bridge reads and the server does not set would be empty for every user.
        for var in sorted(set(BRIDGE_READS.findall(bridge or "")) - set(env)):
            problem(f"{where}: the bridge reads {var}, which server {server!r} does not set")
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
        # Codex cannot take the URL from the user, so it is a literal here and has to be checked as one.
        url = cfg.get("url")
        parts = urlsplit(url) if isinstance(url, str) else None
        if not parts or parts.scheme not in ("http", "https") or not parts.hostname:
            problem(f"{where}: server {server!r} url {url!r} must be an absolute http(s) URL")
        else:
            if parts.username or parts.password:
                problem(f"{where}: server {server!r} url must not carry credentials (user:password@)")
            if parts.query or parts.fragment:
                problem(f"{where}: server {server!r} url must not carry a query string or fragment")
            if parts.scheme == "http" and parts.hostname not in LOOPBACK_HOSTS:
                problem(f"{where}: server {server!r} url uses plain http only to loopback, not {parts.hostname!r}")
        if "bearer_token" in cfg:
            problem(f"{where}: server {server!r} has a literal bearer_token; use bearer_token_env_var")
        if "headers" in cfg:
            problem(f"{where}: server {server!r} has a headers block, which Codex ignores")
        # http_headers values are sent as written, so they would publish whatever they hold;
        # env_http_headers names environment variables instead and is the place for a secret header.
        if "http_headers" in cfg:
            problem(f"{where}: server {server!r} has literal http_headers; use bearer_token_env_var or env_http_headers")
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
