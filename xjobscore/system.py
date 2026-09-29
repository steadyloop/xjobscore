"""Public entry point for the unified xjobscore system."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from xjobscore import activity
from xjobscore.inspector.app import create_app
from xjobscore.runtime import RuntimeSettings, UnifiedRuntime


def build_app(settings: RuntimeSettings | None = None):
    settings = settings or RuntimeSettings.from_env()
    return create_app(runtime=UnifiedRuntime(settings))


# Routine per-posting notices: the System page's server log shows them, the
# terminal does not -- at scale they arrive every few seconds.
TERMINAL_QUIET = (("xjobscore.silver", "unresolved company"),)


def _terminal_filter(record: logging.LogRecord) -> bool:
    return not any(
        record.name == name and str(record.msg).startswith(prefix)
        for name, prefix in TERMINAL_QUIET
    )


def cli() -> None:
    # Everything at INFO reaches the System page's server log; the terminal
    # only prints what needs attention (XJOBSCORE_LOG_LEVEL, WARNING by default).
    logging.basicConfig(level=logging.INFO)
    for handler in logging.getLogger().handlers:
        handler.setLevel(os.environ.get("XJOBSCORE_LOG_LEVEL", "WARNING"))
        handler.addFilter(_terminal_filter)
    logging.getLogger().addHandler(activity.LOGS)
    # httpx logs every vendor request at INFO; the System page drops it anyway.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = RuntimeSettings.from_env()
    # uvicorn.run(), unrolled: Config applies uvicorn's logging dictConfig, which
    # replaces the non-propagating "uvicorn" logger's handlers -- the System
    # page's log buffer has to join after that and before the server starts.
    config = uvicorn.Config(
        build_app(settings),
        host=settings.host,
        port=settings.port,
        reload=False,
        workers=1,
        # The System page's own refreshes would log a line every few seconds.
        access_log=False,
    )
    logging.getLogger("uvicorn").addHandler(activity.LOGS)
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    if not server.started:
        sys.exit(3)  # uvicorn's STARTUP_FAILURE


if __name__ == "__main__":
    cli()
