# ChatGPT Desktop connector

The ChatGPT Desktop Librarian connector lets **ChatGPT Work** and Codex retrieve
notes from an existing canonical Markdown knowledge base. It is not ChatGPT's
native account memory and it does not make the vault automatically available in
every conversation. A user must connect the MCP service to ChatGPT Work and use
the connector in a task.

## Retrieval boundary

The connector exposes only three MCP tools:

- `search_memory` searches canonical text notes and returns short excerpts.
- `read_memory` retrieves one note returned by search.
- `memory_status` reports the active retrieval boundary.

It never exposes `.obsidian`, `.git`, trash, pending proposals, or paths outside
the configured root. It does not write notes. Durable writes still use the
Librarian change/proposal workflow in Hermes or Codex.

## Run locally

Use a dedicated virtual environment and install the optional runtime:

```powershell
python -m venv .venv-chatgpt-mcp
.\.venv-chatgpt-mcp\Scripts\python.exe -m pip install -r requirements-chatgpt-mcp.txt
```

Generate and store a random token in your secret manager or process environment;
do not put it in the repository, a wiki note, or a command history. Then run:

```powershell
$env:CHATGPT_DESKTOP_LIBRARIAN_TOKEN = '<random-secret-at-least-32-characters>'
.\.venv-chatgpt-mcp\Scripts\python.exe scripts\chatgpt_desktop_mcp.py `
  --knowledge-root C:\path\to\wiki
```

The connector binds only to `127.0.0.1:8765` and requires `Authorization: Bearer
<token>` for `/mcp`. `GET /healthz` is intentionally the only unauthenticated
endpoint and reports no vault content.

For an OpenAI Secure MCP Tunnel deployment, use `--tunnel-only` instead of a
connector bearer token. This is deliberately opt-in: it permits unauthenticated
requests only on loopback, where the authenticated tunnel client is the sole
remote ingress. Do not use it with a public proxy or non-loopback binding.

## Connect ChatGPT Work

Use [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
to keep the connector private. It creates an outbound-only path from this
computer to OpenAI; do not expose the local port or create a public tunnel.

1. In Platform tunnel settings, create a tunnel and associate it with the target
   ChatGPT workspace and Platform organization.
2. Create a runtime API key for `tunnel-client`; store it outside the
   repository and outside the vault.
3. Install the official `tunnel-client`, configure it to reach
   `http://127.0.0.1:8765/mcp`, and verify it with `tunnel-client doctor`.
4. In ChatGPT Plugins, choose **Add custom MCP server → Tunnel**, select that
   tunnel, configure authentication, and create the private plugin.

The tunnel client and this connector must remain healthy for retrieval to work.
The tunnel is an explicit retrieval capability, not an automatic long-term-memory
replacement.

For this repository's configured `ChatGPT Desktop Librarian` tunnel, run the
launcher from a normal user PowerShell session after setting
`CONTROL_PLANE_API_KEY` in the user environment:

```powershell
$env:WIKI_PATH = 'C:\path\to\your\wiki'
.\scripts\start_chatgpt_desktop_tunnel.ps1
```

It starts the read-only connector in explicit loopback-only tunnel mode on port
8766 (avoiding the common 8765 conflict), waits
for its health endpoint, and attaches the official tunnel client. Keep that
PowerShell window open while using the ChatGPT connector.

## Verification

1. Request `memory_status`; it must report `writes_enabled: false`.
2. Search for a unique heading in a non-sensitive test note.
3. Use the returned relative path with `read_memory`.
4. Verify that `../` and `.obsidian/...` paths are rejected.
5. Confirm that no wiki content appears in proxy logs, shell history, or this
   repository.
