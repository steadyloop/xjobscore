-- xjobscore -- PostgreSQL 18
-- DO NOT EDIT, this is source of truth. --

BEGIN;

-- USERS --
CREATE TABLE account (
    id          uuid PRIMARY KEY,
    profile     jsonb NOT NULL DEFAULT '{}',
    email       text UNIQUE,
    google_sub  text UNIQUE,        -- OIDC subject; stable across email changes
    name        text NOT NULL DEFAULT '',
    picture_url text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO account (id)
VALUES ('00000000-0000-0000-0000-000000000001');

-- COMPANIES --
CREATE TABLE company (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               text NOT NULL, -- the display name (canonical)
    domain             text UNIQUE,
    description        text,        -- 6-8 words
    discovered_from    text,
    created_at         timestamptz,
    -- Review verdict. A column rather than a `tags` key because every company
    -- has exactly one, drawn from a fixed set: `tags` cannot constrain that,
    -- and "unreviewed" as a missing key conflated absent/null/empty-document.
    -- Rejection is a label, never a DELETE -- job_posting_company_id_fkey is
    -- NO ACTION, so a company carrying postings refuses to be removed.
    reviewed           text NOT NULL DEFAULT 'pending'
        CONSTRAINT company_reviewed_state
        CHECK (reviewed IN ('pending', 'verified', 'rejected')),
    reviewed_at        timestamptz,
    reviewed_by        text,        -- 'human' | 'import'
    tags               jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(tags) = 'object') -- ck_list, bio_eng, startup
);

-- Only the rare verdicts are indexed: `pending` is the bulk of the table, and
-- the queue that reads it is already scanning for postings alongside.
CREATE INDEX company_reviewed ON company (reviewed) WHERE reviewed <> 'pending';

CREATE TABLE company_alias (
    alias_norm text PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    alias      text NOT NULL
);
CREATE INDEX company_alias_company_idx ON company_alias (company_id);

-- The 40px mark on an opportunity card, harvested from the company's own site.
CREATE TABLE company_logo (
    company_id bigint PRIMARY KEY REFERENCES company (id) ON DELETE CASCADE,
    bytes      bytea -- NULL records a domain that refused to answer, so rerun skips.
);

-- FEEDS --
CREATE TABLE token (
    ats        text NOT NULL, -- name of vendor
    token      text NOT NULL,
    company_id bigint REFERENCES company (id) ON DELETE SET NULL,
    status     text NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown', 'live', 'dead')),
    muted      boolean NOT NULL DEFAULT false,
    config     jsonb NOT NULL DEFAULT '{}'::jsonb
        CONSTRAINT token_config_object CHECK (jsonb_typeof(config) = 'object'),
    
    -- discovery --
    source        text,
    checked_at    timestamptz,
    discovered_at timestamptz,
    imported_at   timestamptz, -- set when a row is bulk-loaded from an external resolution (e.g. keyscan)

    -- system --
    tags text[],
    note text,

    PRIMARY KEY (ats, token),
    CONSTRAINT token_workday_source_renamed
        CHECK (ats <> 'workday-list'),
    CONSTRAINT token_workday_key_lower
        CHECK (ats <> 'workday' OR token = lower(token))
);

CREATE TABLE feed (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source   text NOT NULL, -- ashby, greenhouse, workable, etc.
    key      text NOT NULL, -- openai, anthropic, janestreet, etc.
    polling  boolean NOT NULL DEFAULT false,
    type         text NOT NULL DEFAULT 'ATS'
        CHECK (type IN ('ATS', 'Direct', 'Board')),
    -- Config
    interval_s      integer NOT NULL DEFAULT 900 CHECK (interval_s > 0),

    -- Queue
    due_at          timestamptz NOT NULL DEFAULT now(), -- when the feed is next due; this column is the queue
    hold_until      timestamptz,                         -- claim stamp so a crashed worker's feed gets retried, not lost

    -- Optimization
    etag            text,                                -- server's version token; 304 upon hit
    last_modified   text,
    res_hash        text,                                -- hash of listing response; skip parse when nothing moved
    last_change_at  timestamptz,                         -- last poll hash moved; back off keys on to slow a dormant board
    list_total      integer CHECK (list_total >= 0),     -- last complete listing's vendor-reported total
    last_full_poll_at timestamptz,                       -- last successful full reconciliation (probes/reuse do not advance it)

    -- Health
    strikes         smallint NOT NULL DEFAULT 0 CHECK (strikes >= 0), -- fail strike; drives retry backoff/disable
    last_poll_at    timestamptz,
    -- Vestigial: pause is `polling = false` now, and a held feed stays listed
    -- because the Scheduler's membership test is the row's existence, not the
    -- flag. Nothing writes this; see migrations/20260901_unify_pause_into_polling.sql.
    paused_at       timestamptz,

    -- Potential fields
    -- feed.cursor — only needed for incremental pagination; skip until an adapter demands it.
    -- feed.last_change_at — adaptive backoff for dormant boards is an optimization, not MVP.
    -- feed.status, feed.last_success_at, feed.consecutive_failures — redundant with paused_at, last_poll_at + last_error, and strikes respectively.

    UNIQUE (source, key),
    CONSTRAINT feed_token_identity_fkey
        FOREIGN KEY (source, key) REFERENCES token (ats, token) ON DELETE RESTRICT,
    CONSTRAINT feed_workday_key_lower
        CHECK (source <> 'workday' OR key = lower(key))
);
CREATE INDEX feed_due_idx ON feed (due_at) WHERE polling;
-- run.cycle's per-vendor worker pools each claim the oldest due feed *of one
-- source*, so that claim walks the queue per source rather than globally.
CREATE INDEX feed_source_due_idx ON feed (source, due_at) WHERE polling;

CREATE TABLE poll_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id      bigint NOT NULL REFERENCES feed (id) ON DELETE CASCADE,
    polled_at    timestamptz NOT NULL DEFAULT now(),
    status       text NOT NULL
        CHECK (status IN ('ok', 'unchanged', 'partial', 'error')),
    new_postings integer NOT NULL DEFAULT 0 CHECK (new_postings >= 0),  -- postings not seen before this poll
    status_code  smallint CHECK (status_code BETWEEN 100 AND 599)
);
CREATE INDEX poll_log_feed_idx ON poll_log (feed_id, polled_at DESC);

-- Global run/pause for the polling loop the inspector supervises in-process.
-- Single row by construction: the CHECK keeps `id` true, so there is exactly
-- one switch and no way to address a second one by mistake.
--
-- Persistent rather than in-process because both restart defaults are wrong
-- otherwise. Resuming automatically would undo a deliberate pause the moment
-- the process bounced; pausing automatically would silently stop a pipeline
-- nobody asked to stop. The flag outliving the process is what makes the
-- restart a non-event.
--
-- Fresh databases run immediately. Existing rows are never rewritten by
-- startup or migrations, so an operator's explicit pause still survives every
-- process restart.
CREATE TABLE scheduler_control (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    -- A fresh installation is live. An operator pause is stored here and is
    -- deliberately never overwritten by process startup or a migration.
    running    boolean NOT NULL DEFAULT true,
    changed_at timestamptz NOT NULL DEFAULT now(),
    note       text
);
INSERT INTO scheduler_control (id) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE token_heartbeat (
    id                 boolean PRIMARY KEY DEFAULT true CHECK (id),
    -- The operator's runtime switch, driven from the Tokens page. Persistent
    -- so a process restart cannot silently undo a deliberate pause. Both the
    -- heartbeat and source scheduler start live on a fresh database.
    paused             boolean NOT NULL DEFAULT false,
    paused_changed_at  timestamptz,
    due_at             timestamptz NOT NULL DEFAULT now(),
    hold_until         timestamptz,
    runner_seen_at     timestamptz,
    last_started_at    timestamptz,
    last_completed_at  timestamptz,
    last_outcome       text NOT NULL DEFAULT 'never'
        CHECK (last_outcome IN ('never', 'running', 'success', 'error')),
    last_candidates    integer NOT NULL DEFAULT 0 CHECK (last_candidates >= 0),
    last_validated     integer NOT NULL DEFAULT 0 CHECK (last_validated >= 0),
    last_promoted      integer NOT NULL DEFAULT 0 CHECK (last_promoted >= 0),
    last_rejected      integer NOT NULL DEFAULT 0 CHECK (last_rejected >= 0),
    last_error         text
);
INSERT INTO token_heartbeat (id) VALUES (true) ON CONFLICT DO NOTHING;

-- Durable coordination for the Gold work that cannot be scoped to one
-- posting. `generation` advances in the same transaction as changed Silver
-- rows; a dedup pass records the generation it actually observed, so changes
-- arriving during a rebuild cannot be mistaken for completed work.
CREATE TABLE gold_control (
    id                   boolean PRIMARY KEY DEFAULT true CHECK (id),
    generation           bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    processed_generation bigint NOT NULL DEFAULT 0
        CHECK (processed_generation >= 0 AND processed_generation <= generation),
    dirty_at             timestamptz,
    not_before           timestamptz,
    hold_until           timestamptz,
    last_started_at      timestamptz,
    last_completed_at    timestamptz,
    last_outcome         text NOT NULL DEFAULT 'never'
        CHECK (last_outcome IN ('never', 'running', 'success', 'error')),
    last_error           text
);
INSERT INTO gold_control (id) VALUES (true) ON CONFLICT DO NOTHING;

-- Bounded operational history for the unified runtime. Source-health history
-- remains in poll_log; this records the larger job and its stage-level detail.
CREATE TABLE system_run (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_type    text NOT NULL
        CHECK (job_type IN ('feed', 'gold_catchup', 'dedup', 'token_heartbeat')),
    identity    text,
    state       text NOT NULL DEFAULT 'running'
        CHECK (state IN ('running', 'complete', 'degraded', 'failed', 'interrupted')),
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(detail) = 'object'),
    error       text,
    CHECK ((state = 'running') = (finished_at IS NULL))
);
CREATE INDEX system_run_started_idx ON system_run (started_at DESC);

-- Append-only: one row per thing that happened to a token, written beside the
-- statement that made it happen. Two kinds of verb share the column:
--   unknown/live/dead   -- a liveness probe, beside the token UPDATE
--   feed_entered        -- a `feed` row was created for this identity
--   feed_exited         -- that row was deleted
-- token.checked_at/status only ever hold the latest answer, and `feed` records
-- neither birth nor death, so this is the only place either history exists --
-- a live -> dead -> live flip leaves three rows here and no trace there.
--
-- One verb column rather than status + event: a feed_entered row would have to
-- put something in a separate status, and whatever it put would either
-- duplicate the preceding probe row or make status mean two different things
-- depending on the row. The cost is that liveness at the moment of entry is not
-- on the event row; read it off that token's preceding probe row.
--
-- Deliberately five columns: when, which, what, and a stable tail order.
-- Ordered by id, never checked_at: a promote logs live and feed_entered in the
-- same statement, so their timestamps tie.
-- No FK: a log that a delete could cascade away is not a log, and feed_exited
-- exists precisely to outlive the row it describes.
CREATE TABLE token_log (
    id         bigserial PRIMARY KEY,
    checked_at timestamptz NOT NULL,
    ats        text NOT NULL,
    token      text NOT NULL,
    status     text NOT NULL CHECK (status IN ('unknown', 'live', 'dead', 'feed_entered', 'feed_exited'))
);

-- Deferred: adaptive polling. Intervals stay operator-set until poll_log
-- holds enough history to estimate an arrival rate from.
--
-- feed.interval_auto        boolean          -- false = operator pinned interval_s; scheduler leaves it alone
-- feed.decayed_arrivals     double precision -- EWMA posting count, decayed as of estimator_updated_at
-- feed.estimator_updated_at timestamptz      -- when decayed_arrivals was last decayed
--
-- CREATE TABLE vendor (   -- one row per ATS; learned limits survive restarts
--     name          text PRIMARY KEY,
--     rate_rps      double precision NOT NULL, -- halved on 429, crept up on success
--     blocked_until timestamptz                -- Retry-After; no polls until it passes
-- );

-- STAGING - For Inspector UI Feeds
CREATE TABLE fetched_feed (
    source      text NOT NULL,
    feed_key    text NOT NULL,
    -- Written for every attempt; an error or 304 stages no items. 'partial' = fetch did not finish.
    status      text NOT NULL
        CHECK (status IN ('ok', 'unchanged', 'partial', 'error')),
    http_status integer,
    -- Response-root facts no item carries (Workable's company name); parse() takes it as an argument.
    envelope    jsonb NOT NULL DEFAULT '{}',
    -- Request waterfall, timings, byte counts; the only place per-fetch request count is visible.
    network     jsonb NOT NULL DEFAULT '{}',
    fetched_at  timestamptz NOT NULL DEFAULT now(),
    -- Carried into feed.etag / feed.res_hash on promotion; first real poll costs a 304.
    etag        text,
    res_hash    text,

    -- NEW: what the inspector UI reads off a staged fetch, stored rather than derived.
    result_id      text NOT NULL UNIQUE,  -- keys /api/results/{feed_ref}/items/{i}
    feed_id        bigint NOT NULL,
    run_trigger    text NOT NULL DEFAULT 'manual',
    started_at     timestamptz NOT NULL,
    elapsed_ms     double precision NOT NULL,
    item_count     integer NOT NULL,
    raw_size_bytes bigint NOT NULL,       -- bytes on the wire
    size_bytes     bigint NOT NULL,       -- bytes stored
    truncated      boolean NOT NULL DEFAULT false,
    error_class    text,
    error_detail   text,

    PRIMARY KEY (source, feed_key)
);

-- What fetch returned (verbatim/ordered).
-- Keying by position allows harvests that emits same id twice (something parse() can't repair.)
CREATE TABLE fetched_feed_posting (
    source       text NOT NULL,
    feed_key     text NOT NULL,
    item_index   integer NOT NULL,  -- position in the response
    external_id  text NOT NULL,     -- becomes raw_posting.source_key
    kind         text NOT NULL CHECK (kind IN ('json', 'xml', 'html', 'mime')),
    -- Decoded, because parse() consumes it; non-json kinds land as a JSON string scalar.
    payload      jsonb NOT NULL,
    -- NEW: bronze-shaped wrapper the item pane renders; payload and envelope injected at display.
    raw_document jsonb NOT NULL,
    -- Reserved for detail reuse; see raw_posting.item_version. Nothing writes or reads it yet.
    item_version text,

    PRIMARY KEY (source, feed_key, item_index),
    FOREIGN KEY (source, feed_key)
        REFERENCES fetched_feed (source, feed_key) ON DELETE CASCADE
);

-- JOB POSTING - BRONZE
CREATE TABLE raw_posting (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       text NOT NULL,  -- ex) greenhouse:acme
    source_key   text NOT NULL,  -- source's identifer for the posting
    data         jsonb NOT NULL,  -- source specific posting object
    data_hash    text NOT NULL,  -- hash of data to avoid identical versions
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    item_version text,        -- not NULL for two-phase (list req + detail req/job) sources only.
                              -- whatever list offers: updated-at, revision number, hash of list row
                              -- equal to stored value means skip detail request.
    UNIQUE (source, source_key, data_hash),
    CONSTRAINT raw_posting_workday_source_lower
        CHECK (source NOT LIKE 'workday:%' OR source = lower(source))
);
-- Resolves "current payload per posting" for detail reuse; off until a reader needs it:
-- CREATE INDEX raw_posting_current_idx
--     ON raw_posting (source, source_key, fetched_at DESC);

-- JOB POSTING - SILVER
CREATE TABLE job_posting (
    id                       bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source                   text NOT NULL,
    source_key               text NOT NULL,
    company_id               bigint NOT NULL REFERENCES company(id),  -- not in content_hash
    ------------ STABLE (HASH) -------------
    title                    text NOT NULL,
    description              text,
    location_text            text,
    source_url               text,
    apply_url                text,
    posted_at                timestamptz,
    content_hash             text NOT NULL,     -- to check if fetch updated the posting,
    ------------ PARSED ---------------------
    remote                   text NOT NULL DEFAULT 'unknown'
        CHECK (remote IN ('yes', 'hybrid', 'unknown')),
    -- 'yes', never 'no': a stated full-time signal is not reliable enough to
    -- assert. Ashby types "Part-Time Studio Coordinator" as FullTime, so the
    -- absence of a part-time statement stays indistinguishable from no signal.
    -- Orthogonal to job_type on purpose: a part-time internship is `intern`
    -- here and 'yes' there, which the single-valued job_type could not say.
    part_time                text NOT NULL DEFAULT 'unknown'
        CHECK (part_time IN ('yes', 'unknown')),
    job_type                 text -- null means no signal
        CHECK (job_type IS NULL OR job_type IN (
            'intern',
            'coop',
            'student',
            'new_grad',
            'research'
        )),
    locations jsonb NOT NULL DEFAULT '[]'::jsonb,

    ------------- System ------------------
    status                   text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    first_seen_at            timestamptz NOT NULL DEFAULT now(),
    last_seen_at             timestamptz NOT NULL DEFAULT now(),
    ------------ Tracking ----------------
    vendor_closed_at         timestamptz,
    link_marked_at           timestamptz,
    link_checked_at          timestamptz,
    link_closed_at           timestamptz,
    ------------- Enriched (Gold) ---------
    attributes               jsonb NOT NULL DEFAULT '{"version": 1}'::jsonb
        CHECK (jsonb_typeof(attributes) = 'object')
        CHECK (attributes -> 'version' = '1'::jsonb)
        CHECK (
            NOT attributes ? 'skills'
            OR (
                jsonb_typeof(attributes -> 'skills') = 'array'
                AND NOT jsonb_path_exists(
                    attributes,
                    '$.skills[*] ? (@.type() != "string")'
                )
            )
        )
        CHECK (
            NOT attributes ? 'degree'
            OR (
                jsonb_typeof(attributes -> 'degree') = 'array'
                AND NOT jsonb_path_exists(
                    attributes,
                    '$.degree[*] ? (@.type() != "string")'
                )
            )
        )
        CHECK (
            NOT attributes ? 'degree_meta'
            OR jsonb_typeof(attributes -> 'degree_meta') = 'object'
        )
        CONSTRAINT job_posting_summary_type CHECK (
            NOT attributes ? 'summary'
            OR jsonb_typeof(attributes -> 'summary') IN ('string', 'null')
        )
        CONSTRAINT job_posting_summary_meta_type CHECK (
            NOT attributes ? 'summary_meta'
            OR jsonb_typeof(attributes -> 'summary_meta') = 'object'
        ),
    -- Taxonomy revision that produced attributes.degree. NULL means the row has
    -- never been enriched, or that ingest invalidated the tag because the title
    -- or description it was derived from changed. A scalar column, not a key in
    -- attributes: the enrichment loop scans it on every pass to find stale rows,
    -- and that predicate must stay indexable rather than digging through JSONB.
    degree_version           smallint,
    -- Gold title-domain classification populated by `--enrich job_type`.
    job_domain               text
        CHECK (job_domain IS NULL OR job_domain IN (
            'software',
            'finance',
            'unlabeled'
        )),
    job_domain_version       smallint,
    -- Traceability to the unmodified input
    raw_id                   bigint NOT NULL REFERENCES raw_posting(id),  -- raw version it produced current row
    dup_group_id             uuid,              -- groups suspected duplicates together
    -- Judgement, not a source fact: set by hand, never by the pipeline.
    removal_marked_at        timestamptz,       -- NULL = not marked for removal
    -- Source fact: the first complete poll of the posting's board that no longer
    -- listed it. last_seen_at is the last poll that did, so the disappearance
    -- lies between the two. Cleared when the board lists the posting again.
    missing_since            timestamptz,       -- NULL = still listed

    UNIQUE (source, source_key),
    CONSTRAINT job_posting_workday_source_lower
        CHECK (source NOT LIKE 'workday:%' OR source = lower(source))
);
COMMENT ON COLUMN job_posting.attributes IS
    'OneShot enrichment v1: version, term, remote, skills/meta, degree/meta, summary/meta, deadline_at';
COMMENT ON COLUMN job_posting.degree_version IS
    'Taxonomy revision behind attributes.degree; NULL means unenriched or invalidated by an input change.';
COMMENT ON COLUMN job_posting.job_domain IS
    'Gold title domain populated by --enrich job_type: software, finance, or unlabeled.';
COMMENT ON COLUMN job_posting.job_domain_version IS
    'Classifier revision behind job_domain; NULL means not yet enriched.';
COMMENT ON COLUMN job_posting.removal_marked_at IS
    'When a human marked this posting for removal; NULL means not marked. Never written by ingest.';
COMMENT ON COLUMN job_posting.missing_since IS
    'First complete board poll that no longer listed this posting; NULL while listed. A signal, not a closure: status is untouched.';
CREATE INDEX job_posting_attributes_idx ON job_posting USING gin (attributes);
CREATE INDEX job_posting_degree_active_idx
    ON job_posting USING gin ((attributes -> 'degree'))
    WHERE status = 'active';
-- The enrichment loop pages stale rows by id. Leading with the version keeps
-- the steady state -- every row already at the current revision -- an empty
-- index scan instead of a full pass over a 2 GB table.
CREATE INDEX job_posting_degree_version_idx ON job_posting (degree_version, id);
-- OneShot Opportunities filters this scalar on every list/count request.
CREATE INDEX job_posting_job_type_active_idx
    ON job_posting (job_type)
    WHERE status = 'active' AND job_type IS NOT NULL;
CREATE INDEX job_posting_domain_active_idx
    ON job_posting (job_domain, job_type)
    WHERE status = 'active' AND job_domain IS NOT NULL;
-- Supports the default "Newest first" page without sorting the entire active
-- opportunity pool before applying LIMIT.
CREATE INDEX job_posting_fresh_active_idx
    ON job_posting ((COALESCE(posted_at, first_seen_at)) DESC, id DESC)
    WHERE status = 'active';
CREATE INDEX job_posting_missing_since_idx
    ON job_posting (missing_since DESC)
    WHERE missing_since IS NOT NULL;
CREATE INDEX job_posting_removal_marked_idx
    ON job_posting (removal_marked_at DESC)
    WHERE removal_marked_at IS NOT NULL;
-- Scheduler telemetry (open counts, discovery p95) reads only these columns;
-- an index-only scan of this avoids a full pass over the wide heap.
CREATE INDEX job_posting_stats_idx
    ON job_posting (source, status, first_seen_at, posted_at);

-- DEDUP
CREATE TABLE job_posting_dup_group (
    id                 UUID PRIMARY KEY,
    primary_posting_id BIGINT REFERENCES job_posting(id) ON DELETE SET NULL,
    state              TEXT NOT NULL
        CHECK (state IN ('pending', 'confirmed', 'rejected')),
    evidence           JSONB
);
-- job_posting.dup_group_id <=> job_posting_dup_group.id
ALTER TABLE job_posting
    ADD FOREIGN KEY (dup_group_id)
    REFERENCES job_posting_dup_group(id);
CREATE INDEX job_posting_dup_group_id_idx ON job_posting (dup_group_id);

-- CURRENT SOURCE COVERAGE --
-- One replaceable document, produced by the poller and read by OneShot. The
-- pipeline tables remain authoritative; this row carries no snapshot history.
CREATE TABLE coverage_snapshot (
    id          boolean PRIMARY KEY DEFAULT true CHECK (id),
    payload     jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    computed_at timestamptz NOT NULL
);
INSERT INTO coverage_snapshot (id, payload, computed_at)
VALUES (
    true,
    '{"source_coverage":{"sources":[],"source_count":0,"total_coverage":0,"total_added_today":0},"board_map":{"providers":[],"totals":{"boards":0,"providers":0,"ok":0,"erroring":0,"stale":0,"paused":0,"never":0,"added_today":0}}}'::jsonb,
    now()
);

-- APPLICATION TRACKER
CREATE TABLE job_application (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- No DEFAULT, deliberately: every writer supplies an account. A DEFAULT here
    -- would turn a query that forgot to scope into a silent write against the
    -- shared sentinel row, which is how one user ends up seeing another's data.
    account_id         uuid NOT NULL REFERENCES account(id),
    job_posting_id     bigint REFERENCES job_posting(id),
    -- Job Specs
    company_snapshot   text NOT NULL,
    title_snapshot     text NOT NULL,
    location_snapshot  text,
    apply_url_snapshot text,
    resume_variant     text,

    -- Updates
    stage text NOT NULL DEFAULT 'considering'
        CHECK (stage IN (
            'considering',
            'applied',
            'assessment',
            'interview',
            'offer',
            'closed'
        )),
    closed_reason text
        CHECK (closed_reason IN (
            'rejected',
            'withdrawn',
            'no_reply'
        )),
    next_action text,
    note        text NOT NULL DEFAULT '',

    submitted_at timestamptz,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    next_due_at  timestamptz,

    CHECK (stage = 'closed' OR closed_reason IS NULL),
    UNIQUE (account_id, job_posting_id)
);
CREATE INDEX job_application_stage_due_idx
    ON job_application (account_id, stage, next_due_at, updated_at DESC);

CREATE TABLE job_application_event (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_application_id bigint NOT NULL REFERENCES job_application(id) ON DELETE CASCADE,
    event_type         text NOT NULL,
    to_stage           text
        CHECK (to_stage IN (
            'considering',
            'applied',
            'assessment',
            'interview',
            'offer',
            'closed'
        )),
    occurred_at        timestamptz NOT NULL DEFAULT now(),
    note               text
);
CREATE INDEX job_application_event_application_idx
    ON job_application_event (job_application_id, occurred_at DESC);

-- TRIAGE FEEDBACK
-- A row hides the posting (and its duplicate group) from this account's
-- Opportunities feed; deleting it restores the posting. `mislabeled` rows are
-- also the account's label corrections, kept for the classifier eval.
CREATE TABLE job_feedback (
    account_id     uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    job_posting_id bigint NOT NULL REFERENCES job_posting(id),
    kind           text NOT NULL
        CHECK (kind IN ('skip', 'mislabeled')),
    -- Optional, and only a skip carries one.
    skip_reason    text
        CHECK (skip_reason IN ('level', 'location', 'field', 'company')),
    -- [{field, from, to}]: field is type | focus | company | location | skills,
    -- `to` is null when the reporter didn't say what it should be.
    corrections    jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(corrections) = 'array'),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, job_posting_id),
    CHECK (kind = 'skip' OR skip_reason IS NULL),
    CHECK (kind = 'mislabeled' OR corrections = '[]'::jsonb)
);
CREATE INDEX job_feedback_recent_idx
    ON job_feedback (account_id, created_at DESC);

-- RESUME STUDIO
CREATE TABLE resume_workspace (
    account_id   uuid PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    profile      jsonb NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(profile) = 'object'),
    entries      jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(entries) = 'array'),
    skills       jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(skills) = 'array'),
    variants     jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(variants) = 'array'),
    next_item_id bigint NOT NULL DEFAULT 1 CHECK (next_item_id > 0),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE resume_target (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id  uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    company     text NOT NULL,
    title       text NOT NULL,
    url         text NOT NULL DEFAULT '',
    jd_text     text NOT NULL DEFAULT '',
    date_posted text NOT NULL DEFAULT '',
    note        text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- The queued application this target was built from, so tailoring a resume
    -- for a tracked job is a link rather than a retype. NULL for a target typed
    -- in by hand. ON DELETE SET NULL: dropping an application must not discard
    -- the resume work done for it.
    job_application_id bigint REFERENCES job_application(id) ON DELETE SET NULL,

    UNIQUE (account_id, id)
);
CREATE INDEX resume_target_account_idx
    ON resume_target (account_id, created_at DESC, id DESC);
-- Makes "tailor a resume for this application" idempotent: the second click
-- finds this row instead of creating a near-duplicate target.
CREATE UNIQUE INDEX resume_target_account_application_key
    ON resume_target (account_id, job_application_id)
    WHERE job_application_id IS NOT NULL;

CREATE TABLE resume_document (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id        uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    target_job_id     bigint,
    name              text NOT NULL,
    -- No CHECK: valid values are the keys in resume_workspace.variants, which
    -- each account defines for itself. Validated in the store, where that list
    -- is readable; '' means no variant selected.
    variant           text NOT NULL DEFAULT '',
    -- The typeset design; the list lives in resume_models.Template.
    template          text NOT NULL DEFAULT 'main'
        CHECK (template IN ('main', 'barlow')),
    availability_line text NOT NULL DEFAULT '',
    education_note    text NOT NULL DEFAULT '',
    selection         jsonb NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(selection) = 'object'),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (account_id, target_job_id)
        REFERENCES resume_target(account_id, id)
        ON DELETE SET NULL (target_job_id)
);
CREATE INDEX resume_document_account_updated_idx
    ON resume_document (account_id, updated_at DESC, id DESC);


-- EXPERIMENTS --
CREATE TABLE job_classification_experiment (
    job_id              TEXT PRIMARY KEY,
    content_hash        TEXT UNIQUE NOT NULL,
    title               TEXT NOT NULL,
    company             TEXT,
    location            TEXT,
    posting_text        TEXT NOT NULL,
    dataset_split       TEXT CHECK (dataset_split IN ('train', 'validation', 'test')),

    -- Human ground truth
    gold_internship     BOOLEAN,
    gold_cs             BOOLEAN,
    gold_bioe           BOOLEAN,
    gold_ambiguous      BOOLEAN DEFAULT FALSE,
    gold_notes          TEXT,

    -- TF-IDF
    tfidf_internship    REAL,
    tfidf_cs            REAL,
    tfidf_bioe          REAL,
    tfidf_version       TEXT,
    tfidf_latency_ms    REAL,

    -- ModernBERT
    modernbert_internship REAL,
    modernbert_cs         REAL,
    modernbert_bioe       REAL,
    modernbert_version    TEXT,
    modernbert_latency_ms REAL,

    -- Frontier + rules
    frontier_internship REAL,
    frontier_cs         REAL,
    frontier_bioe       REAL,
    frontier_version    TEXT,
    frontier_latency_ms REAL,

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

COMMIT;
