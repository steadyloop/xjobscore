"""Demo mode: a fully populated OneShot board with no network and no Postgres.

Activate with ``XJOBSCORE_ONESHOT_DEMO=1``; see ``README.md``.  Nothing in this
package is imported when demo mode is off.

Re-exports are resolved lazily (PEP 562).  Importing this package must stay
free of side effects, because ``python -m oneshot_ui.demo`` runs ``__init__``
*before* ``__main__`` gets a chance to put the repository root on ``sys.path``
-- and an eager ``from .catalog import ...`` here would fail on ``xjobscore``
before the launcher could fix it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .catalog import DemoCatalog, DemoGateway
    from .dataset import DEMO_FEEDS, DEMO_PROFILE
    from .seeder import (
        SeedReport,
        build_coordinator,
        seed,
        seed_fresh,
        seed_if_empty,
        summarize,
    )
    from .topup import apply_topup
    from .audit import VerifyReport, verify

# public name -> submodule that defines it
_EXPORTS: dict[str, str] = {
    "DemoCatalog": "catalog",
    "DemoGateway": "catalog",
    "DEMO_FEEDS": "dataset",
    "DEMO_PROFILE": "dataset",
    "SeedReport": "seeder",
    "build_coordinator": "seeder",
    "seed": "seeder",
    "seed_fresh": "seeder",
    "seed_if_empty": "seeder",
    "summarize": "seeder",
    "apply_topup": "topup",
    "VerifyReport": "audit",
    "verify": "audit",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{module_name}", __name__), name)


def __dir__() -> list[str]:
    return __all__
