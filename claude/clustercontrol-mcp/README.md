# ClusterControl for Claude Code

Connect Claude Code to the ClusterControl MCP server, `cmon-mcp`, so Claude can inspect, troubleshoot and
operate the database clusters that [ClusterControl](https://severalnines.com/clustercontrol/) manages:
cluster and node health, alarms, backups, jobs and configuration, plus guided procedures such as a daily
fleet review, failover drills and Galera recovery.

## What the plugin does

The plugin adds one MCP server, `clustercontrol`, to Claude Code. Every ClusterControl site runs its own
`cmon-mcp`, so the plugin cannot name one address for everyone. It takes yours from its options and reaches
the server through a small bridge.

Claude Code starts the bridge, [`bridge/cmon-mcp-bridge.mjs`](bridge/cmon-mcp-bridge.mjs), with Node.js on
your machine. The bridge is one readable file. It connects over Streamable HTTP to the `cmon-mcp` service on
your ClusterControl controller, at the URL you enter, and sends the token you enter as an
`Authorization: Bearer` header. It connects to that URL and to nothing else. It reads and writes no files,
runs no other program, installs nothing, and uses only modules that ship with Node.js. It never prints the
token.

The server on the controller decides what Claude can do: which tools it lists, whether it is read-only, and
whether a change needs a person's approval. Tool results, such as cluster names, host names, metrics and
log lines, become part of your Claude conversation.

## Requirements

- ClusterControl 2.3.1 or later, with the `clustercontrol-mcp` package, build `1.0.0-241` or later, on the
  controller.
- `cmon-mcp` running as its systemd service in HTTP mode, with a bearer token.
- Claude Code.
- Node.js 18 or later on the machine that runs Claude Code. `node --version` shows what you have.

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

The `cmon-mcp` listener on `MCP_BIND_ADDRESS` speaks plain HTTP. Keep it on `127.0.0.1` and reach the
server in one of three ways:

- **SSH tunnel.** Run `ssh -N -L 3000:127.0.0.1:3000 <user>@<controller>` and keep it open while you work.
  The plugin's default URL, `http://127.0.0.1:3000/mcp`, then reaches the server through the tunnel.
- **Native TLS** (`clustercontrol-mcp` build `1.0.0-246` or later). On the controller, also set
  `MCP_TLS_BIND_ADDRESS` (for example `0.0.0.0:3443`), `MCP_TLS_CERT_FILE` and `MCP_TLS_KEY_FILE` (the key
  file must not be readable by group or others). The TLS listener requires the `MCP_AUTH_TOKEN` from step 1,
  or an identity map. The server then serves TLS on that address as well, while the plain listener keeps
  serving the tunnel. Enter `https://<host>:3443/mcp` as the URL. If the
  certificate comes from a private CA, start Claude Code with `NODE_EXTRA_CA_CERTS=/path/to/ca.pem` so the
  bridge trusts that CA.
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

`/mcp` shows why the server failed to connect.

- **"Executable not found in $PATH":** Claude Code could not find `node`. Install Node.js 18 or later, and
  start Claude Code from a shell where `node --version` works.
- **"refused the token (HTTP 401)":** the token does not match `MCP_AUTH_TOKEN`. Run
  `/plugin configure clustercontrol-mcp@severalnines` and enter it again.
- **"Cannot reach the ClusterControl MCP server ... (ECONNREFUSED)":** the SSH tunnel is not running,
  `cmon-mcp` is stopped, or `MCP_BIND_ADDRESS` is not `127.0.0.1:3000`.
- **"The server certificate was not trusted":** the certificate comes from a CA your machine does not know.
  Start Claude Code with `NODE_EXTRA_CA_CERTS=/path/to/ca.pem`.
- **Fewer tools than expected:** the server's profile (`MCP_PROFILE`) or read-only mode limits what it lists.

After `cmon-mcp` restarts there is nothing to do: the bridge starts a new session with the next request.

## Support

- Documentation: [ClusterControl MCP reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/)
- Support: [support.severalnines.com](https://support.severalnines.com/hc/en-us)
