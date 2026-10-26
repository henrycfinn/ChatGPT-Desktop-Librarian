"""Read-only MCP connector for ChatGPT Work and Codex.

The canonical Markdown folder remains the source of truth.  This process is
intentionally a narrow retrieval surface: it neither writes into the vault nor
indexes proposal data.  It can be published through an authenticated reverse
proxy (for example Cloudflare Access) when ChatGPT Work needs remote access.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from librarian_core import integrations


MAX_FILE_BYTES = 1_000_000
MAX_RESULTS = 20
SEARCHABLE_SUFFIXES = {".md", ".mdx", ".txt", ".rst", ".org"}
EXCLUDED_PARTS = {".git", ".obsidian", ".trash", ".Trash"}


class VaultAccessError(ValueError):
    """A caller requested a path or query outside the retrieval boundary."""


def _resolve_root(raw_root: str | Path) -> Path:
    root = Path(raw_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise VaultAccessError("knowledge root must be a directory")
    return root


def _is_searchable(path: Path) -> bool:
    return path.suffix.lower() in SEARCHABLE_SUFFIXES and not any(part in EXCLUDED_PARTS for part in path.parts)


def _excerpt(text: str, terms: list[str], limit: int = 360) -> str:
    lower = text.lower()
    offsets = [lower.find(term) for term in terms if lower.find(term) >= 0]
    start = max(0, (min(offsets) if offsets else 0) - 100)
    result = " ".join(text[start : start + limit].split())
    return result + ("…" if start + limit < len(text) else "")


@dataclass(frozen=True)
class VaultReader:
    root: Path

    @classmethod
    def from_path(cls, raw_root: str | Path) -> "VaultReader":
        return cls(root=_resolve_root(raw_root))

    def _resolve_note(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise VaultAccessError("path must stay inside the knowledge root") from exc
        if not candidate.is_file() or not _is_searchable(candidate.relative_to(self.root)):
            raise VaultAccessError("path is not a readable knowledge note")
        if candidate.stat().st_size > MAX_FILE_BYTES:
            raise VaultAccessError("note exceeds the maximum readable size")
        return candidate

    def read_note(self, relative_path: str) -> dict[str, str]:
        note = self._resolve_note(relative_path)
        return {
            "path": note.relative_to(self.root).as_posix(),
            "content": note.read_text(encoding="utf-8", errors="replace"),
        }

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        terms = sorted({term.lower() for term in re.findall(r"[\w-]{2,}", query)})
        if not terms:
            raise VaultAccessError("query needs at least one two-character term")
        bounded_limit = max(1, min(int(limit), MAX_RESULTS))
        qmd_matches = self._search_qmd(query, terms, bounded_limit)
        if qmd_matches is not None:
            return qmd_matches
        matches: list[tuple[int, str, str]] = []
        for candidate in self.root.rglob("*"):
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(self.root)
            if not _is_searchable(relative) or candidate.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = content.lower()
            title = relative.stem.lower()
            score = sum(lowered.count(term) * 3 + title.count(term) * 8 for term in terms)
            if score:
                matches.append((score, relative.as_posix(), _excerpt(content, terms)))
        matches.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"path": path, "score": score, "excerpt": excerpt}
            for score, path, excerpt in matches[:bounded_limit]
        ]

    def _search_qmd(self, query: str, terms: list[str], limit: int) -> list[dict[str, Any]] | None:
        """Prefer QMD's index, falling back only when it cannot answer."""
        if not integrations.qmd_available():
            return None
        code, out, _ = integrations._run(
            integrations._qmd_args("search", query, "--format", "json", "--full-path", "-n", str(limit)),
            cwd=self.root,
            timeout=20,
        )
        if code != 0 or not out:
            return None
        try:
            candidates = integrations._extract_qmd_paths(__import__("json").loads(out), self.root)
        except (__import__("json").JSONDecodeError, ValueError):
            return None
        if not candidates:
            # An empty or stale index must never hide readable vault material.
            return None
        results: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                relative = candidate.relative_to(self.root)
                if not _is_searchable(relative) or not candidate.is_file() or candidate.stat().st_size > MAX_FILE_BYTES:
                    continue
                content = candidate.read_text(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                continue
            results.append({"path": relative.as_posix(), "score": None, "excerpt": _excerpt(content, terms)})
            if len(results) >= limit:
                break
        return results

    def search_backend(self) -> str:
        return "qmd indexed retrieval" if integrations.qmd_available() else "local lexical fallback (QMD unavailable)"


def create_server(reader: VaultReader):
    """Create the MCP server lazily so package-free policy tests still run."""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:  # pragma: no cover - runtime dependency boundary
        raise RuntimeError(
            "ChatGPT MCP support requires the Python 'mcp' package. Install the supported "
            "MCP SDK in the connector environment."
        ) from exc

    server = MCPServer(
        name="chatgpt-desktop-librarian",
        title="ChatGPT Desktop Librarian",
        description="Read-only search and retrieval over a canonical Markdown knowledge base.",
        instructions=(
            "Use search_memory to locate candidate notes, then read_memory before relying on "
            "their detail. This connector cannot change the knowledge base."
        ),
        version="1.2.0",
    )

    @server.tool(
        name="search_memory",
        description="Search the canonical knowledge base. Returns note paths and short excerpts; read a note before using it as evidence.",
    )
    def search_memory(query: str, limit: int = 8) -> dict[str, Any]:
        return {"results": reader.search(query, limit)}

    @server.tool(
        name="read_memory",
        description="Read one knowledge note returned by search_memory. Paths must be relative to the canonical knowledge root.",
    )
    def read_memory(path: str) -> dict[str, str]:
        return reader.read_note(path)

    @server.tool(
        name="memory_status",
        description="Report the connector's retrieval boundary and whether it permits mutations.",
    )
    def memory_status() -> dict[str, Any]:
        return {
            "knowledge_root_name": reader.root.name,
            "writes_enabled": False,
            "search_backend": reader.search_backend(),
            "max_note_bytes": MAX_FILE_BYTES,
        }

    return server


def create_http_app(server: Any, bearer_token: str | None):
    """Wrap the Streamable HTTP app with optional local bearer authentication.

    ``None`` is permitted only for the explicit tunnel-only deployment mode:
    the server remains bound to loopback and OpenAI Secure MCP Tunnel is the
    sole ingress control. All other deployments require a bearer token.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        def __init__(self, app, *, token: str):
            super().__init__(app)
            self.token = token

        async def dispatch(self, request, call_next):
            if request.url.path == "/healthz":
                return await call_next(request)
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(supplied, expected):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="127.0.0.1",
    )
    if bearer_token:
        app.add_middleware(BearerAuthMiddleware, token=bearer_token)

    async def healthz(_request):
        return JSONResponse({"status": "ok", "writes_enabled": False})

    app.add_route("/healthz", healthz, methods=["GET"])
    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ChatGPT Desktop Librarian MCP connector.")
    parser.add_argument("--knowledge-root", default=os.environ.get("WIKI_PATH", ""))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bearer-token-env", default="CHATGPT_DESKTOP_LIBRARIAN_TOKEN")
    parser.add_argument(
        "--tunnel-only",
        action="store_true",
        help="Allow unauthenticated loopback MCP requests only when OpenAI Secure MCP Tunnel is the sole ingress.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.knowledge_root:
        raise SystemExit("--knowledge-root or WIKI_PATH is required")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("the connector must bind to loopback; publish it through an authenticated reverse proxy")
    token = os.environ.get(args.bearer_token_env, "")
    if not args.tunnel_only and len(token) < 32:
        raise SystemExit(f"{args.bearer_token_env} must contain a random token of at least 32 characters")
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - runtime dependency boundary
        raise SystemExit("the connector requires uvicorn in its runtime environment") from exc
    app = create_http_app(create_server(VaultReader.from_path(args.knowledge_root)), None if args.tunnel_only else token)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
