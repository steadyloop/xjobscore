"""Public entry point for the unified xjobscore system."""

from __future__ import annotations

import logging
import os

import uvicorn

from xjobscore.inspector.app import create_app
from xjobscore.runtime import RuntimeSettings, UnifiedRuntime


def build_app(settings: RuntimeSettings | None = None):
    settings = settings or RuntimeSettings.from_env()
    return create_app(runtime=UnifiedRuntime(settings))


def cli() -> None:
    logging.basicConfig(level=os.environ.get("XJOBSCORE_LOG_LEVEL", "INFO"))
    settings = RuntimeSettings.from_env()
    uvicorn.run(
        build_app(settings),
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    cli()
