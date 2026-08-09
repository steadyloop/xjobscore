"""Local-only feed inspection console.

The inspector is deliberately separate from the polling loop. It may read
feeds and call a harvester once; it never changes feed configuration, creates
poll runs, or writes bronze data.
"""

from xjobscore.inspector.app import app

__all__ = ["app"]
