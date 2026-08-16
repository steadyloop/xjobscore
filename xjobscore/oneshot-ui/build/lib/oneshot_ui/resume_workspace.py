"""The content bank, as pure data.

``resume_workspace`` stores the profile, every entry (with its bullets nested),
and every skill in a single jsonb row, so nearly all of the studio's remaining
work is list manipulation rather than SQL. Keeping that work here — with no
database, no clock, and no I/O — means the Postgres store and the in-memory test
fake run the *same* ordering, position, and uniqueness rules, and a fake can only
drift on the load/save shell.

Ids for entries, bullets, and skills are drawn from one shared ``next_item_id``
counter, so a `resume_document.selection` stays valid without a table per content
type. Every mutator edits the workspace in place and returns a deep copy, so a
caller can never hold a reference into stored state.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

PROFILE_FIELDS = ("full_name", "email", "phone", "location", "links")
ENTRY_FIELDS = (
    "kind", "org", "role", "location", "start_date", "end_date", "url", "detail",
)
ENTRY_UPDATABLE = frozenset(ENTRY_FIELDS) | {"position"}
BULLET_UPDATABLE = frozenset({"text", "audience", "tags", "defense_notes", "position"})


def blank_workspace() -> dict[str, Any]:
    return {
        "profile": {"full_name": "", "email": "", "phone": "", "location": "", "links": []},
        "entries": [],
        "skills": [],
        "next_item_id": 1,
    }


def normalize(workspace: dict[str, Any] | None) -> dict[str, Any]:
    """Fill in anything missing and repair ``next_item_id``.

    The counter is raised past every id actually present. That protects against
    a hand-edited row or a half-finished import handing out an id that is
    already in use, which would silently merge two bullets.
    """
    state = blank_workspace() if not workspace else dict(workspace)
    profile = dict(state.get("profile") or {})
    for key in PROFILE_FIELDS:
        profile.setdefault(key, [] if key == "links" else "")
    state["profile"] = profile
    state["entries"] = list(state.get("entries") or [])
    state["skills"] = list(state.get("skills") or [])

    highest = 0
    for entry in state["entries"]:
        entry.setdefault("bullets", [])
        highest = max(highest, int(entry.get("id") or 0))
        for bullet in entry["bullets"]:
            highest = max(highest, int(bullet.get("id") or 0))
    for skill in state["skills"]:
        highest = max(highest, int(skill.get("id") or 0))
    state["next_item_id"] = max(int(state.get("next_item_id") or 1), highest + 1)
    return state


def allocate(workspace: dict[str, Any]) -> int:
    item_id = int(workspace["next_item_id"])
    workspace["next_item_id"] = item_id + 1
    return item_id


# -- profile -------------------------------------------------------------


def public_profile(workspace: dict[str, Any]) -> dict[str, Any]:
    # No "id": the SQLite table needed a singleton primary key, the jsonb column
    # does not, and the client never read it.
    return deepcopy(workspace["profile"])


def apply_profile(workspace: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    for key, value in fields.items():
        if key in PROFILE_FIELDS and value is not None:
            workspace["profile"][key] = value
    return public_profile(workspace)


# -- entries and bullets -------------------------------------------------


def _public_bullet(bullet: dict[str, Any], entry_id: int) -> dict[str, Any]:
    # entry_id is derived from the nesting rather than stored, so the two can
    # never disagree. It is emitted because the SQLite shape carried it.
    return deepcopy(bullet) | {"entry_id": entry_id}


def _public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(entry.get("bullets", []), key=lambda b: (b["position"], b["id"]))
    return deepcopy(entry) | {
        "bullets": [_public_bullet(bullet, entry["id"]) for bullet in ordered]
    }


def _find_entry(workspace: dict[str, Any], entry_id: int) -> dict[str, Any] | None:
    return next((e for e in workspace["entries"] if e["id"] == entry_id), None)


def _find_bullet(
    workspace: dict[str, Any], bullet_id: int
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for entry in workspace["entries"]:
        for bullet in entry.get("bullets", []):
            if bullet["id"] == bullet_id:
                return entry, bullet
    return None


def list_entries(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    # (position, id) across ALL entries, not within each kind. Position is
    # *allocated* per kind, so kinds interleave — sorting per kind and
    # concatenating produces a different order.
    ordered = sorted(workspace["entries"], key=lambda e: (e["position"], e["id"]))
    return [_public_entry(entry) for entry in ordered]


def add_entry(workspace: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    kind = fields["kind"]
    position = max(
        (e["position"] for e in workspace["entries"] if e["kind"] == kind),
        default=0,
    ) + 1
    entry = {
        "id": allocate(workspace),
        "kind": kind,
        "org": fields["org"],
        "role": fields.get("role", ""),
        "location": fields.get("location", ""),
        "start_date": fields.get("start_date", ""),
        "end_date": fields.get("end_date", ""),
        "url": fields.get("url", ""),
        "detail": fields.get("detail", ""),
        "position": position,
        "bullets": [],
    }
    workspace["entries"].append(entry)
    return _public_entry(entry)


def edit_entry(
    workspace: dict[str, Any], entry_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    entry = _find_entry(workspace, entry_id)
    if entry is None:
        return None
    for key, value in fields.items():
        if key in ENTRY_UPDATABLE and value is not None:
            entry[key] = value
    return _public_entry(entry)


def remove_entry(workspace: dict[str, Any], entry_id: int) -> bool:
    before = len(workspace["entries"])
    # Bullets are nested, so they go with the entry — SQLite got this from
    # ON DELETE CASCADE.
    workspace["entries"] = [e for e in workspace["entries"] if e["id"] != entry_id]
    return len(workspace["entries"]) != before


def add_bullet(
    workspace: dict[str, Any], entry_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    entry = _find_entry(workspace, entry_id)
    if entry is None:
        return None
    position = max((b["position"] for b in entry["bullets"]), default=0) + 1
    bullet = {
        "id": allocate(workspace),
        "text": fields["text"],
        "audience": fields.get("audience", "all"),
        "tags": fields.get("tags", []),
        "defense_notes": fields.get("defense_notes", ""),
        "position": position,
    }
    entry["bullets"].append(bullet)
    return _public_bullet(bullet, entry["id"])


def edit_bullet(
    workspace: dict[str, Any], bullet_id: int, fields: dict[str, Any]
) -> dict[str, Any] | None:
    found = _find_bullet(workspace, bullet_id)
    if found is None:
        return None
    entry, bullet = found
    for key, value in fields.items():
        if key in BULLET_UPDATABLE and value is not None:
            bullet[key] = value
    return _public_bullet(bullet, entry["id"])


def remove_bullet(workspace: dict[str, Any], bullet_id: int) -> bool:
    found = _find_bullet(workspace, bullet_id)
    if found is None:
        return False
    entry, bullet = found
    entry["bullets"] = [b for b in entry["bullets"] if b["id"] != bullet_id]
    return True


# -- skills --------------------------------------------------------------


def list_skills(workspace: dict[str, Any]) -> list[dict[str, Any]]:
    # SQLite's BINARY collation is byte order, which matches Python's default
    # string comparison. Do not casefold: that would reorder mixed-case names.
    return deepcopy(sorted(workspace["skills"], key=lambda s: (s["category"], s["name"])))


def add_skill(workspace: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any] | None:
    category, name = fields["category"], fields["name"]
    # UNIQUE (category, name) under BINARY collation is an exact match, so this
    # check is case-sensitive too. Returning None becomes a 409.
    if any(s["category"] == category and s["name"] == name for s in workspace["skills"]):
        return None
    skill = {
        "id": allocate(workspace),
        "category": category,
        "name": name,
        "audience": fields.get("audience", "all"),
    }
    workspace["skills"].append(skill)
    return deepcopy(skill)


def remove_skill(workspace: dict[str, Any], skill_id: int) -> bool:
    before = len(workspace["skills"])
    workspace["skills"] = [s for s in workspace["skills"] if s["id"] != skill_id]
    return len(workspace["skills"]) != before


# -- keyword coverage ----------------------------------------------------


def compute_coverage(
    job: dict[str, Any], skills: list[dict[str, Any]], shown_ids: set[int]
) -> dict[str, Any]:
    """Compare a JD against the skill bank and (optionally) one resume.

    matched: bank skills named in the JD and shown on the resume.
    have_not_shown: bank skills named in the JD but not on the resume —
      candidates to mirror in the JD's own words.
    missing: JD tech vocabulary with no bank skill at all. Never faked in;
      surfaced so the gap is a conscious decision.
    """
    jd = (job.get("jd_text") or "").lower()

    def mentioned(name: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", jd) is not None

    in_jd = [skill for skill in skills if skill["name"] and mentioned(skill["name"])]
    bank_names = {skill["name"].lower() for skill in skills}
    vocabulary_hits = sorted(
        term for term in TECH_VOCABULARY
        if term.lower() not in bank_names and mentioned(term)
    )
    return {
        "job_id": job["id"],
        "matched": [s for s in in_jd if s["id"] in shown_ids],
        "have_not_shown": [s for s in in_jd if s["id"] not in shown_ids],
        "missing": vocabulary_hits,
    }


# Tech terms a JD may name that deserve a "missing from bank" flag. Curated,
# not exhaustive — coverage of arbitrary vocabulary would need NLP and would
# invite keyword-stuffing, which the strategy doc explicitly rejects.
TECH_VOCABULARY = [
    "Python", "C", "C++", "C#", "Java", "JavaScript", "TypeScript", "Go", "Rust",
    "Kotlin", "Swift", "Scala", "R", "Julia", "MATLAB", "SQL", "Bash", "OCaml",
    "Haskell", "React", "Vue", "Angular", "Node.js", "Django", "Flask", "FastAPI",
    "Spring", "NumPy", "pandas", "SciPy", "scikit-learn", "PyTorch", "TensorFlow",
    "JAX", "Keras", "XGBoost", "Spark", "Hadoop", "Kafka", "Airflow", "dbt",
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "DuckDB", "Snowflake",
    "Docker", "Kubernetes", "Terraform", "AWS", "GCP", "Azure", "Linux", "Git",
    "CI/CD", "GraphQL", "gRPC", "REST", "WebSocket", "CUDA", "OpenMP", "MPI",
    "machine learning", "deep learning", "reinforcement learning", "NLP",
    "computer vision", "linear algebra", "probability", "statistics",
    "stochastic processes", "optimization", "numerical methods", "PDE",
    "Monte Carlo", "time series", "regression", "A/B testing", "data structures",
    "algorithms", "distributed systems", "operating systems", "networking",
    "concurrency", "functional programming", "object-oriented",
    # GPU / ML systems
    "Triton", "CUTLASS", "cuBLAS", "NCCL", "TensorRT", "vLLM", "SGLang", "PTX",
    "ROCm", "HIP", "MLIR", "LLVM", "XLA", "TVM", "Nsight", "GPU", "tensor cores",
    "kernel optimization", "quantization", "mixed precision", "FP8", "FP4", "GEMM",
    "attention", "transformers", "KV cache", "inference", "model serving",
    "batching", "roofline", "memory bandwidth", "SIMD", "vectorization", "HPC",
    "parallel computing", "RDMA", "InfiniBand", "profiling", "compilers",
    "computer architecture",
    # Low latency / trading systems
    "low latency", "lock-free", "kernel bypass", "DPDK", "io_uring", "NUMA",
    "cache optimization", "market data", "order book", "market microstructure",
    "FIX protocol", "ITCH", "floating point", "numerical linear algebra",
    "queueing theory",
    # Infrastructure
    "eBPF", "CMake", "Bazel", "gdb", "Raft", "consensus", "Prometheus", "Grafana",
]
