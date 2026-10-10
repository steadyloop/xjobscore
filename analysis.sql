-- Closed-posting research archive, separate from ingestion and Console telemetry.
-- Apply/populate: psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f analysis.sql
-- Subsequent imports: SELECT analysis.refresh_closed_jobs();
-- One row per source posting, not an inferred hire or a deduplicated vacancy.
-- Dates are first detected closures; employer closure times are not available.
-- Snapshot fields survive deletion or modification of the ingestion posting.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '120s';

CREATE SCHEMA IF NOT EXISTS analysis;

CREATE TABLE IF NOT EXISTS analysis.closed_job (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_posting_id     bigint NOT NULL,
    source                text NOT NULL,
    source_key            text NOT NULL,
    company_id            bigint NOT NULL,
    company_name          text NOT NULL,
    company_domain        text,
    title                 text NOT NULL,
    role_family           text NOT NULL,
    role_family_version   smallint NOT NULL DEFAULT 1,
    description           text,
    location_text         text,
    locations             jsonb NOT NULL DEFAULT '[]',
    remote                text NOT NULL,
    job_type              text,
    job_domain            text,
    source_url            text,
    apply_url             text,
    posted_at             timestamptz,
    first_seen_at         timestamptz NOT NULL,
    last_seen_at          timestamptz NOT NULL,
    closed_at             timestamptz NOT NULL,
    ats_closed_at         timestamptz,
    linked_dead_at        timestamptz,
    paged_closed_at       timestamptz,
    duplicate_group_id    uuid,
    archived_at           timestamptz NOT NULL DEFAULT now(),
    refreshed_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source, source_key)
);

COMMENT ON TABLE analysis.closed_job IS
    'Research snapshots of evidenced source-posting closures. Intentionally no ingestion foreign keys: deletion there must not erase research history.';
COMMENT ON COLUMN analysis.closed_job.closed_at IS
    'Earliest observed ATS, dead-link or page closure; not the employer''s actual closure time.';
COMMENT ON COLUMN analysis.closed_job.role_family IS
    'Broad title-derived research grouping, v1; original title is retained. Other roles means no supported match.';

CREATE INDEX IF NOT EXISTS closed_job_time_idx
    ON analysis.closed_job(closed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS closed_job_company_time_idx
    ON analysis.closed_job(company_id, closed_at DESC);
CREATE INDEX IF NOT EXISTS closed_job_role_time_idx
    ON analysis.closed_job(role_family, closed_at DESC);
CREATE INDEX IF NOT EXISTS closed_job_search_idx
    ON analysis.closed_job USING gin
    (to_tsvector('simple',company_name || ' ' || title || ' ' || coalesce(location_text,'')));

-- Research groups deliberately use the title, not sparse student-only job_domain.
-- Classification is broad, deterministic, versioned, and queryable alongside
-- the original title. Data/AI precedes software, and engineering precedes sales.
CREATE OR REPLACE FUNCTION analysis.role_family(title text)
RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
      WHEN title ~* '\m(data (scientist|engineer|analyst)|machine learning|artificial intelligence|deep learning|nlp|computer vision|analytics)\M'
        THEN 'Data & AI'
      WHEN title ~* '\m(software|developer|devops|site reliability|sre|swe|sde|frontend|backend|front.end|back.end|full.stack|firmware)\M'
        THEN 'Software engineering'
      WHEN title ~* '\m(nurse|nursing|physician|doctor|pharmacist|therapist|medical assistant|clinical)\M'
        THEN 'Healthcare & clinical'
      WHEN title ~* '\m(product (manager|management|designer)|ux|ui|user experience|graphic design)\M'
        THEN 'Product & design'
      WHEN title ~* '\m(cybersecurity|cyber security|security (engineer|analyst)|system administrator|systems administrator|it support|help desk|network (engineer|administrator))\M'
        THEN 'IT & security'
      WHEN title ~* '\m(engineer|engineering|architect|technician|mechanic|electrician)\M'
        THEN 'Engineering & technical'
      WHEN title ~* '\m(accountant|accounting|finance|financial|auditor|audit|treasury|investment|banker|trader|trading|actuarial)\M'
        THEN 'Finance & accounting'
      WHEN title ~* '\m(research|scientist|laboratory|lab (assistant|technician)|biologist|chemist)\M'
        THEN 'Research & science'
      WHEN title ~* '\m(human resources|recruiter|recruiting|talent acquisition|people operations|hr)\M'
        THEN 'People & recruiting'
      WHEN title ~* '\m(marketing|communications|copywriter|content|brand manager)\M'
        THEN 'Marketing & communications'
      WHEN title ~* '\m(sales|account executive|account manager|customer success|customer service|business development)\M'
        THEN 'Sales & customer'
      WHEN title ~* '\m(operations|supply chain|logistics|warehouse|procurement|purchasing|inventory|production|manufacturing)\M'
        THEN 'Operations & supply chain'
      ELSE 'Other roles' END
$$;

CREATE OR REPLACE FUNCTION analysis.refresh_closed_jobs()
RETURNS bigint LANGUAGE plpgsql SET search_path=pg_catalog,analysis AS $$
DECLARE affected bigint;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('analysis.refresh_closed_jobs'));
    INSERT INTO analysis.closed_job (
        source_posting_id,source,source_key,company_id,company_name,company_domain,
        title,role_family,description,location_text,locations,remote,job_type,job_domain,
        source_url,apply_url,posted_at,first_seen_at,last_seen_at,closed_at,
        ats_closed_at,linked_dead_at,paged_closed_at,duplicate_group_id
    )
    SELECT j.id,j.source,j.source_key,j.company_id,c.name,c.domain,
           j.title,analysis.role_family(j.title),j.description,j.location_text,j.locations,
           j.remote,j.job_type,j.job_domain,j.source_url,j.apply_url,j.posted_at,
           j.first_seen_at,j.last_seen_at,LEAST(j.ats_closed_at,j.linked_dead_at,j.paged_closed_at),
           j.ats_closed_at,j.linked_dead_at,j.paged_closed_at,j.dup_group_id
    FROM public.job_posting j JOIN public.company c ON c.id=j.company_id
    WHERE j.status='closed'
      AND LEAST(j.ats_closed_at,j.linked_dead_at,j.paged_closed_at) IS NOT NULL
      AND LEAST(j.ats_closed_at,j.linked_dead_at,j.paged_closed_at) <= now()
    ON CONFLICT(source,source_key) DO UPDATE SET
        closed_at=LEAST(analysis.closed_job.closed_at,EXCLUDED.closed_at),
        ats_closed_at=LEAST(analysis.closed_job.ats_closed_at,EXCLUDED.ats_closed_at),
        linked_dead_at=LEAST(analysis.closed_job.linked_dead_at,EXCLUDED.linked_dead_at),
        paged_closed_at=LEAST(analysis.closed_job.paged_closed_at,EXCLUDED.paged_closed_at),
        refreshed_at=now();
    GET DIAGNOSTICS affected=ROW_COUNT;
    RETURN affected;
END
$$;

CREATE OR REPLACE VIEW analysis.closed_job_hourly AS
    SELECT date_trunc('hour',closed_at AT TIME ZONE 'UTC') AS hour_utc,
           company_id,company_name,role_family,count(*) AS closures
    FROM analysis.closed_job GROUP BY 1,2,3,4;

CREATE OR REPLACE VIEW analysis.closed_job_daily AS
    SELECT (closed_at AT TIME ZONE 'America/Los_Angeles')::date AS day,
           company_id,company_name,role_family,count(*) AS closures
    FROM analysis.closed_job GROUP BY 1,2,3,4;

SELECT analysis.refresh_closed_jobs();
ANALYZE analysis.closed_job;
COMMIT;

-- Research examples (timestamps include an explicit Pacific offset):
-- SELECT * FROM analysis.closed_job
-- WHERE closed_at >= '2026-10-05 00:00:00-07'
--   AND closed_at < '2026-10-08 00:00:00-07'
--   AND role_family='Software engineering'
-- ORDER BY closed_at DESC;
--
-- SELECT company_name,role_family,count(*) AS closures
-- FROM analysis.closed_job
-- WHERE to_tsvector('simple',company_name || ' ' || title || ' ' || coalesce(location_text,''))
--       @@ websearch_to_tsquery('simple','software engineer')
-- GROUP BY 1,2 ORDER BY 3 DESC;
