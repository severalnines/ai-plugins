# ClusterControl for Claude Code

Connect Claude Code to the ClusterControl MCP server, `cmon-mcp`, so Claude can inspect, troubleshoot and
operate the database clusters that [ClusterControl](https://severalnines.com/clustercontrol/) manages:
cluster and node health, alarms, backups, jobs and configuration, plus guided procedures such as a daily
fleet review, failover drills and Galera recovery.

## What the plugin does

The plugin adds one MCP server, `clustercontrol`, to Claude Code. It connects over Streamable HTTP to the
`cmon-mcp` service on your ClusterControl controller, at the URL you enter, and sends the token you enter as
an `Authorization: Bearer` header. It runs nothing on your machine, installs nothing, and connects to
nothing else.

The server on the controller decides what Claude can do: which tools it lists, whether it is read-only, and
whether a change needs a person's approval. Tool results, such as cluster names, host names, metrics and
log lines, become part of your Claude conversation.

## Requirements

- ClusterControl 2.3.1 or later, with the `clustercontrol-mcp` package, build `1.0.0-241` or later, on the
  controller.
- `cmon-mcp` running as its systemd service in HTTP mode, with a bearer token.
- Claude Code.

## 1. Prepare the controller

On the controller host, set these in `/etc/default/cmon-mcp`, next to the ClusterControl credentials the
server uses (`CMON_USERNAME` with `CMON_PASSWORD` or `CMON_KEY_FILE`):

```bash
MCP_BIND_ADDRESS=127.0.0.1:3000
MCP_AUTH_TOKEN=<a long random value, for example the output of: openssl rand -hex 32>
```

Restart the service with `sudo systemctl restart cmon-mcp`. `journalctl -u cmon-mcp -n 20` should show the
server listening, with `Streamable HTTP: /mcp` and `bearer auth enabled`.

The [reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/)
covers every server setting, including read-only mode, deployment profiles and approvals.

## 2. Reach the server securely

The `cmon-mcp` listener speaks plain HTTP. Keep it on `127.0.0.1` and reach it in one of two ways:

- **SSH tunnel.** Run `ssh -N -L 3000:127.0.0.1:3000 <user>@<controller>` and keep it open while you work.
  The plugin's default URL, `http://127.0.0.1:3000/mcp`, then reaches the server through the tunnel.
- **TLS reverse proxy.** Put a proxy with a certificate, such as nginx, in front of `127.0.0.1:3000`, and
  use its `https://<host>/mcp` URL. Turn response buffering off for `/mcp`, and set the read timeout above
  the server's approval window.

Do not expose port 3000 over plain HTTP beyond a network you trust: the token would cross it unencrypted.

## 3. Install the plugin

In Claude Code:

```
/plugin marketplace add severalnines/ai-plugins
/plugin install clustercontrol-mcp@severalnines
```

Claude Code asks for the MCP URL and the token. It stores the token in your system's credential store, not
in a settings file. To change either later, run `/plugin configure clustercontrol-mcp@severalnines`.

Run `/mcp` and check that `plugin:clustercontrol-mcp:clustercontrol` is connected, then ask, for example,
"Give me a daily review of my ClusterControl fleet." The server's guided procedures, such as
`sop_daily_review`, are MCP prompts: type `/` to find them in the command menu.

## Approvals and timeouts

When the server requires approval for changes (`MCP_HUMAN_APPROVAL=out_of_band`), a destructive call waits
until an approver runs `cmon-mcp approve` on the controller, or until the approval window
(`MCP_APPROVAL_TTL`, 2 minutes by default, 30 at most) ends. The plugin gives the server 31 minutes per
request, so Claude Code keeps waiting for the longest window the server allows. Press Esc to stop waiting.

Approvals are a server setting, and they need more than the shared token above: an identity map
(`MCP_IDENTITY_MAP_FILE`) that gives each person their own token and ClusterControl user, plus the approval
settings. Without them, the server refuses destructive calls when approvals are on. The
[reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/)
covers the setup.

## Troubleshooting

- **`/mcp` says the server rejected the Authorization header (HTTP 401):** the token does not match
  `MCP_AUTH_TOKEN`. Run `/plugin configure clustercontrol-mcp@severalnines` and enter it again.
- **`/mcp` says it is unable to connect (ECONNREFUSED):** the SSH tunnel is not running, `cmon-mcp` is
  stopped, or `MCP_BIND_ADDRESS` is not `127.0.0.1:3000`.
- **Fewer tools than expected:** the server's profile (`MCP_PROFILE`) or read-only mode limits what it lists.

## Support

- Documentation: [ClusterControl MCP reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/)
- Support: [support.severalnines.com](https://support.severalnines.com/hc/en-us)
