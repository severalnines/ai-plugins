# ClusterControl for Codex

Connect Codex to the ClusterControl MCP server, `cmon-mcp`, so Codex can inspect, troubleshoot and operate
the database clusters that [ClusterControl](https://severalnines.com/clustercontrol/) manages: cluster and
node health, alarms, backups, jobs and configuration, plus guided procedures such as a daily fleet review.

## What the plugin does

The plugin adds one MCP server, `clustercontrol`, to Codex. It connects over Streamable HTTP to
`http://127.0.0.1:3000/mcp`, where an SSH tunnel brings you to the `cmon-mcp` service on your controller.
It sends the value of the `CLUSTERCONTROL_MCP_TOKEN` environment variable as a bearer token. It runs
nothing on your machine, installs nothing, and connects to nothing else.

The server on the controller decides what Codex can do: which tools it lists, whether it is read-only, and
whether a change needs a person's approval.

## Requirements

- ClusterControl 2.3.1 or later, with the `clustercontrol-mcp` package, build `1.0.0-241` or later, on the
  controller.
- `cmon-mcp` running as its systemd service in HTTP mode, with a bearer token. See
  [Prepare the controller](../../claude/clustercontrol-mcp/README.md#1-prepare-the-controller); the server
  setup is the same for every client.
- Codex CLI 0.131 or later.

## Install

1. Open an SSH tunnel to the controller and keep it open while you work:

   ```bash
   ssh -N -L 3000:127.0.0.1:3000 <user>@<controller>
   ```

2. Put the token in your environment, for example in your shell profile:

   ```bash
   export CLUSTERCONTROL_MCP_TOKEN=<the MCP_AUTH_TOKEN value from the controller>
   ```

3. Add the marketplace and install the plugin:

   ```bash
   codex plugin marketplace add severalnines/ai-plugins
   codex plugin add clustercontrol-mcp@severalnines
   ```

Start Codex and run `/mcp` to check that `clustercontrol` is connected.

## A different URL

A Codex plugin cannot ask for a URL, so this one always uses the tunnel address. If you reach the server
through a TLS reverse proxy instead, define the server yourself in `~/.codex/config.toml`. An entry with the
same name replaces the plugin's definition as a whole, so give every setting, the timeout included:

```toml
[mcp_servers.clustercontrol]
url = "https://<host>/mcp"
bearer_token_env_var = "CLUSTERCONTROL_MCP_TOKEN"
tool_timeout_sec = 1860
```

## Approvals and timeouts

When the server requires approval for changes (`MCP_HUMAN_APPROVAL=out_of_band`), a destructive call waits
until an approver runs `cmon-mcp approve` on the controller, or until the approval window
(`MCP_APPROVAL_TTL`, 2 minutes by default, 30 at most) ends. The plugin sets `tool_timeout_sec` to 1860, so
Codex keeps waiting for the longest window the server allows.

## Troubleshooting

- **401 from the server:** `CLUSTERCONTROL_MCP_TOKEN` is unset in the shell that started Codex, or does not
  match `MCP_AUTH_TOKEN`.
- **Connection refused:** the SSH tunnel is not running, or `cmon-mcp` is stopped.
- **Fewer tools than expected:** the server's profile (`MCP_PROFILE`) or read-only mode limits what it lists.

Documentation: [ClusterControl MCP reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/).
