# Search Filter — Aug 2026 – Mar 2027 Student Job Search

Filter strategy for the `posting` table to surface all opportunities the user can realistically apply to as a student during the Aug 2026 – Mar 2027 window.

## Context

`docs/strategy.md` defines four work windows that fall inside Aug 2026 – Mar 2027:

| # | Window | Dates | Availability |
|---|---|---|---|
| A | 2026 Summer | Jul 30 – Sep 23, 2026 | full-time |
| B | 2026 Fall | Sep 24 – Dec 11, 2026 | 10–20 h/wk |
| C | 2026 Winter break | Dec 12 – Jan 3 | full-time |
| D | 2027 Winter co-op | Jan 4 – Mar 12, 2027 | full-time or 20 h/wk |

Simultaneously, the "Application cycles" table shows that **Summer 2027 internship applications open and peak inside this same Aug 2026 – Mar 2027 span**:
- Quant Summer 2027: early Aug 2026 (a few late Jul) → closes hard by Nov 2026
- Big tech / large AI labs Summer 2027: Sep – Oct 2026 → effectively closed Feb 2027
- Startups / mid-size Summer 2027: Nov 2026 – Mar 2027, rolling

So "jobs I am searching for Aug 2026 – Mar 2027" means: **everything the user would realistically apply to during that span, regardless of when the work itself starts**. This includes startup part-time, full-time co-op, part-time co-op, contract work, externships, national-lab terms, and Summer 2027 internships. It **explicitly excludes** the Aug–Nov 2027 new-grad FT cycle and anything Path-3-specific (Sep 2027 onward) — those postings won't exist yet during this window.

## Filter Design

### 1. Structural filters (always applied)

**Open status:**
- `status = 'open'` — drop `closed` and `unlisted` postings.
- `deadline_at IS NULL OR deadline_at > now()` — drop postings with a stated closure date in the past.

No explicit date-range filter is applied for "Aug 2026 – Mar 2027" itself. The temporal filter is implicit: "currently open" at query time is the definition. Postings for cycles that open later will not yet exist in the table.

### 2. Employment-type / student-eligibility classification

`employment_type` is raw and source-dependent (12/14 ATS adapters populate it, format varies). Classification combines `title_normalized` and `employment_type` text matching using two regex patterns applied case-insensitively:

#### Include pattern (student-signal keywords)
```
intern|internship|co.?op|part.?time|contract(or)?|temporary|fellow(ship)?|apprentice|working student|extern(ship)?|summer analyst|student
```

Matches postings that explicitly signal student eligibility across the cycle taxonomy:
- `intern(ship)?` — covers "Intern", "Internship", "Software Engineering Internship"
- `co.?op` — covers "co-op", "coop", "Co-Op", "Cooperative Education"
- `part.?time` — covers "Part-Time", "part time", and hyphenated variants
- `contract(or)?` — covers "Contract", "Contractor", "Contracting"
- `temporary` — covers temp roles and seasonal positions
- `fellow(ship)?` — covers "Fellow", "Fellowship"
- `apprentice` — covers apprenticeships
- `working student` — covers work-study and student-employment tiers
- `extern(ship)?` — covers externships (windows C/E fallback)
- `summer analyst` — hedge-fund / quant tier phrasing (distinct from general "analyst")
- `student` — catch-all for explicit student status

#### Exclude pattern (seniority-signal keywords)
```
senior|sr\.|staff|principal|lead |director|\bvp\b|head of|manager
```

Applied as a negation: a row matching the include pattern but also matching this pattern is dropped. Prevents false positives like:
- "Contract Senior SRE" (matches `contract` but also `senior`)
- "Staff Software Engineer" (matches neither include nor exclude directly, fails include pattern)
- "Director of Engineering Internship" (unlikely, but would match include; rejected by exclude)

#### Application

A posting passes if:
```
(title_normalized ~* include_pattern OR employment_type ~* include_pattern)
  AND title_normalized !~* exclude_pattern
```

**Why both fields?** `employment_type` often carries the student signal where `title_normalized` does not (e.g. a generic "Software Engineer" title with `employment_type = "Intern"`).

**New-grad exclusion (automatic):** Postings titled "Software Engineer I" or "SDE, 2026 University Grad" without an intern/co-op/part-time/contract qualifier fail the include pattern by design — that's the Aug–Nov 2027 new-grad cycle, outside this window.

### 3. Company tiering (priority tag, not a hard filter)

Join `posting → company` and match `company.name` (or via `company_alias`) against the 12 tier lists in `docs/summer-companies.md`:

| Tier | Code | Count | Segment priority |
|---|---|---|---|
| Quant / Proprietary Trading | Q | 48 | 1 (anchor) |
| Frontier AI Labs | FL | 47 | 1 (anchor) |
| Big Tech SWE Intern | BT | 69 | 1–2 |
| Inference Startups + Neoclouds | INF | 32 | 2 |
| AI Accelerator Startups | ACC | 25 | 2 |
| GPU / Accelerator Software | GPU | 23 | 2 |
| ML Infrastructure / Platform | MLI | 38 | 2 |
| Backend / Systems SWE | BE | 78 | 2–3 |
| Hedge Fund / Multi-Strat | HF | 31 | 1–2 |
| National Labs + FFRDC | LAB | 35 | 1–3 (citizenship-gated) |
| Defense Tech Primes + Startups | DEF | 54 | 1–3 (citizenship-gated) |
| Crypto Market Makers | CX | 25 | 3 (net-widener) |

Attach the tier code (or `NULL`/"general" for unlisted companies) as a tag for sorting and filtering. **Do not apply a tier as a hard filter.** Companies outside this list are not rejected — that is exactly the intended behavior for windows A/B (cold-email channels, small startups with no formal postings) and matches `docs/strategy.md`'s own note that "the only channel that can produce paid work before October" requires a wider net than the curated 505.

### 4. Window/term tagging (secondary, best-effort)

Optionally scan `description_html`, `content_sections`, and `source_extra` (JSON long-tail) for season+year tokens ("Summer 2026", "Fall 2026", "Winter 2027", "Jan 2027") and attach as a tag for secondary sorting or filtering. Because most part-time/contract postings do not mention a term explicitly, **absence of a term is not evidence of irrelevance** — term tagging is for **display and sorting only**, not a hard filter.

**Note on pointer rows:** `description_html IS NULL` marks a Simplify or Gmail pointer row — the actual body lives elsewhere, reachable via `posting_link` and `group_id`. Term-tagging on a pointer row requires resolving to a sibling row (`posting_link` traversal) to find the actual text. Title-based classification (steps 1–2 above) does not require this resolution, since `title_normalized` is always populated on every row.

## SQL Query Template

# Haiku
```sql
-- Core filter (hard: status, deadline, title + employment_type keywords)
SELECT
    p.id,
    p.company_id,
    p.title,
    p.title_normalized,
    p.employment_type,
    p.url_posting,
    p.published_at,
    p.first_seen_at,
    p.last_seen_at,
    c.name AS company_name,
    c.domain,
    COALESCE(t.tier, 'general') AS tier
FROM posting p
JOIN company c ON c.id = p.company_id
LEFT JOIN company_tier_lookup t ON t.company_id = c.id
WHERE p.status = 'open'
  AND (p.deadline_at IS NULL OR p.deadline_at > now())
  AND (
    p.title_normalized ~* '(intern(ship)?|co.?op|part.?time|contract(or)?|temporary|fellow(ship)?|apprentice|working student|extern(ship)?|summer analyst|student)'
    OR (p.employment_type IS NOT NULL AND p.employment_type ~* '(intern|co.?op|part.?time|contract|temporary|fellow)')
  )
  AND p.title_normalized !~* '(senior|sr\.|staff|principal|lead |director|\bvp\b|head of|manager)'
ORDER BY
  -- Segment priority (Q/FL first, then BT/HF/LAB/DEF, then INF/ACC/GPU/MLI/BE, then CX, then general)
  CASE t.tier
    WHEN 'Q'  THEN 1
    WHEN 'FL' THEN 1
    WHEN 'BT' THEN 2
    WHEN 'HF' THEN 2
    WHEN 'LAB' THEN 2
    WHEN 'DEF' THEN 2
    WHEN 'INF' THEN 3
    WHEN 'ACC' THEN 3
    WHEN 'GPU' THEN 3
    WHEN 'MLI' THEN 3
    WHEN 'BE'  THEN 3
    WHEN 'CX'  THEN 4
    ELSE 5 -- general (unranked)
  END ASC,
  p.first_seen_at DESC;  -- within tier, newest first
```

# Gemini
```sql
SELECT
    p.id,
    p.company_id,
    p.title,
    p.title_normalized,
    p.employment_type,
    p.url_posting,
    p.published_at,
    p.first_seen_at,
    c.name AS company_name,
    COALESCE(t.tier, 'general') AS tier
FROM posting p
JOIN company c ON c.id = p.company_id
LEFT JOIN company_tier_lookup t ON t.company_id = c.id
WHERE p.status = 'open'
  AND (p.deadline_at IS NULL OR p.deadline_at > now())
  
  -- 1. THE SURGICAL BLACKLIST: Drop jobs you strictly CANNOT get
  AND p.title_normalized !~* '\b(senior|sr\.?|staff|principal|lead|director|vp|head of|manager|architect|chief)\b'
  AND p.title_normalized !~* '\b(ii|iii|iv|v|[2-9])\b' 
  AND p.title_normalized !~* '\b(ts/?sci|polygraph)\b' 

  AND (
    -- 2A. EXPLICIT STUDENT ROLES: Pass these through for ALL companies
    (
      p.title_normalized ~* '\b(intern(?:ship)?|co-?op|part-?time|temporary|fellow(?:ship)?|apprentice|working student|extern(?:ship)?|summer analyst|student|junior|entry-?level)\b'
      OR (p.employment_type IS NOT NULL AND p.employment_type ~* '\b(intern|co-?op|part-?time|temporary|fellow)\b')
      -- Handle 'contract' safely by ignoring 'smart contract'
      OR (p.title_normalized ~* '\bcontract(?:or)?\b' AND p.title_normalized !~* 'smart contract')
    )
    OR
    -- 2B. FLEXIBLE STARTUPS: Pass standard, unlabeled SWE roles through
    -- (Allows you to find posts to pitch a part-time/contract arrangement)
    (t.tier IN ('INF', 'ACC', 'GPU', 'MLI', 'CX', 'general'))
  )
ORDER BY
  -- High-conviction ML/Systems priorities pushed to the top
  CASE t.tier
    WHEN 'Q'   THEN 1
    WHEN 'FL'  THEN 1
    WHEN 'INF' THEN 1  -- Elevated for startup contract potential
    WHEN 'ACC' THEN 1
    WHEN 'GPU' THEN 1
    WHEN 'MLI' THEN 1
    WHEN 'BT'  THEN 2
    WHEN 'HF'  THEN 2
    WHEN 'BE'  THEN 2
    WHEN 'LAB' THEN 3
    WHEN 'DEF' THEN 3
    WHEN 'CX'  THEN 4
    ELSE 5
  END ASC,
  p.first_seen_at DESC;
```

`company_tier_lookup` is a small reference table seeded from `docs/summer-companies.md`:

```sql
-- Pseudo-code: build this once from summer-companies.md tier tables
-- For production, this can be seeded in an initialization script or generated from config
CREATE TABLE company_tier_lookup AS (
  SELECT c.id, 'Q'   AS tier FROM company c WHERE c.name IN ('3Red Partners', 'Akuna Capital', ... )  UNION ALL
  SELECT c.id, 'BT'  AS tier FROM company c WHERE c.name IN ('Adobe', 'Airbnb', ... )  UNION ALL
  SELECT c.id, 'FL'  AS tier FROM company c WHERE c.name IN ('1X', 'AI21 Labs', ... )  UNION ALL
  -- ... (remaining tiers)
);
```

## Verification

### Manual checks against `docs/summer-roles.md` titles

**Should pass** (include keywords, no seniority):
- "Quantitative Developer Intern" → matches `intern`
- "Software Engineer Intern, Backend" → matches `intern`
- "SDE Intern" → matches `intern`
- "Internship, Software Engineering" → matches `internship`
- "ML Platform Engineer Intern" → matches `intern`
- "Co-Op Engineer" → matches `co.?op`
- "Part-Time Contract Software Engineer" → matches `part.?time` or `contract`
- "Software Engineering Summer Analyst" → matches `summer analyst`
- "Research Infrastructure Intern" → matches `intern`

**Should fail** (no include keywords, or seniority signal):
- "Software Engineer, Core/Platform" → no intern/co-op/part-time; fails include pattern
- "Software Development Engineer" → no student signal; fails include pattern
- "Senior Software Engineer Intern" → matches include (`intern`) but also matches exclude (`senior`); rejected
- "Staff Software Engineer" → no student signal; fails include pattern
- "Software Engineer I" → new-grad tier notation; fails include pattern
- "Manager, Engineering" → no student signal; fails include pattern

### Live data validation (when posting data is available)

Run the query above against the `posting` table and spot-check a sample of results:
1. **False positives** — do any senior/staff roles slip through?
2. **False negatives** — are there common student role phrasings not captured by the keyword set?

If either is found, update the include/exclude patterns and re-run.

## Related docs

- `docs/strategy.md` — work windows, application cycles, career plan
- `docs/summer-companies.md` — 505-company tier lists and domain reachability checks
- `docs/summer-roles.md` — role title taxonomy per tier
- `schema.sql` — posting table schema and column definitions
