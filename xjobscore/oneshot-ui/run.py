"""File-path launcher that keeps all new code inside the mandated directory."""

from __future__ import annotations

import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = APP_ROOT.parents[1]
for path in (REPOSITORY_ROOT, APP_ROOT):
    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

import uvicorn

from oneshot_ui.config import load_settings


if __name__ == "__main__":
    settings = load_settings()
    uvicorn.run(
        "oneshot_ui.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )

