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
- the Claude Code server's URL is exactly one userConfig reference and its Authorization header exactly
  `Bearer` and one, each naming a declared option, and it sends no other header; the token's option is
  sensitive and has no default, so no credential sits in the repo;
- the Codex server's literal URL is an absolute http(s) URL with no credentials, query or fragment, and plain
  http only to loopback;
- the Codex server reads its token from an environment variable and carries no literal token, no
  `http_headers` (sent as written) and no `headers` block (ignored by Codex, so a token there would
  silently not be sent);
- a plugin submitted to Anthropic's plugin directory holds only what the directory takes from an
  organization that is not Verified: skills, no server, no hooks, and no file that is not text or an image.

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
BEARER_REF = re.compile(r"Bearer \$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}")

# Plain http carries the bearer token in cleartext, which is acceptable only when it never leaves the machine
# (the SSH-tunnel address).
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# MCP_APPROVAL_TTL accepts 1m to 30m. A client that gives up first cancels the approval request.
LONGEST_APPROVAL_WINDOW_SEC = 30 * 60

# The plugins submitted to Anthropic's plugin directory. From an organization that is not Verified, the
# directory refuses a plugin that defines a server running on the user's computer, or that holds a
# program or any file that is not text or an image (its findings LOCAL_CODE_MCP_SERVER and
# LOCAL_CODE_FILE_TYPE), and it refuses a server whose url the user supplies. A plugin named here is
# held to that, so that a change the directory would refuse fails here first.
DIRECTORY_PLUGINS = {"clustercontrol-mcp-setup"}
TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico"}
TEXT_NAMES = {"LICENSE", ".gitignore"}
PACKAGE_MANAGER_FILES = {
    "package.json", "package-lock.json", "npm-shrinkwrap.json", "pnpm-workspace.yaml", "pnpm-lock.yaml",
    "yarn.lock", ".npmrc", "bun.lock", "bun.lockb", "bunfig.toml", "uv.toml", "uv.lock", "pyproject.toml",
}

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


def check_directory_plugin(where, folder, manifest):
    """Hold a plugin to what the directory takes from an organization that is not Verified."""
    if (folder / ".mcp.json").exists() or "mcpServers" in manifest or "lspServers" in manifest:
        problem(f"{where}: must not define an MCP server; the directory refuses one that runs on the user's "
                f"computer, and a ClusterControl site's own server has no public address to name")
    if (folder / "hooks").exists() or "hooks" in manifest:
        problem(f"{where}: must not have hooks; a hook runs a command on the user's computer")
    skills = sorted((folder / "skills").glob("*/SKILL.md"))
    if not skills:
        problem(f"{where}: needs at least one skill, skills/<name>/SKILL.md")
    for skill in skills:
        text = skill.read_text(encoding="utf-8", errors="replace")
        front = text.split("---", 2)[1] if text.startswith("---") and text.count("---") >= 2 else ""
        if not re.search(r"^description:\s*\S", front, re.M):
            problem(f"{where}: {skill.relative_to(folder)} needs a description in its front matter")
    icon = manifest.get("icon")
    icon_path = (folder / icon).resolve() if isinstance(icon, str) else None
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder)
        suffix = path.suffix.lower()
        if path.name in PACKAGE_MANAGER_FILES:
            problem(f"{where}: {rel} is a package manager file, which the directory refuses")
            continue
        if suffix in IMAGE_SUFFIXES:
            continue
        # The icon may be an SVG, because an SVG is text. The directory refuses any other one.
        is_svg_icon = suffix == ".svg" and path.resolve() == icon_path
        if suffix not in TEXT_SUFFIXES and path.name not in TEXT_NAMES and not is_svg_icon:
            problem(f"{where}: {rel} has a file type the directory refuses; it takes Markdown, text, JSON, YAML, "
                    f"and PNG, JPEG, GIF, WebP or ICO images")
            continue
        data = path.read_bytes()
        if data.startswith(b"#!"):
            problem(f"{where}: {rel} starts with #!, which marks a program")
        if b"\x00" in data:
            problem(f"{where}: {rel} holds a NUL byte, so it is not text")
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                problem(f"{where}: {rel} is not UTF-8 text")


def check_claude(name, folder):
    manifest = load_json(folder / ".claude-plugin" / "plugin.json")
    if not isinstance(manifest, dict):
        return None, {}
    where = f"claude/{name}"
    if manifest.get("name") != name:
        problem(f"{where}: plugin.json name {manifest.get('name')!r} differs from the marketplace entry")
    if name in DIRECTORY_PLUGINS:
        check_directory_plugin(where, folder, manifest)
        return manifest.get("version"), {}
    servers = servers_of(load_json(folder / ".mcp.json"))
    options = manifest.get("userConfig", {})
    for server, cfg in servers.items():
        # Whole-value matches, not substring tests: text beside a reference is a literal shipped in the repo.
        url_ref = USER_CONFIG_REF.fullmatch(cfg.get("url", ""))
        if not url_ref:
            problem(f"{where}: server {server!r} url must be exactly ${{user_config.<option>}}")
        elif url_ref.group(1) not in options:
            problem(f"{where}: server {server!r} url references undeclared userConfig option {url_ref.group(1)!r}")
        headers = cfg.get("headers", {})
        # Only the Authorization value is checked for a reference below, so any other header would be a place
        # a literal could ship unchecked.
        if set(headers) - {"Authorization"}:
            problem(f"{where}: server {server!r} headers may hold only Authorization, not "
                    f"{sorted(set(headers) - {'Authorization'})}")
        token_ref = BEARER_REF.fullmatch(headers.get("Authorization", ""))
        if not token_ref:
            problem(f"{where}: server {server!r} Authorization header must be exactly Bearer ${{user_config.<option>}}")
        elif token_ref.group(1) not in options:
            problem(f"{where}: server {server!r} Authorization header references undeclared userConfig option "
                    f"{token_ref.group(1)!r}")
        else:
            key = token_ref.group(1)
            if not options[key].get("sensitive"):
                problem(f"{where}: userConfig {key!r} carries the token and must be sensitive")
            # sensitive governs where the client stores the value; a default would still be published here.
            if options[key].get("default") not in (None, ""):
                problem(f"{where}: userConfig {key!r} carries the token and must not have a default")
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
