from __future__ import annotations

"""
FastMCP-based stdio server (quickstart style) for local testing.

- Tools:
  - health(): returns "ok"

Run:
  uv run xenomcps

Claude Desktop config (macOS):
  {
    "mcpServers": {
      "xenomcps": {
        "type": "stdio",
        "command": "/opt/homebrew/bin/uv",
        "args": ["run", "xenomcps"]
      }
    }
  }
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


# ---------- minimal file logging (never to stdout) ----------
def _log_path() -> Path:
    try:
        root = os.environ.get("XENOMCPS_LOG_DIR")
        if root:
            p = Path(root)
        else:
            p = Path.home() / ".claude" / "mcp"
        p.mkdir(parents=True, exist_ok=True)
        return p / "xenomcps.log"
    except Exception:
        return Path.home() / "xenomcps.log"


def _log_line(text: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(text.rstrip("\n") + "\n")
    except Exception:
        pass


def _log_probe(stage: str) -> None:
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _log_line(
            f"{ts} stage={stage} cwd={os.getcwd()} exe={sys.executable} PATH={os.environ.get('PATH','')}"
        )
    except Exception:
        pass


_log_probe("import")

mcp = FastMCP("xenomcps")


@mcp.tool()
async def health() -> str:
    """Health check."""
    return "ok"

def main() -> None:
    # write a startup probe, then run stdio
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _log_line(f"{ts} starting stdio")
    except Exception:
        pass
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
