# Severalnines AI plugins

Plugins from [Severalnines](https://severalnines.com/) for Claude Code and Codex. The first one connects your
AI coding agent to [ClusterControl](https://severalnines.com/clustercontrol/), so it can inspect, troubleshoot
and operate the database clusters ClusterControl manages, through the ClusterControl MCP server (`cmon-mcp`)
that runs on your controller.

| Plugin | Claude Code | Codex |
|---|---|---|
| `clustercontrol-mcp` | [claude/clustercontrol-mcp](claude/clustercontrol-mcp/README.md) | [codex/clustercontrol-mcp](codex/clustercontrol-mcp/README.md) |
| `clustercontrol-mcp-setup` | [claude/clustercontrol-mcp-setup](claude/clustercontrol-mcp-setup/README.md) | |

`clustercontrol-mcp` is the connection. `clustercontrol-mcp-setup` is a guide: a skill with which Claude walks
you through setting that connection up. If you are reading this, you do not need the guide; it exists for
people who find ClusterControl in Anthropic's plugin directory.

## Install

Both plugins need `cmon-mcp` running in HTTP mode on the controller, with a bearer token. The plugin READMEs
cover that setup and how to reach the server securely.

**Claude Code**

```
/plugin marketplace add severalnines/ai-plugins
/plugin install clustercontrol-mcp@severalnines
```

**Codex**

```bash
codex plugin marketplace add severalnines/ai-plugins
codex plugin add clustercontrol-mcp@severalnines
```

## Repository layout

```
.claude-plugin/marketplace.json     Claude Code marketplace "severalnines"
.agents/plugins/marketplace.json    Codex marketplace "severalnines"
claude/clustercontrol-mcp/          Claude Code plugin: asks for the URL and token at install
claude/clustercontrol-mcp-setup/    Claude Code plugin: the setup guide, one skill and nothing else
codex/clustercontrol-mcp/           Codex plugin: tunnel URL, token from CLUSTERCONTROL_MCP_TOKEN
tools/check-manifests.py            consistency checks (CI), with planted-defect tests beside it
```

The two tools read different manifest and MCP formats, so each plugin has its own folder.

### Why there is a setup guide

Anthropic's plugin directory does not list `clustercontrol-mcp`. It refuses a server whose address the
user supplies, and every ClusterControl site has its own. It also refuses a server that runs on the
user's computer, and any program, unless the publisher is a Verified organization, which cannot be
applied for yet; a version of the plugin that ran a local bridge was refused for that. What the directory
does take from any publisher is a plugin of skills alone. `clustercontrol-mcp-setup` is that plugin, and
`tools/check-manifests.py` holds it to those rules.

## Changing a plugin

- Set the same `version` in both `clustercontrol-mcp` `plugin.json` files, and raise it with every change.
  Users stay on the version they installed until it changes. `clustercontrol-mcp-setup` has a version of its own.
- When the setup of `clustercontrol-mcp` changes (a setting, a command, a minimum build, an error message),
  change the skill in `clustercontrol-mcp-setup` with it. The skill repeats those facts so that Claude needs
  nothing from outside the plugin.
- Keep the two server definitions equivalent: same server name, and the same timeout (`timeout` in
  milliseconds for Claude Code, `tool_timeout_sec` in seconds for Codex).
- Before pushing, run the checks CI runs:

  ```bash
  claude plugin validate . --strict
  claude plugin validate ./claude/clustercontrol-mcp --strict --profile directory
  claude plugin validate ./claude/clustercontrol-mcp-setup --strict --profile directory
  python3 tools/check-manifests.py
  python3 -m unittest discover -s tools -p 'test_*.py'
  ```

## License

Apache License 2.0; see [LICENSE](LICENSE). The ClusterControl MCP server itself is commercial software
distributed by Severalnines and is not part of this repository. ClusterControl and the ClusterControl logo
are trademarks of Severalnines AB.
