"""Record, Item and Feed -- plus the silver vocabulary. No logic, stdlib only.

`Record` is what crosses the source boundary: one source unit (a board, a
thread, an inbox query), demuxed into single-listing raw `Item`s with the
response envelope alongside. A source peels the vendor-specific transport layer
and translates nothing -- the payload it hands back is the payload the server
sent, so bronze (`raw_document`) is genuinely replayable and a parser bug is
fixed by re-running over stored payloads, never by re-fetching postings that
may have closed.

`Posting`, `Target`, `Label` and `Fact` are the silver vocabulary: what raw
items become downstream of bronze. They are produced by the parsing layer, not
by sources, and land in the canonical tables. (Where that layer lives is an
open item in ARCHITECTURE.md.)

Every type here is frozen. A source hands back a value, not a handle: nothing
downstream can mutate what a source observed, and nothing a source returns can
be quietly rewritten before it lands.

Field names track `schema.sql` where a column exists, so the mapping in
`store.py` stays mechanical. Columns the engine owns -- `content_hash`,
`title_normalized`, `first_seen_at`, `status`, `group_id` -- are deliberately
absent: a source reports what it saw, not what the database should conclude.
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

# Mirrors raw_document.kind: the vendor transport format the payload arrived
# in, not the role it plays (that is Record.envelope, routed separately).
# 'json' -> payload_json; 'xml' | 'html' | 'mime' -> payload_text -- a source
# must say which of the three, since router cannot tell them apart from a
# bare `str`.
Kind = Literal["json", "xml", "html", "mime"]

# Did we read the employer's own listing, or someone's copy of it? Secondhand
# never lands canonical; it is promoted by re-fetch at origin.
Fidelity = Literal["origin", "secondhand"]

# What the *source* says about the posting. Not posting.status, which the
# engine owns and which also has 'unlisted'.
PostState = Literal["open", "closed"]

# Who asserted a Label or Fact. Source-stated outranks model-inferred, which is
# the whole reason the field exists.
Provenance = Literal["source", "model", "human"]

# Mirrors posting.published_at_precision. Gates detect_latency_s so latency is
# never fabricated out of a "Posted Today".
Precision = Literal["exact", "date", "unknown"]


@dataclass(frozen=True, slots=True)
class Feed:
    """One fetchable endpoint, as a source sees it.

    A source reads `key` and `config` and nothing else. The four capability
    flags are the operator's assertions about the endpoint, not facts a source
    module may claim: a credentialed or incomplete feed will never close
    anything, so authed and partial sources need no special handling.

    `state` is engine-owned -- etag, last_modified, cursor,
    consecutive_failures. A source must not write it, and `config` and `state`
    stay separate columns precisely so rotating a credential cannot silently
    take the cursor with it.
    """

    id: int
    source: str  # resolves to a harvest function in sources.SOURCES
    key: str  # locates the endpoint: a token, a URL, a Gmail query
    config: dict  # operator-owned: credentials, URLs, per-feed parameters
    state: dict  # engine-owned; see above

    enabled: bool = True
    complete: bool = False  # the response is the full listing
    closes: bool = False  # absence may close postings (requires complete)
    credentialed: bool = False  # failure is expected on expiry; best-effort
    cadence_s: int = 900  # base poll interval; backoff writes over it
    next_poll_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Item:
    """One demuxed unit, verbatim -- one job or one detail fetch.

    `payload` is exactly what the source received for this unit, no field
    dropped and no value converted. That is what makes `raw_document` genuinely
    replayable: a parsing bug is fixed by re-running over stored `Item`s, never
    by re-fetching a posting that may have closed since.
    """

    external_id: str  # -> raw_document.external_id; text always, per Posting
    payload: dict | str  # -> payload_json / payload_text
    kind: Kind = "json"  # -> raw_document.kind; the source knows its own transport


@dataclass(frozen=True, slots=True)
class Record:
    """What one `harvest` call returns: one source unit, demuxed.

    A source's whole job is finding item boundaries and peeling the transport
    layer -- not translating. `items` holds one `Item` per listing; `envelope`
    holds response-root facts no single item carries (Workable's company name
    lives only here, on no element). `truncated` reports that *this fetch*
    did not finish (a paginated board that died halfway) and is distinct from
    `Feed.complete`, which is the operator's claim that the endpoint, when it
    succeeds, returns the full listing -- a truncated listing must never be
    returned as if whole.
    """

    items: list[Item] = field(default_factory=list)
    envelope: dict = field(default_factory=dict)
    truncated: bool = False
    http_status: int | None = None  # -> poll_run.http_status
    validators: dict = field(default_factory=dict)  # etag/last_modified -> feed.state


@dataclass(frozen=True, slots=True)
class Posting:
    """A job.

    `fidelity='origin'` means you fetched the employer's own listing. Anything
    else is `secondhand`, and must be accompanied by a `Target` for
    `origin_url` -- promotion happens by re-fetch, never by trust.

    `state='closed'` is a *stated* closure: an aggregator's `active: false`, a
    404 on a detail page, an explicit "this role is filled". It requires
    `closed_evidence`. Absence from a listing is not closure and is not yours
    to report; that is `reconcile`'s job and it runs only on feeds the operator
    flagged `complete` and `closes`.
    """

    external_id: str  # text always: int, uuid, slug and hash all appear
    title: str
    url_posting: str

    fidelity: Fidelity = "origin"
    state: PostState = "open"
    closed_evidence: str | None = None  # required when state == 'closed'

    # Resolution runs on this string. Most sources name the company on the
    # element itself; the rest carry it on the response root or in the feed.
    company_raw: str | None = None

    apply_url: str | None = None  # distinct from url_posting on 6/14 sources
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

    # Where to re-fetch this posting at origin. Meaningful only when
    # fidelity == 'secondhand', and the Target you emit alongside is what
    # actually schedules it.
    origin_url: str | None = None


@dataclass(frozen=True, slots=True)
class Target:
    """Something worth fetching later.

    A new board, an unprobed token, or a secondhand posting needing an origin
    re-fetch. A Target and a recurring feed are the same row in the same table,
    differing only by `once` -- which is why scheduling, backoff and one-shot
    dispatch share one code path and one policy function.
    """

    source: str  # the source it will be harvested as
    key: str
    config: dict = field(default_factory=dict)
    once: bool = True  # False makes this a recurring feed instead
    reason: str = ""  # 'origin_refetch' | 'discovered_board' | ...
    company_raw: str | None = None  # if the finder knew who it belongs to


@dataclass(frozen=True, slots=True)
class Label:
    """A fact about a posting: term, sponsorship, degree, workplace type.

    `provenance` is the point of the record. A source that *stated* the term
    outranks a model that guessed it, and both can be stored without either
    overwriting the other.
    """

    external_id: str  # identifies the posting within the emitting feed
    name: str
    value: str | int | bool | None
    provenance: Provenance = "source"
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class Fact:
    """A fact about a company: cooldown expiry, referral contact, headcount.

    Company-level facts reach the scorer, which is what makes "referral in
    hand" and "cooled down until March" scoreable. Human channels emit these
    through the same router as any source -- a referral is a hand-written Fact,
    not a module.
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
