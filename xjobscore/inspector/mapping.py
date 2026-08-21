"""What an adapter's config reads, and what those reads collapse to.

Pure and offline: no database, no network. Everything here is derived from
the two declarative config tables (``json-api-configs.py``,
``docxtract-configs.py``) plus the canons under ``registry/job-posting``, so
it answers the Fields page's second question -- "what does this field
collapse to?" -- without touching a row.

Two halves:

``adapter_map`` walks a config's field expressions backwards, turning each
one into the set of raw payload paths it reads. The path notation matches the
Fields survey exactly (``departments[].name``), so an annotation is a dict
lookup rather than a fuzzy match.

``COLLAPSE`` transcribes the downstream chain from ``silver._projection`` and
``gold``: which ``Posting`` field reaches which ``job_posting`` column, which
pure function narrows it, and what value domain it lands in. Fields absent
from that table are bronze-only -- captured by the adapter and never
projected -- which is the single most useful thing the page reports.
"""

from __future__ import annotations

import re
from typing import Any

from xjobscore import degrees, silver
from xjobscore.sources import PARSERS, docxtract, json_api

# `{token}` inside a "template" expression. Bare tokens mean the payload,
# matching json-api's own _format().
_TEMPLATE_TOKEN = re.compile(r"{([^}]+)}")

# Sources whose parser is hand-written Python rather than a config table.
# Listed by absence from both config tables, not by name, so a source that
# moves into a config table stops being reported as opaque automatically.


def _join(prefix: str, path: str) -> str:
    """Compose an outer path with one read relative to it."""
    if not path or path == ".":
        return prefix
    return f"{prefix}.{path}" if prefix else path


def _paths(spec: Any, prefix: str = "") -> tuple[str, ...]:
    """Every payload path one config expression reads, in survey notation.

    ``prefix`` carries the enclosing row for the iterating operators, so
    ``{"map": "@offices", "value": "@name"}`` -- whose ``value`` is evaluated
    against each element rather than the payload -- yields ``offices[].name``
    and lines up with what the survey's recursive walk reports.

    ``$name`` expressions read the harvest context (``$key``, ``$id``,
    ``$cursor``), never the payload, so they contribute nothing.
    """
    if isinstance(spec, str):
        if spec.startswith("@"):
            return (_join(prefix, spec[1:]),)
        return ()  # "$ctx" reads context; anything else is a literal.
    if isinstance(spec, (list, tuple)):
        return tuple(path for item in spec for path in _paths(item, prefix))
    if not isinstance(spec, dict):
        return ()

    if "template" in spec:
        found = []
        for token in _TEMPLATE_TOKEN.findall(spec["template"]):
            if token.startswith("$"):
                continue
            found.append(_join(prefix, token.lstrip("@")))
        return tuple(found)

    # Operators that iterate: the inner expression sees a row, not the
    # payload, so its reads nest under the outer path plus "[]".
    # "where" selects a row by a sibling field, so it reads the element too --
    # without it the name half of a tenant name/value bag is invisible here.
    for outer, inner_keys in (("map", ("value", "where")), ("sections", ("heading", "html", "sort"))):
        if outer in spec:
            bases = _paths(spec[outer], prefix) or (prefix,)
            found = []
            for base in bases:
                element = f"{base}[]"
                inner = tuple(
                    path
                    for key in inner_keys
                    if key in spec
                    for path in _paths(spec[key], element)
                )
                found.extend(inner or (element,))
            return tuple(found)

    # Operators that unwrap a list into one of its elements. The path the
    # config reads is the array's contents, which the survey calls "x[]".
    for outer in ("first", "rest"):
        if outer in spec:
            inner = spec[outer]
            # ...unless the inner expression already iterated, as in
            # {"first": {"map": ..., "value": ...}}. Its paths are element-level
            # already, so appending a second "[]" would name a path the survey
            # never reports.
            iterated = isinstance(inner, dict) and ("map" in inner or "sections" in inner)
            found = _paths(inner, prefix)
            return found if iterated else tuple(f"{base}[]" for base in found)

    # "lookup" pairs a literal table with a key expression; "date"/"join"/
    # "concat"/"fallback"/"eq"/"unescape"/"epoch_*" and any plain nested dict
    # all read their values against the same payload the caller does.
    ignored = {"sep", "fmt", "sort"}
    return tuple(
        path
        for key, value in spec.items()
        if key not in ignored
        for path in _paths(value, prefix)
    )


# schema.org JSON-LD is read by fixed key, not by configured path, so the
# mapping for a `schemaorg` board is the same every time. Transcribed from
# docxtract._schemaorg.
_SCHEMAORG = {
    "title": "title",
    "url": "url_posting",
    "hiringOrganization.name": "company_raw",
    "description": "description_html",
    "employmentType": "employment_type",
    "datePosted": "published_at",
    "validThrough": "deadline_at",
    "identifier": "requisition_id",
}

# The `*_path` keys an XML parser block may carry, and the Posting field each
# one feeds. Transcribed from docxtract._rss / _atom / _xml_map.
_DOC_PATH_FIELDS = {
    "company_path": "company_raw",
    "description_path": "description_html",
    "location_path": "location_raw",
    "location_paths": "location_raw",
    "employment_type_path": "employment_type",
}


def _docxtract_map(config: dict) -> dict[str, list[str]]:
    """Raw path -> canonical fields for a document board."""
    found: dict[str, list[str]] = {}

    def add(path: str, field: str) -> None:
        if path:
            found.setdefault(path, [])
            if field not in found[path]:
                found[path].append(field)

    parser = config.get("parser")
    if parser == "schemaorg":
        for path, field in _SCHEMAORG.items():
            add(path, field)
        for key in config.get(
            "source_extra", ("baseSalary", "identifier", "industry", "validThrough_raw")
        ):
            add("validThrough" if key == "validThrough_raw" else key, f"source_extra.{key}")
        return found

    block = config.get(parser) or config.get("xml_map") or {}
    for key, field in _DOC_PATH_FIELDS.items():
        value = block.get(key)
        for path in (value,) if isinstance(value, str) else (value or ()):
            add(path, field)
    for key, path in block.get("extra_paths", {}).items():
        add(path, f"source_extra.{key}")
    return found


def adapter_map(adapter: str) -> dict[str, Any]:
    """How ``adapter`` reads its payload, or why that cannot be answered.

    ``paths`` annotates the survey (raw path -> canonical fields); ``fields``
    drives the collapse chain (canonical field -> the expression behind it).
    A hand-written parser gets ``introspectable: False`` and a note, never a
    silently empty table -- "this adapter maps nothing" and "this adapter's
    mapping is not machine-readable" are very different answers.
    """
    config = json_api.CONFIGS.get(adapter)
    if config is not None:
        fields: dict[str, Any] = {}
        for name, expr in config.get("fields", {}).items():
            fields[name] = {"expr": expr, "paths": list(dict.fromkeys(_paths(expr)))}
        for name, expr in config.get("extra", {}).items():
            fields[f"source_extra.{name}"] = {
                "expr": expr,
                "paths": list(dict.fromkeys(_paths(expr))),
            }
        paths: dict[str, list[str]] = {}
        for name, entry in fields.items():
            for path in entry["paths"]:
                paths.setdefault(path, []).append(name)
        return {
            "adapter": adapter,
            "engine": "json-api",
            "introspectable": True,
            "paths": paths,
            "fields": fields,
            "note": None,
        }

    config = docxtract.CONFIGS.get(adapter)
    if config is not None:
        paths = _docxtract_map(config)
        fields = {}
        for path, names in paths.items():
            for name in names:
                fields.setdefault(name, {"expr": path, "paths": []})["paths"].append(path)
        return {
            "adapter": adapter,
            "engine": "docxtract",
            "introspectable": True,
            "paths": paths,
            "fields": fields,
            "note": (
                f"Document board ({config.get('parser')}); paths are XML/JSON-LD "
                "element paths, not payload keys."
            ),
        }

    known = adapter in PARSERS
    return {
        "adapter": adapter,
        "engine": "bespoke" if known else "unknown",
        "introspectable": False,
        "paths": {},
        "fields": {},
        "note": (
            f"Hand-written parser -- see xjobscore/sources/{adapter}.py. "
            "Its field mapping is Python, not a config table, so it cannot be "
            "introspected here."
            if known
            else f"No source named {adapter!r} is registered."
        ),
    }


# Mirrors schema.sql's job_type CHECK. NULL is a real member of the domain --
# it is what a vendor with no measured signal emits -- so it is named here.
_JOB_TYPE_DOMAIN = ["intern", "coop", "student_program", "research", "part_time", None]


def _degree_domain() -> list[str]:
    return sorted(field.name for field in degrees.canon())


# The downstream chain, transcribed from silver._projection and gold. A
# Posting field absent from this table is bronze-only: the adapter captures
# it, and nothing projects it into job_posting.
COLLAPSE: dict[str, dict[str, Any]] = {
    "title": {"column": "title", "collapse": None, "target": "title"},
    # Derived in the adapter, not here. The rules are vendor-specific and read
    # several payload fields at once (title, slug, feed identity), so there is
    # no single canonical field to hang the chain off, and no pure
    # single-value collapse that `preview` could honestly run.
    "job_type": {
        "column": "job_type",
        "collapse": "adapter classify rules",
        "target": "job_type",
        "note": "Parse-time and vendor-specific; doc/raw_postings/ holds the "
        "measured evidence behind each vendor's rule table.",
    },
    "remote": {
        "column": "remote",
        "collapse": "adapter classify rules",
        "target": "remote",
        "note": "Never 'no' -- an on-site signal yields 'unknown' rather than "
        "being asserted as a negative.",
    },
    "workplace_type_raw": {
        "column": None,
        "collapse": "silver._remote()",
        "target": "attributes.remote",
        "preview": "remote",
        "note": "Never 'no' -- an on-site signal is left absent rather than asserted.",
    },
    "location_raw": {
        "column": "location_text",
        "collapse": "silver._remote_from_location()",
        "target": "attributes.remote",
        "preview": "remote_location",
        "note": "Description text is deliberately excluded from this match.",
    },
    "description_html": {
        "column": "description",
        "collapse": "silver._plain_text() -> skills.extract, degrees.extract",
        "target": "attributes.skills, attributes.degree_fields",
    },
    "state": {"column": "status", "collapse": None, "target": "status"},
    "source_active": {"column": "status", "collapse": None, "target": "status"},
    "deadline_at": {"column": None, "collapse": "isoformat()", "target": "attributes.deadline_at"},
    "company_raw": {
        "column": "company_id",
        "collapse": "store.resolve_company()",
        "target": "company_id",
        "note": "Falls back to feed.company_id for ATS/Direct feeds only; a Board "
        "posting that resolves to nothing stays bronze-only.",
    },
    "published_at": {"column": "posted_at", "collapse": None, "target": "posted_at"},
    "url_posting": {"column": "source_url", "collapse": None, "target": "source_url"},
    "apply_url": {"column": "apply_url", "collapse": None, "target": "apply_url"},
}


def domains() -> dict[str, list[str]]:
    """Value domains for the enum-ish targets, read from the live canons."""
    return {
        "job_type": _JOB_TYPE_DOMAIN,
        "remote": ["yes", "hybrid", "unknown"],
        # The legacy silver-derived attribute, still the only remote signal for
        # vendors whose parse-time rules have not been transcribed yet. It has
        # no 'unknown' member: absence is how it says "no signal".
        "attributes.remote": ["yes", "hybrid"],
        "attributes.degree_fields": _degree_domain(),
        "status": ["active", "closed"],
    }


def chain(field: str) -> dict[str, Any]:
    """The collapse chain for one canonical field, bronze-only if unlisted."""
    entry = COLLAPSE.get(field.split(".", 1)[0] if field.startswith("source_extra") else field)
    if entry is None:
        return {
            "field": field,
            "projected": False,
            "column": None,
            "collapse": None,
            "target": None,
            "domain": None,
            "note": "Bronze-only: captured by the adapter, never projected into job_posting.",
        }
    return {
        "field": field,
        "projected": True,
        "column": entry.get("column"),
        "collapse": entry.get("collapse"),
        "target": entry.get("target"),
        "domain": domains().get(entry.get("target") or ""),
        "note": entry.get("note"),
    }


def preview(field: str, values: list[str | None]) -> dict[str, str | None]:
    """Run the pure collapse for ``field`` over observed values.

    Only the fields whose collapse is a pure function of the value alone.
    Anything needing a database (company resolution), the whole description
    (skills, degree fields), or several payload fields at once (the adapter's
    job_type / remote rules) is deliberately absent -- a preview that lied
    would be worse than no preview.
    """
    kind = (COLLAPSE.get(field) or {}).get("preview")
    if kind is None:
        return {}
    functions = {
        "remote": silver._remote,
        "remote_location": silver._remote_from_location,
    }
    collapse = functions[kind]
    return {value: collapse(value) for value in values if isinstance(value, str)}


def _ancestors(path: str) -> list[str]:
    """``a[].b.c`` -> ``a[].b``, ``a[]``, ``a`` -- nearest enclosing first."""
    found = []
    current = path
    while current:
        if current.endswith("[]"):
            current = current[:-2]
        elif "." in current:
            current = current.rsplit(".", 1)[0]
        else:
            break
        if current:
            found.append(current)
    return found


def annotate(paths: list[str], adapter: str) -> dict[str, dict[str, Any]]:
    """Attach the canonical fields each surveyed path feeds.

    A path with no exact entry inherits from its nearest mapped ancestor:
    greenhouse captures the whole ``departments`` array into
    ``source_extra.departments``, so ``departments[].name`` is carried along
    inside it without being named. Marking that as ``inherited`` keeps the
    distinction visible -- "this exact value is mapped" and "this value rides
    along inside a mapped object" are different answers during recon.
    """
    known = adapter_map(adapter)["paths"]
    found: dict[str, dict[str, Any]] = {}
    for path in paths:
        fields = known.get(path)
        if fields:
            found[path] = {"fields": fields, "inherited": None}
            continue
        for ancestor in _ancestors(path):
            if ancestor in known:
                found[path] = {"fields": known[ancestor], "inherited": ancestor}
                break
        else:
            found[path] = {"fields": [], "inherited": None}
    return found
