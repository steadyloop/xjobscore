# xjobscore — Exhaustive Source Catalog

**Goal:** capture *every* online internship / co-op / part-time / startup role reachable by a CS + Applied Math
undergraduate — including technical roles at non-tech companies.

**Status:** planning-stage inventory. This is deliberately over-inclusive. Nothing here is a commitment to build;
it is the full option space so that scoping decisions are *subtractive* and reversible.

**Generated:** 2026-08-01

---

## How to read this document

Every source is one row in a table with a fixed schema, so the whole document can be parsed into a source registry.

| Column | Meaning |
|---|---|
| **Source** | Canonical name. Use as the human label. |
| **URL** | Entry point. For API sources this is the base endpoint or docs page. |
| **Access** | How you get data out. See legend below. |
| **Auth** | What credentials are required. |
| **Coverage** | What it actually yields: role types, geography, rough volume, freshness. |
| **Tier** | Build priority. See legend below. |
| **Notes** | The ingestion hint — endpoint patterns, pagination, rate limits, anti-bot, seasonality. |

### Access legend

| Value | Meaning | Implementation cost |
|---|---|---|
| `API` | Documented public API | Low — write a client |
| `RSS` | RSS/Atom feed | Lowest — feed parser |
| `JSON` | Undocumented but stable JSON endpoint (most ATS boards) | Low — highest value-per-effort in this doc |
| `HTML` | Server-rendered HTML, scrapeable with requests + parser | Medium — brittle to redesigns |
| `HEADLESS` | Requires JS execution / browser automation | High — Playwright, proxies, maintenance |
| `MANUAL` | No programmatic path; human check on a cadence | High recurring — reserve for high-yield only |
| `EMAIL` | Newsletter or job-alert email → parse inbox | Medium — often the only path past anti-bot |
| `SOCIAL` | Discord / Slack / X / Reddit feed | Medium — ToS-sensitive |

### Auth legend

`none` · `key` (free API key) · `paid` · `account` (login) · `edu` (school SSO) · `invite` (gated community)

### Tier legend

| Tier | Meaning |
|---|---|
| **T1** | Must-have. High yield, low effort, or uniquely irreplaceable coverage. Build first. |
| **T2** | Worth building. Real yield but more effort, narrower coverage, or redundant with a T1. |
| **T3** | Long tail / opportunistic. Build when the core works, or check manually. |
| **T0** | Dead, renamed, or a trap. Documented so it is not re-investigated later. |

---

## The core strategic insight

Aggregators (LinkedIn, Indeed) are the *worst* source to build on: heaviest anti-bot, most duplication, most
stale postings, most legal exposure. They feel like the obvious starting point and they are not.

The highest-leverage layer is **ATS endpoints** (Section 2). A few thousand employers each expose a clean,
unauthenticated, rate-limit-friendly JSON endpoint listing every open role — the authoritative copy, posted
before aggregators pick it up, with no duplication. One well-built Greenhouse/Lever/Ashby/Workday client
covers a large fraction of every company worth applying to. **Company → ATS-slug resolution is the real
engineering problem**, not fetching.

Around that core, three source families exist that ATS scraping structurally *cannot* reach, and each needs
its own path:

1. **Gated portals** — Handshake, WaterlooWorks, school Symplicity instances. Employer-direct postings that
   appear nowhere else. `edu` auth, often manual.
2. **Named programs** — Google STEP, Jane Street INSIGHT, NSA Stokes, NSF REU, Coding it Forward. Own portals,
   own calendars, frequently 6–12 months ahead of the general cycle. Calendar problem, not a scraping problem.
3. **Hidden market** — VC portfolio boards, HN "Who Is Hiring", Discord servers, cold outreach. Where startup
   roles actually live.

A system that only scrapes boards misses all three.

---

## Recruiting calendar

Timing dominates. Most missed opportunities are deadline misses, not discovery misses. The single most common
failure mode for a US CS student is discovering the summer cycle in January — five months after it opened.

| Window | What opens | Notes |
|---|---|---|
| **May–July** (prev. year) | Quant/HFT for the *following* summer (Jane Street, Citadel, HRT, Jump, Optiver, SIG, DRW) | ~13 months ahead. Rolling, fills fast. The earliest real deadline in the calendar. |
| **July–September** | Big Tech summer internships; most Fortune 500 programs; GitHub trackers rebuild for the new season | The main opening surge. Highest-density window of the year. |
| **August–October** | Early-identity / sophomore programs (Google STEP, Meta University, Microsoft Explore, Uber STAR); banks; consulting | Frequently hard deadlines, not rolling. |
| **September–November** | Government & national labs (NSA, NASA OSTEM, DOE SULI, Federal Reserve); FBI Honors; MITRE/APL | Long security-clearance lead times force early closes. |
| **October–January** | NSF REU and academic research programs; math-specific summer programs; fellowships | Most REU deadlines cluster in **February**; applications open in the fall. |
| **January–March** | Second-wave / backfill postings; startups; smaller companies; late Fortune 500 reqs | Startups hire on a much shorter horizon — 4–8 weeks out, not 12 months. |
| **March–May** | Late startup postings; part-time and campus roles for fall; summer research backfill | Also where cancelled-offer backfill appears. |
| **Rolling / year-round** | Freelance, AI-data work, tutoring, part-time, open-source stipends | No calendar. Capacity-limited, not deadline-limited. |

**Implication for the crawler:** poll frequency should be seasonal, per family. Polling quant boards in
February and REU sites in June is wasted work; missing the August–September Big Tech surge is expensive.

---

## Suggested build order

Reordered by yield-per-unit-effort, not by section number.

**Phase 1 — the core (highest yield per unit of work)**
1. Greenhouse + Lever + Ashby JSON clients. Three simple, stable, unauthenticated APIs. Largest single
   coverage jump available.
2. Company → ATS-slug resolution. Seed from the GitHub internship trackers (Section 3), which already contain
   thousands of resolved links. This is the piece that makes everything else compound.
3. Normalization + dedupe: canonical company ID, role classification (intern / co-op / part-time / new-grad),
   term inference, location, posting hash.

**Phase 2 — breadth**
4. Workday CXS (the largest non-tech employer surface — most of Sections 7 and 8 sit behind it).
   Harder than phase 1: POST-based, tenant discovery required. Unlocks disproportionate volume.
5. SmartRecruiters, Workable, Recruitee, Breezy, Personio, Teamtailor — same pattern as phase 1, thinner tail.
6. GitHub tracker ingestion as a *source* (commit polling), not just a seed list.
7. The free-API tier: Adzuna, USAJOBS, RemoteOK, Remotive, Himalayas, Arbeitnow, Findwork.

**Phase 3 — the parts scraping can't reach**
8. A **calendar/deadline registry** for named programs. Not a crawler — a curated date table with alerts.
   For programs with hard deadlines this is worth more than any scraper.
9. Email-alert ingestion (dedicated address + IMAP + per-sender parsers). The practical answer to LinkedIn,
   Indeed, Glassdoor and every other anti-bot site — and it is *sanctioned*, since they are sending you the mail.
10. VC portfolio boards. Many share infrastructure (Getro, Consider) — solve the platform, get hundreds of
    portfolios for free.
11. HN "Who Is Hiring" via the Algolia API. One endpoint, monthly, high startup signal.

**Phase 4 — long tail and human-in-the-loop**
12. Search-operator harvesting + schema.org JobPosting extraction (Section 16) for everything unmodeled.
13. Discord/social relays where ToS permits.
14. A manual-check queue for `MANUAL` sources, ranked by expected yield, on a seasonal cadence.

**Explicitly deprioritize:** LinkedIn and Indeed scraping. Worst effort-to-yield ratio in the document, plus
real ToS and legal exposure. Reach them through email alerts instead.

---

## Known constraints to carry through the build

- **Duplication is the central data problem.** The same role appears on the ATS, LinkedIn, Indeed, three
  aggregators and two GitHub trackers. Dedupe on (canonical company, normalized title, location, term) —
  not on URL.
- **Ghost postings are real.** A meaningful share of aggregator listings are stale, already filled, or never
  existed. ATS-direct is the strongest freshness signal available; treat aggregator-only postings as suspect.
- **Eligibility is a hard filter, not a preference.** Citizenship/clearance (most of Section 9), F-1 CPT/OPT
  status, class-year restrictions, and graduation-date windows. Model these as first-class fields or the
  system will surface large volumes of unapplicable roles.
- **ToS and rate limits are load-bearing.** ATS JSON endpoints are effectively public and tolerant. Aggregators
  are not. Respect robots.txt, keep concurrency low, identify the crawler, and prefer sanctioned paths (APIs,
  email alerts) wherever one exists.
- **Seasonality changes what "fresh" means.** A source that is dead in June is the primary source in September.
  Poll cadence should be per-source and seasonal.

---
