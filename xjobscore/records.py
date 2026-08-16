"""Record, Item and Feed -- plus the silver vocabulary. No logic, stdlib only.

`Record` is what crosses the source boundary: one source unit (a board, a
thread, an inbox query), demuxed into single-listing raw `Item`s with the
response envelope alongside. A source peels the vendor-specific transport layer
and translates nothing -- the payload it hands back is the payload the server
sent, so bronze (`raw_posting`) is genuinely replayable and a parser bug is
fixed by re-running over stored payloads, never by re-fetching postings that
may have closed.

`Posting`, `Target`, `Label` and `Fact` are the parser vocabulary: what raw
items become downstream of bronze. `Posting` currently lands in
`job_posting`; the other outputs remain available to adapters but are counted
as unsupported until schema.sql gives them durable storage.

Every type here is frozen. A source hands back a value, not a handle: nothing
downstream can mutate what a source observed, and nothing a source returns can
be quietly rewritten before it lands.

Field names track `schema.sql` where a column exists, so the mapping in
`silver.py` stays mechanical. Columns the engine owns -- `content_hash`,
`first_seen_at`, and canonical `status` -- are deliberately absent: a source
reports what it saw, not what the database should conclude.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

__all__ = [
    "Canonical",
    "Fact",
    "Feed",
    "Fidelity",
    "Item",
    "Kind",
    "Label",
    "PostState",
    "Posting",
    "Precision",
    "Provenance",
    "Record",
    "Target",
]

# The vendor transport format stored inside raw_posting.data's versioned
# wrapper. A source must distinguish XML, HTML, and MIME because router cannot
# infer the format from a bare string.
Kind = Literal["json", "xml", "html", "mime"]

# Did we read the employer's own listing, or someone's copy of it? Secondhand
# remains bronze-only until schema.sql has an explicit provenance model.
Fidelity = Literal["origin", "secondhand"]

# What the *source* says about the posting. The engine maps this to the smaller
# active/closed job_posting.status vocabulary.
PostState = Literal["open", "closed"]

# Who asserted a Label or Fact. Source-stated outranks model-inferred, which is
# the whole reason the field exists.
Provenance = Literal["source", "model", "human"]

# Parser metadata retained for date-quality decisions; schema.sql currently
# stores only the resulting posted_at value.
Precision = Literal["exact", "date", "unknown"]


@dataclass(frozen=True, slots=True)
class Feed:
    """One fetchable endpoint, as a source sees it.

    A source reads ``key``, ``config``, and ``etag``. Company and scheduling
    fields are carried for the orchestrator and ignored by harvesters.

    Runtime configuration comes from the validated registry. Scheduling state
    comes from schema.sql's ``schedule`` row; only the ETag is exposed to a
    source because it is the only conditional-request validator the schema
    persists.
    """

    id: int
    source: str  # resolves to a harvest function in sources.SOURCES
    key: str  # locates the endpoint: a token, a URL, a Gmail query
    config: dict = field(default_factory=dict)  # registry-owned adapter parameters

    enabled: bool = True
    company_id: int | None = None
    company_name: str | None = None
    source_type: str = "Other"
    etag: str | None = None
    res_hash: str | None = None
    interval_s: int = 900
    strikes: int = 0
    due_at: datetime | None = None
    hold_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class Item:
    """One demuxed unit, verbatim -- one job or one detail fetch.

    `payload` is exactly what the source received for this unit, no field
    dropped and no value converted. That is what makes `raw_posting` genuinely
    replayable: a parsing bug is fixed by re-running over stored `Item`s, never
    by re-fetching a posting that may have closed since.
    """

    external_id: str  # -> raw_posting.source_key; text always, per Posting
    payload: dict | str  # -> raw_posting.data.payload
    kind: Kind = "json"  # -> raw_posting.data.kind


@dataclass(frozen=True, slots=True)
class Record:
    """What one `harvest` call returns: one source unit, demuxed.

    A source's whole job is finding item boundaries and peeling the transport
    layer -- not translating. `items` holds one `Item` per listing; `envelope`
    holds response-root facts no single item carries (Workable's company name
    lives only here, on no element). `truncated` reports that *this fetch*
    did not finish (a paginated board that died halfway) and is distinct from
    scheduler state. A truncated listing must never be returned as if whole.
    """

    items: list[Item] = field(default_factory=list)
    envelope: dict = field(default_factory=dict)
    truncated: bool = False
    http_status: int | None = None  # diagnostics/logging; schedule has no status column
    etag: str | None = None  # response ETag -> schedule.etag
    # Inspector-only incremental harvests may carry forward a detail payload
    # whose stable list/detail identity fields prove it unchanged. Production
    # harvesters leave this at zero; it is diagnostics only.
    reused_item_count: int = 0
    enumerated_item_count: int = 0
    partition_count: int = 0
    detail_fetched_count: int = 0
    result_window_truncated: bool = False


@dataclass(frozen=True, slots=True)
class Posting:
    """A job.
    `state='closed'` is a *stated* closure: an aggregator's `active: false`, a
    404 on a detail page, an explicit "this role is filled". It requires
    `closed_evidence`. Absence from a listing is not closure; reconciliation
    remains deferred until schema.sql has an explicit completeness model.
    """

    external_id: str  # source_key
    
    title: str        # title
    url_posting: str  # source_url

    fidelity: Fidelity = "origin" # unused
    state: PostState = "open"     # status
    closed_evidence: str | None = None

    company_raw: str | None = None

    apply_url: str | None = None  # apply_url
    location_raw: str | None = None

    # NULL marks a pointer row (aggregator, email): no body means it cannot be
    # labeled or scored, so it has to resolve to an origin feed.
    description_html: str | None = None

    requisition_id: str | None = None  # employer-assigned, cross-ATS dedup key
    employment_type: str | None = None  # raw string, unmapped
    workplace_type_raw: str | None = None  # the normalized value is a Label

    published_at: datetime | None = None
    published_at_precision: Precision = "unknown"
    source_updated_at: datetime | None = None
    deadline_at: datetime | None = None
    source_active: bool | None = None  # close on fact, not on inference

    locations_extra: list = field(default_factory=list)
    content_sections: list = field(default_factory=list)
    source_extra: dict = field(default_factory=dict)  # the verbatim long tail

    # A parser hint retained for future provenance support. It does not create
    # a feed; only the registry controls feed inventory.
    origin_url: str | None = None


@dataclass(frozen=True, slots=True)
class Target:
    """Something worth fetching later.

    Kept in the parser contract for adapter compatibility. The registry owns
    feed inventory, so the core reports Target outputs as unsupported and
    never creates runtime feeds from them.
    """

    source: str  # the source it will be harvested as
    key: str
    config: dict = field(default_factory=dict)
    once: bool = True  # compatibility metadata; scheduler ignores Target
    reason: str = ""  # 'origin_refetch' | 'discovered_board' | ...
    company_raw: str | None = None  # if the finder knew who it belongs to


@dataclass(frozen=True, slots=True)
class Label:
    """A fact about a posting: term, sponsorship, degree, workplace type.

    `provenance` remains useful to adapters even though schema.sql currently
    has no label table. The core counts this output as unsupported.
    """

    external_id: str  # identifies the posting within the emitting feed
    name: str
    value: str | int | bool | None
    provenance: Provenance = "source"
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class Fact:
    """A fact about a company: cooldown expiry, referral contact, headcount.

    Kept for parser compatibility. schema.sql currently has no fact table, so
    the core counts this output as unsupported.
    """

    company_raw: str
    name: str  # 'cooldown_until' | 'referral' | 'one_shot_spent' | 'headcount'
    value: str | int | bool | None
    provenance: Provenance = "source"
    expires_at: datetime | None = None


# The silver vocabulary: what a raw Item becomes downstream of bronze. Closed
# on purpose -- a new kind of fact is a design conversation, not an import.
# Not `harvest`'s return type; `harvest` returns a `Record` (see above).
Canonical = Posting | Target | Label | Fact
