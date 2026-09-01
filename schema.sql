-- xjobscore -- PostgreSQL 18
-- createdb xjobscore
-- psql -v ON_ERROR_STOP=1 --dbname=xjobscore --file=schema.sql

-- DO NOT EDIT, this is source of truth.

BEGIN;

-- USERS --
CREATE TABLE account (
    id          uuid PRIMARY KEY,   -- minted here, not by the provider: a Google
                                    -- `sub` is a numeric string, not a uuid
    profile     jsonb NOT NULL DEFAULT '{}',
    -- Identity, as returned by Google OIDC. NULL on the local sentinel account
    -- below, which predates authentication and belongs to no person.
    email       text UNIQUE,
    google_sub  text UNIQUE,        -- OIDC subject; stable across email changes
    name        text NOT NULL DEFAULT '',
    picture_url text NOT NULL DEFAULT '',
    created_at  timestamptz NOT NULL DEFAULT now()
);
-- Nobody's login: email and google_sub stay NULL, so no Google identity can ever
-- resolve to it. It survives as the default account_id of a PostgresRepository
-- or PostgresResumeStore constructed without one — a code path the API no longer
-- takes, but which tests use and which needs a real row to satisfy the foreign
-- keys. Nothing DEFAULTs to it any more: an unscoped write now fails outright.
INSERT INTO account (id)
VALUES ('00000000-0000-0000-0000-000000000001');

-- COMPANIES --
CREATE TABLE company (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               text NOT NULL,
    domain             text UNIQUE,
    domain_verified_at timestamptz, -- NULL if unverified
    description        text,        -- 6-8 words
    labels             jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(labels) = 'object') -- ck_list, bio_eng
);

CREATE TABLE company_alias (
    alias_norm text PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES company (id) ON DELETE CASCADE,
    alias      text NOT NULL
);
CREATE INDEX company_alias_company_idx ON company_alias (company_id);

-- FEEDS --
CREATE TABLE token (
    ats        text NOT NULL, -- name of vendor
    token      text NOT NULL,
    company_id bigint REFERENCES company (id) ON DELETE SET NULL,
    status     text NOT NULL DEFAULT 'unknown'
        CHECK (status IN ('unknown', 'live', 'dead')),
    muted      boolean NOT NULL DEFAULT false,
    -- Endpoint-specific adapter values the URL templates and params in
    -- sources/*-configs.py interpolate: a board hostname, an API token, a
    -- career-section URL. Operator-entered -- no code path writes it -- and
    -- never loaded from a checked-in catalog, because the worker and the
    -- inspector both read it and must agree. It rides with the token rather
    -- than the feed so that `token` + `company` reproduces a feed completely.
    config     jsonb NOT NULL DEFAULT '{}'::jsonb
        CONSTRAINT token_config_object CHECK (jsonb_typeof(config) = 'object'),
    
    -- discovery --
    source text,
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
    -- Per-tenant adapter values live on `token.config`, not here: `feed` is
    -- rebuilt from `token` by promotion, and config is the one input nothing
    -- can re-derive. See migrations/20260827_token_config.sql.
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
    paused_at       timestamptz, -- operator hold; the feed keeps polling=true and stays visible, but is never claimed

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
CREATE INDEX feed_due_idx ON feed (due_at) WHERE paused_at IS NULL;

-- One row per poll, append-only during normal operation. Dropping a feed
-- removes its observations through the foreign-key cascade; otherwise this is
-- the history the arrival-rate estimator, replay simulator, and feed sparkline read.
CREATE TABLE poll_observation (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feed_id      bigint NOT NULL REFERENCES feed (id) ON DELETE CASCADE,
    polled_at    timestamptz NOT NULL DEFAULT now(),
    -- Matches work.Outcome. No 'rate_limited': net.py absorbs 429s internally.
    status       text NOT NULL
        CHECK (status IN ('ok', 'not_modified', 'partial', 'error')),
    new_postings integer NOT NULL DEFAULT 0 CHECK (new_postings >= 0)  -- postings not seen before this poll
);
CREATE INDEX poll_observation_feed_idx ON poll_observation (feed_id, polled_at DESC);

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
-- `running` defaults to false: a database brought up to this schema polls
-- nothing until an operator says so, which matters when `feed.polling` covers
-- thousands of rows.
CREATE TABLE scheduler_control (
    id         boolean PRIMARY KEY DEFAULT true CHECK (id),
    running    boolean NOT NULL DEFAULT false,
    changed_at timestamptz NOT NULL DEFAULT now(),
    note       text
);
INSERT INTO scheduler_control (id) VALUES (true) ON CONFLICT DO NOTHING;

CREATE TABLE token_heartbeat (
    id                 boolean PRIMARY KEY DEFAULT true CHECK (id),
    -- The operator's runtime switch, driven from the Tokens page. Persistent
    -- so a process restart cannot silently undo a deliberate pause, and false
    -- by default because the heartbeat is on by default (unlike the poller).
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

-- Append-only: one row per liveness probe, written beside the token UPDATE
-- that records the same result. token.checked_at/status only ever hold the
-- latest answer, so this is the only place a token's history exists -- a
-- live -> dead -> live flip leaves three rows here and no trace there.
-- Deliberately five columns: when, which, what, and a stable tail order.
-- No FK: a log that a delete could cascade away is not a log.
CREATE TABLE token_log (
    id         bigserial PRIMARY KEY,
    checked_at timestamptz NOT NULL,
    ats        text NOT NULL,
    token      text NOT NULL,
    status     text NOT NULL CHECK (status IN ('unknown', 'live', 'dead'))
);

-- Deferred: adaptive polling. Intervals stay operator-set until poll_observation
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
        CHECK (status IN ('ok', 'not_modified', 'partial', 'error')),
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
    result_id      text NOT NULL UNIQUE,  -- keys /api/postings/{result_id}/items/{i}
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
    -- Traceability to the unmodified input
    raw_id                   bigint NOT NULL REFERENCES raw_posting(id),  -- raw version it produced current row
    dup_group_id             uuid,              -- groups suspected duplicates together
    -- Judgement, not a source fact: set by hand, never by the pipeline.
    removal_marked_at        timestamptz,       -- NULL = not marked for removal

    UNIQUE (source, source_key),
    CONSTRAINT job_posting_workday_source_lower
        CHECK (source NOT LIKE 'workday:%' OR source = lower(source))
);
COMMENT ON COLUMN job_posting.attributes IS
    'OneShot enrichment v1: version, term, remote, skills/meta, degree, summary/meta, deadline_at';
COMMENT ON COLUMN job_posting.degree_version IS
    'Taxonomy revision behind attributes.degree; NULL means unenriched or invalidated by an input change.';
COMMENT ON COLUMN job_posting.removal_marked_at IS
    'When a human marked this posting for removal; NULL means not marked. Never written by ingest.';
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
-- Supports the default "Newest first" page without sorting the entire active
-- opportunity pool before applying LIMIT.
CREATE INDEX job_posting_fresh_active_idx
    ON job_posting ((COALESCE(posted_at, first_seen_at)) DESC, id DESC)
    WHERE status = 'active';
CREATE INDEX job_posting_removal_marked_idx
    ON job_posting (removal_marked_at DESC)
    WHERE removal_marked_at IS NOT NULL;

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

-- RESUME STUDIO
-- The small, account-owned content bank is loaded and saved as one workspace.
-- Entries contain their bullets; every entry, bullet, and skill carries a stable
-- integer `id` allocated from next_item_id so existing API selections remain
-- valid without a table for each content type.
CREATE TABLE resume_workspace (
    account_id   uuid PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    profile      jsonb NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(profile) = 'object'),
    entries      jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(entries) = 'array'),
    skills       jsonb NOT NULL DEFAULT '[]'
        CHECK (jsonb_typeof(skills) = 'array'),
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

    UNIQUE (account_id, id)
);
CREATE INDEX resume_target_account_idx
    ON resume_target (account_id, created_at DESC, id DESC);

CREATE TABLE resume_document (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id        uuid NOT NULL REFERENCES account(id) ON DELETE CASCADE,
    target_job_id     bigint,
    name              text NOT NULL,
    variant           text NOT NULL DEFAULT 'swe'
        CHECK (variant IN ('mlsys', 'swe', 'quant')),
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
