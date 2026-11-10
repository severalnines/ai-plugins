# ClusterControl MCP Setup for Claude Code

A setup guide. With this plugin, Claude walks you through connecting Claude Code to the MCP server of your
own [ClusterControl](https://severalnines.com/clustercontrol/), checks the connection with you, and helps
when it fails. Once connected, Claude can inspect, troubleshoot and operate the database clusters that
ClusterControl manages.

## What the plugin does

The plugin holds one skill, `connect`, which is a page of instructions for Claude. It contains no MCP
server and no program. It runs nothing on your machine, connects to nothing, and collects no data.

It is a guide, and not the connection, for a reason. Every ClusterControl site runs its own MCP server,
`cmon-mcp`, on its own controller, so there is no one address a plugin could connect everyone to. The
connection itself is a second plugin, `clustercontrol-mcp`, in the
[Severalnines marketplace](https://github.com/severalnines/ai-plugins). It asks you for your server's
address and token. The skill tells you how to install it, and you decide whether to.

## What the skill covers

1. What to set on the ClusterControl controller: HTTP mode and a bearer token.
2. How Claude Code reaches the server: an SSH tunnel, the server's own TLS listener, or a TLS reverse proxy.
3. Installing the connection in Claude Code.
4. Checking the connection, and what to do about each way it can fail.
5. What to expect once connected: previews before changes, approvals, and the server's guided procedures.

## Requirements

- Claude Code.
- ClusterControl 2.3.1 or later, with the `clustercontrol-mcp` package, build `1.0.0-241` or later, on the
  controller. You, or your ClusterControl administrator, need to be able to change its settings.

## Install

In Claude Code:

```
/plugin marketplace add severalnines/ai-plugins
/plugin install clustercontrol-mcp-setup@severalnines
```

## Try it

Ask Claude, for example:

- "Connect Claude Code to my ClusterControl."
- "I installed the ClusterControl connection and `/mcp` says it failed. Help me fix it."
- "What do I need to set on my ClusterControl controller before Claude can reach it?"

## Support

- Documentation: [ClusterControl MCP reference manual](https://docs.severalnines.com/clustercontrol/latest/reference-manuals/clustercontrol-mcp/)
- Support: [support.severalnines.com](https://support.severalnines.com/hc/en-us)

ClusterControl and the ClusterControl logo are trademarks of Severalnines AB.
