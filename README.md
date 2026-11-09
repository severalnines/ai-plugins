# Severalnines AI plugins

Plugins from [Severalnines](https://severalnines.com/) for Claude Code and Codex. The first one connects your
AI coding agent to [ClusterControl](https://severalnines.com/clustercontrol/), so it can inspect, troubleshoot
and operate the database clusters ClusterControl manages, through the ClusterControl MCP server (`cmon-mcp`)
that runs on your controller.

| Plugin | Claude Code | Codex |
|---|---|---|
| `clustercontrol-mcp` | [claude/clustercontrol-mcp](claude/clustercontrol-mcp/README.md) | [codex/clustercontrol-mcp](codex/clustercontrol-mcp/README.md) |

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
codex/clustercontrol-mcp/           Codex plugin: tunnel URL, token from CLUSTERCONTROL_MCP_TOKEN
tools/check-manifests.py            consistency checks (CI), with planted-defect tests beside it
```

The two tools read different manifest and MCP formats, so each plugin has its own folder.

## Changing a plugin

- Set the same `version` in both `plugin.json` files, and raise it with every change. Users stay on the
  version they installed until it changes.
- Keep the two server definitions equivalent: same server name, and the same timeout (`timeout` in
  milliseconds for Claude Code, `tool_timeout_sec` in seconds for Codex).
- Before pushing, run the checks CI runs:

  ```bash
  claude plugin validate . --strict
  claude plugin validate ./claude/clustercontrol-mcp --strict
  python3 tools/check-manifests.py
  python3 -m unittest discover -s tools -p 'test_*.py'
  ```

## License

Apache License 2.0; see [LICENSE](LICENSE). The ClusterControl MCP server itself is commercial software
distributed by Severalnines and is not part of this repository. ClusterControl and the ClusterControl logo
are trademarks of Severalnines AB.
