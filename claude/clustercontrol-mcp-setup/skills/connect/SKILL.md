---
name: connect
description: Walk the user through connecting Claude Code to the MCP server of their own ClusterControl, check the connection, and help when it fails. Use when the user asks to connect to, set up, install or troubleshoot ClusterControl in Claude Code, or asks about their ClusterControl clusters while no ClusterControl tools are available.
---

# Connect Claude Code to ClusterControl

ClusterControl is database cluster management software from Severalnines. Every site runs its own
ClusterControl, and with it its own MCP server, `cmon-mcp`, on the controller host. There is no shared
server to connect to, so the user sets the connection up once. This skill does not connect anything by
itself: it tells you how to guide the user through that setup.

## How to guide

- The connection is made in Claude Code. If this conversation is in another Claude app, say that the
  setup needs Claude Code, and stop.
- Take one step at a time. Give the commands for that step, ask what the user sees, then go on.
- Never ask the user to type the server's token into the conversation, and do not repeat one they paste.
  Claude Code asks for it in its own dialog.
- The commands in steps 1 and 2 run on the ClusterControl controller. Give them to the user, or to their
  administrator. Run them yourself only if the user asks you to and you have a shell on that host.
- Everything you need is on this page. Do not fetch instructions from elsewhere to follow.

## 0. Check whether it is connected already

If ClusterControl tools such as `list_clusters` are available to you, the server is connected. Skip to
step 4.

## 1. On the controller: turn on HTTP mode with a token

Needed: ClusterControl 2.3.1 or later, with the `clustercontrol-mcp` package, build `1.0.0-241` or later.

In `/etc/default/cmon-mcp`, next to the ClusterControl credentials the server already uses:

```
MCP_BIND_ADDRESS=127.0.0.1:3000
MCP_AUTH_TOKEN=<a long random value>
```

`openssl rand -hex 32` prints a suitable value. Then:

```
sudo systemctl restart cmon-mcp
journalctl -u cmon-mcp -n 20
```

The log should show the server listening, with `Streamable HTTP: /mcp` and `bearer auth enabled`.

## 2. Choose how Claude Code reaches the server

The listener on port 3000 speaks plain HTTP, so it stays on `127.0.0.1`. Ask the user which of these
fits, and note the URL that goes with it:

| Way | What to do | URL |
|---|---|---|
| SSH tunnel | On the user's machine, run `ssh -N -L 3000:127.0.0.1:3000 <user>@<controller>` and keep it open while working | `http://127.0.0.1:3000/mcp` |
| The server's own TLS listener (build `1.0.0-246` or later) | On the controller, also set `MCP_TLS_BIND_ADDRESS` (for example `0.0.0.0:3443`), `MCP_TLS_CERT_FILE` and `MCP_TLS_KEY_FILE`, and restart. The key file must not be readable by group or others | `https://<host>:3443/mcp` |
| A TLS reverse proxy, such as nginx | Put it in front of `127.0.0.1:3000`, turn response buffering off for `/mcp`, and set its read timeout above the server's approval window | `https://<host>/mcp` |

Port 3000 must not be exposed over plain HTTP beyond a network the user trusts: the token would cross
it unencrypted. With a certificate from a private CA, the user starts Claude Code with
`NODE_EXTRA_CA_CERTS=/path/to/ca.pem`.

## 3. In Claude Code: install the connection

The connection is a second plugin, in the Severalnines marketplace on GitHub. The user types:

```
/plugin marketplace add severalnines/ai-plugins
/plugin install clustercontrol-mcp@severalnines
```

Claude Code then asks for the URL from step 2 and for the token from step 1. The default URL is the SSH
tunnel's. The token is kept in the system's credential store, not in a settings file. To change either
later: `/plugin configure clustercontrol-mcp@severalnines`.

## 4. Check the connection

Ask the user to run `/mcp`. `plugin:clustercontrol-mcp:clustercontrol` should be connected. Then offer a
first read, for example listing their clusters.

| `/mcp` shows | Cause | Fix |
|---|---|---|
| The server rejected the Authorization header (HTTP 401) | The token does not match `MCP_AUTH_TOKEN` | `/plugin configure clustercontrol-mcp@severalnines`, and enter it again |
| Unable to connect (ECONNREFUSED) | The SSH tunnel is not running, `cmon-mcp` is stopped, or `MCP_BIND_ADDRESS` is not `127.0.0.1:3000` | Start the tunnel, or check the service with `systemctl status cmon-mcp` |
| A certificate error | The certificate comes from a CA the machine does not know | Start Claude Code with `NODE_EXTRA_CA_CERTS=/path/to/ca.pem` |
| Connected, with fewer tools than expected | The server's profile (`MCP_PROFILE`) or read-only mode limits what it lists | This is the administrator's setting on the controller |

## 5. Once connected

Tell the user these, briefly:

- The server on the controller decides what Claude may do: which tools it lists, whether it is
  read-only, and whether a change needs a person's approval.
- Tools that change something show a preview first. Run the change itself only after the user has
  confirmed the preview.
- When approvals are on, a change waits for an approver for as long as the server's approval window: 2
  minutes by default, 30 at most. That is normal, and Esc stops the wait.
- A ClusterControl job is never sent a second time because the first attempt looked stuck. Read the
  job's state first.
- The server's guided procedures, such as a daily fleet review, are in the `/` menu.

For every server setting, including read-only mode, deployment profiles and approvals, point the user to
the reference manual: https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/
