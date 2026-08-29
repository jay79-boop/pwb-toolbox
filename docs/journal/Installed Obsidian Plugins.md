---
type: reference
status: active
project: meta
---

# Installed Obsidian Plugins

Community plugins this setup was built around, with a one-line purpose each. Not all of these are load-bearing — audit periodically for overlapping "let AI touch my vault" plugins and drop what's unused.

## Core (load-bearing)
- **obsidian-local-rest-api** ("Local REST API with MCP") — REST+MCP server other tools connect through. Generates its own API key/cert on first run — never commit that config.
- **copilot** (Logan Yang's Copilot) — in-vault AI chat.

## Sync / mirroring
- **claude-code-sync** — mirrors coding-agent sessions into the vault as markdown notes. If used, exclude that folder in `.graphifyignore` — see the note there.
- **claude-sync** — auto-imports chat exports from a watched folder.
- **obvec-sync** (AI Search for Your Second Brain) — semantic search over the vault via MCP.
- **agentage-memory** (Agentage Sync) — two-way git sync to a private memory store via MCP.

## MCP / agent bridges
- **vault-as-mcp** — exposes the vault itself as an MCP server.
- **mcp-tools-istefox** (MCP Connector)
- **local-rest-api-second-brain-mcp-extension**
- **agent-client** — chat with AI agents via Agent Client Protocol.
- **agent-mcp** — run coding agents (Claude Code, Codex, Ollama) in a built-in terminal.
- **agentfiles** — discover/organize AI agent skills/commands across Claude Code, Cursor, Codex, Windsurf.
- **local-rest-api-periodic-notes** — periodic-note endpoints extension for the REST API.
- **mcp-rest** — another MCP server via the local REST API.

## Utility
- **local-version-history** — per-file local version history.
- **llm-token-count** — token counter in the status bar.
- **linkvault** — AI-assisted link bookmarking.
- **obsidian42-brat** — beta plugin installer/updater (how several of the above get installed).

**How to apply:** if plugins start fighting over a port, causing unexpected vault writes, or slowing startup, check ones with tiny/default-sized config files first — that usually means installed-and-never-configured rather than real use.
