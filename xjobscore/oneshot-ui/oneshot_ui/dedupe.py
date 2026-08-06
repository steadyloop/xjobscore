"""Safe, reversible cross-source posting identity decisions.

This module returns evidence-bearing decisions.  It never mutates observations
or materializes groups, so every suggested link can be replayed or reversed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEDUPE_VERSION = "posting-resolution/1.0"

_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "gh_jid_source",
    "gh_src",
    "lever-origin",
    "lever-source",
    "source",
    "trk",
}


class DedupeOutcome(str, Enum):
    EXACT = "exact"
    REVIEW = "review"
    NEW = "new"
    RELATED = "related"


@dataclass(frozen=True, slots=True, order=True)
class PostingIdentifier:
    namespace: str
    scope: str
    value: str
    provenance: str = "parsed"

    @property
    def key(self) -> tuple[str, str, str]:
        return self.namespace, self.scope, self.value


@dataclass(frozen=True, slots=True)
class PostingIdentity:
    source_instance: str
    external_id: str
    employer_id: str | None
    title: str
    identifiers: tuple[PostingIdentifier, ...] = ()
    location: str | None = None
    employment_type: str | None = None
    published_at: date | datetime | None = None


@dataclass(frozen=True, slots=True)
class DedupeDecision:
    outcome: DedupeOutcome
    auto_merge: bool
    evidence: tuple[str, ...]
    shared_identifiers: tuple[PostingIdentifier, ...] = ()
    algorithm_version: str = DEDUPE_VERSION


def normalize_url(url: str) -> str:
    """Apply conservative URL normalization without fetching or redirecting."""

    raw = url.strip()
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    if not parsed.hostname:
        raise ValueError(f"URL has no hostname: {url!r}")
    scheme = parsed.scheme.casefold()
    if scheme not in {"http", "https"}:
        raise ValueError("only HTTP(S) posting URLs can be normalized")
    host = parsed.hostname.casefold().rstrip(".")
    port = parsed.port
    netloc = host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    query.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlunsplit((scheme, netloc, path, urlencode(query, doseq=True), ""))


def _url_identifier(url: str) -> PostingIdentifier:
    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    return PostingIdentifier("canonical_url", parsed.hostname or "", normalized, "url")


def extract_url_identifiers(url: str) -> tuple[PostingIdentifier, ...]:
    """Extract scoped ATS IDs plus a safely normalized URL identifier."""

    normalized = normalize_url(url)
    parsed = urlsplit(normalized)
    host = parsed.hostname or ""
    parts = [part for part in parsed.path.split("/") if part]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    identifiers: list[PostingIdentifier] = [_url_identifier(normalized)]

    def add(namespace: str, scope: str | None, value: str | None) -> None:
        if scope and value:
            identifiers.append(PostingIdentifier(namespace, scope.casefold(), value, "url"))

    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        tenant = parts[0] if parts and parts[0] not in {"embed", "jobs"} else query.get("for")
        job_id = None
        if "jobs" in parts:
            index = parts.index("jobs")
            if index + 1 < len(parts):
                job_id = parts[index + 1]
        job_id = job_id or query.get("token") or query.get("gh_jid")
        add("greenhouse", tenant, job_id)
    elif host == "jobs.lever.co" and len(parts) >= 2:
        add("lever", parts[0], parts[1])
    elif host == "jobs.ashbyhq.com" and len(parts) >= 2:
        add("ashby", parts[0], parts[1])
    elif host == "apply.workable.com" and len(parts) >= 3 and parts[1].casefold() == "j":
        add("workable", parts[0], parts[2])
    elif host == "jobs.smartrecruiters.com" and len(parts) >= 2:
        add("smartrecruiters", parts[0], parts[1].split("-", 1)[0])
    elif host.endswith("myworkdayjobs.com"):
        tenant = host.split(".", 1)[0]
        value = None
        if parts:
            match = re.search(r"_([A-Za-z][A-Za-z0-9_-]*)$", parts[-1])
            value = match.group(1) if match else parts[-1]
        add("workday", tenant, value)
    # ``*.icims.com`` is a portal hostname, not the token for iCIMS's
    # separate ``careers.<token>.com`` JSON board.  Do not manufacture an
    # ATS identity from an identifier that cannot be converted reliably.

    return tuple(sorted(set(identifiers)))


def build_identifiers(
    *,
    urls: tuple[str, ...] = (),
    employer_id: str | None = None,
    requisition_id: str | None = None,
) -> tuple[PostingIdentifier, ...]:
    identifiers: set[PostingIdentifier] = set()
    for url in urls:
        identifiers.update(extract_url_identifiers(url))
    if requisition_id and employer_id:
        value = re.sub(r"\s+", "", requisition_id).casefold()
        identifiers.add(PostingIdentifier("employer_req", employer_id, value, "source"))
    return tuple(sorted(identifiers))


def normalize_title(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^a-z0-9]+", " ", title).strip()


def _identifier_map(value: PostingIdentity) -> dict[tuple[str, str], set[str]]:
    result: dict[tuple[str, str], set[str]] = {}
    for identifier in value.identifiers:
        result.setdefault((identifier.namespace, identifier.scope), set()).add(identifier.value)
    return result


def _conflicts(left: PostingIdentity, right: PostingIdentity) -> tuple[str, ...]:
    conflicts: list[str] = []
    left_map = _identifier_map(left)
    right_map = _identifier_map(right)
    for namespace_scope in sorted(left_map.keys() & right_map.keys()):
        namespace, scope = namespace_scope
        if namespace == "canonical_url":
            continue
        if left_map[namespace_scope].isdisjoint(right_map[namespace_scope]):
            conflicts.append(f"different {namespace} identifiers in scope {scope}")
    if left.employer_id and right.employer_id and left.employer_id != right.employer_id:
        conflicts.append("resolved employers differ")
    if left.employment_type and right.employment_type and left.employment_type.casefold() != right.employment_type.casefold():
        conflicts.append("employment types differ")
    return tuple(conflicts)


def compare_postings(left: PostingIdentity, right: PostingIdentity) -> DedupeDecision:
    """Return exact/review/new/related without performing a merge."""

    left_by_key = {item.key: item for item in left.identifiers}
    right_keys = {item.key for item in right.identifiers}
    shared = tuple(sorted(left_by_key[key] for key in left_by_key.keys() & right_keys))
    conflicts = _conflicts(left, right)
    if shared:
        if conflicts:
            return DedupeDecision(
                DedupeOutcome.REVIEW,
                False,
                ("deterministic identifier match is contradicted", *conflicts),
                shared,
            )
        return DedupeDecision(
            DedupeOutcome.EXACT,
            True,
            tuple(f"shared {item.namespace}:{item.scope}:{item.value}" for item in shared),
            shared,
        )

    same_employer = bool(left.employer_id and left.employer_id == right.employer_id)
    if not same_employer:
        return DedupeDecision(DedupeOutcome.NEW, False, ("no scoped deterministic identifier match", *conflicts))

    identifier_conflict = any(
        reason.startswith("different employer_req")
        or reason.startswith("different greenhouse")
        or reason.startswith("different lever")
        or reason.startswith("different ashby")
        or reason.startswith("different workday")
        for reason in conflicts
    )
    if identifier_conflict:
        return DedupeDecision(
            DedupeOutcome.RELATED,
            False,
            ("same employer but scoped requisition evidence identifies distinct roles", *conflicts),
        )

    left_title = normalize_title(left.title)
    right_title = normalize_title(right.title)
    similarity = SequenceMatcher(None, left_title, right_title).ratio() if left_title and right_title else 0.0
    if similarity >= 0.86:
        return DedupeDecision(
            DedupeOutcome.REVIEW,
            False,
            (f"same employer and similar title ({similarity:.3f}) are insufficient for automatic linking",),
        )
    if similarity >= 0.55:
        return DedupeDecision(
            DedupeOutcome.RELATED,
            False,
            (f"same employer and possibly related title ({similarity:.3f})",),
        )
    return DedupeDecision(DedupeOutcome.NEW, False, ("same employer but no convincing role match",))


def safe_cluster_decision(candidate: PostingIdentity, members: tuple[PostingIdentity, ...]) -> DedupeDecision:
    """Block unsafe transitivity by checking a candidate against every member.

    An exact edge to one member is not allowed to pull a fuzzy or contradictory
    edge into the connected component.  Automatic grouping requires an exact,
    conflict-free decision against every existing member.
    """

    if not members:
        return DedupeDecision(DedupeOutcome.NEW, False, ("cluster is empty",))
    decisions = tuple(compare_postings(candidate, member) for member in members)
    if all(decision.outcome is DedupeOutcome.EXACT and decision.auto_merge for decision in decisions):
        shared = tuple(sorted({item for decision in decisions for item in decision.shared_identifiers}))
        return DedupeDecision(DedupeOutcome.EXACT, True, ("exact against every cluster member",), shared)
    if any(decision.outcome is DedupeOutcome.EXACT for decision in decisions):
        return DedupeDecision(
            DedupeOutcome.REVIEW,
            False,
            ("unsafe transitivity blocked: exact for only part of the cluster",),
        )
    if any(decision.outcome is DedupeOutcome.REVIEW for decision in decisions):
        return DedupeDecision(DedupeOutcome.REVIEW, False, ("one or more cluster edges require review",))
    if any(decision.outcome is DedupeOutcome.RELATED for decision in decisions):
        return DedupeDecision(DedupeOutcome.RELATED, False, ("related observations remain separate",))
    return DedupeDecision(DedupeOutcome.NEW, False, ("no cluster member matched",))


__all__ = [
    "DEDUPE_VERSION",
    "DedupeDecision",
    "DedupeOutcome",
    "PostingIdentifier",
    "PostingIdentity",
    "build_identifiers",
    "compare_postings",
    "extract_url_identifiers",
    "normalize_title",
    "normalize_url",
    "safe_cluster_decision",
]
