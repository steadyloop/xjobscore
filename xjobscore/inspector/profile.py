"""Display taxonomy for sources: adapter, response format, methodology archetype.

Everything here is derived at request time from the adapter config tables --
nothing is persisted. The ``json-api | docxtract | bespoke`` adapter axis says
how a source is *implemented*, not what kind of board it is: the first two are
config rows driven by a generic module, and ``bespoke`` means the transport
needed its own hand-written adapter. Board kind is a separate field entirely,
the registry's ``source_type`` (ATS / Board / Direct). The remaining vocabulary
reuses what the sources tree already names: the
``json | html | jsonld | xml`` response formats of ``unified_v2.spec``, and the
archetypes exercised by ``sources/tests/test_unified_v2_archetypes.py``.

Each archetype names a request-count multiplier, which is why the inspector
shows them next to elapsed time: ``paginated`` walks list pages, ``detail``
pays one request per item, ``link-discovery`` scrapes item URLs and then
fetches each one, ``discovery`` spends an extra request locating the feed,
and ``single`` is one request per run.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from xjobscore.sources import docxtract, json_api

ADAPTERS = ("json-api", "docxtract", "bespoke")
FORMATS = ("json", "html", "jsonld", "xml")
ARCHETYPES = ("single", "paginated", "detail", "batch-detail", "link-discovery", "discovery")

# The only registered sources outside both spec tables. test_profile.py fails
# loudly when a new bespoke source appears without a row here.
_BESPOKE: dict[str, dict[str, Any]] = {
    # Algolia thread discovery, then a paged comment walk.
    "hn": {"formats": ["json"], "archetypes": ["discovery", "paginated"]},
    "janestreet": {"formats": ["json"], "archetypes": ["single"]},
    "workatastartup": {"formats": ["json"], "archetypes": ["single"]},
    # Server-rendered search pages carry both pager and detail links; each
    # detail response is kept verbatim as the bronze item.
    "avature": {
        "formats": ["html"],
        "archetypes": ["paginated", "link-discovery", "detail"],
    },
    # A site probe plus a CSRF token before the paged POST search can run.
    "dayforce": {"formats": ["json"], "archetypes": ["discovery", "paginated"]},
    # Postings arrive whole inside __NEXT_DATA__, so paging the list is the
    # only request multiplier -- no detail fetch despite the page count.
    "join": {"formats": ["html", "json"], "archetypes": ["paginated"]},
    # fillList pages walked over POST, then one detail GET per requisition.
    "taleo": {"formats": ["html"], "archetypes": ["paginated", "detail"]},
    # One request: the server-rendered page embeds the complete open-job array.
    "zohorecruit": {"formats": ["html", "json"], "archetypes": ["single"]},
}

_DOCXTRACT_FORMATS = {
    "jsonld": ["html", "jsonld"],
    "xml_items": ["xml"],
    "json_items": ["json"],
    # The discovered links all resolve to JSON-LD detail pages today.
    "html_links": ["html", "jsonld"],
}


def _json_api_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    archetypes = []
    if "page" in cfg["list"]:
        archetypes.append("paginated")
    if "detail" in cfg:
        # gem's detail is a GraphQL batch endpoint but declares no "batch"
        # key, so it profiles as plain "detail"; config-derived truth wins.
        archetypes.append("batch-detail" if "batch" in cfg["detail"] else "detail")
    return {
        "adapter": "json-api",
        "formats": ["json"],
        "flavor": None,
        "archetypes": archetypes or ["single"],
    }


def _docxtract_profile(cfg: dict[str, Any]) -> dict[str, Any]:
    extract = cfg["list"]["extract"]
    if extract == "html_links":
        archetypes = ["link-discovery", "detail"]
    else:
        archetypes = ["single"]
    return {
        "adapter": "docxtract",
        "formats": list(_DOCXTRACT_FORMATS.get(extract, [])),
        "flavor": cfg.get("parser"),
        "archetypes": archetypes,
    }


@lru_cache(maxsize=None)
def source_profile(source: str) -> dict[str, Any]:
    """JSON-ready taxonomy for one source name; safe fallback for unknowns."""
    if source in json_api.SPECS:
        return _json_api_profile(json_api.SPECS[source])
    if source in docxtract.SPECS:
        return _docxtract_profile(docxtract.SPECS[source])
    if source in _BESPOKE:
        return {"adapter": "bespoke", "flavor": None, **_BESPOKE[source]}
    return {"adapter": "bespoke", "formats": [], "flavor": None, "archetypes": []}


def wire_content_type(network: dict[str, Any]) -> str | None:
    """Media type of the run's first successful response, parameters stripped.

    The first 2xx event is always the list request, which is the payload the
    pane renders, so a run-level answer is honest without per-item bookkeeping.
    """
    for event in network.get("requests", ()):
        status = event.get("status")
        if status is not None and 200 <= status < 300:
            content_type = event.get("response_headers", {}).get("content-type")
            if content_type:
                return content_type.split(";", 1)[0].strip().lower() or None
            return None
    return None
