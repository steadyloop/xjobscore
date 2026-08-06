# xjobscore

**Goal**. Cross board aggregation of job listings with dedup and labeling.

## Inpspector

A local, read-only web UI for fetching the adapters individually.

```bash
uv run python -m xjobscore.inspector
```


---

## Advantages
1. **Speed**: Early apply before it hits mass aggregators (5 minute poll).
2. **Accuracy**: Check the source (avoid dead listings), and accurate labeling.
3. **Filtering**: Personalize the filter.

## Workflow
1. FETCH: Periodically fetch \<company\>'s job list URL for their current jobs.
2. COMPARE: Which job id is new since last time (and what disappeared).
3. FILTER: User filter; CS/Math role? Junior enough? (Keyword first, then LLM).
4. PRIORITIZE: Bump good ones up the list.
5. REPEAT

Step 3 is the clever bit and it's not complicated: we just keep a list of job IDs we saw last
time and compare. New IDs are new jobs. Missing IDs are closed jobs. No guessing.

## The pieces

| Piece | Stack | What it does |
|---|---|---|
| `main.py` | Python 3.12, `asyncio` | The engine — runs the loop above, forever, in one process |
| `ats/greenhouse.py`, `ats/lever.py`, … | `httpx` + `re`, one plain module each | Translators. Each hiring system returns a different shape; these convert to one common shape |
| `db.py` | `asyncpg` → PostgreSQL 16+ | The memory. **Every SQL query in the project lives in this one file** |
| `classify.py` | `re` first, then Claude Haiku 4.5 | The filter. Keywords are free and catch most cases; AI handles only the unclear ones |
| `notify.py` | `ntfy` HTTP push | The messenger |
| `companies.yaml` | plain YAML, in git | Your target list — a text file you edit by hand |
| `schema/001_init.sql` | plain SQL, numbered | Table definitions. To change them, add a new numbered file; never edit an applied one |
| `tests/` | `pytest` + saved JSON fixtures | Proves each translator still works, without needing the internet |

## Full stack

| Concern | Choice | Why, in plain terms |
|---|---|---|
| Language | Python 3.12 | One language for polling, parsing, and AI work |
| Packaging | `uv` | Faster and simpler than pip + venv; one lockfile |
| Fetching | `httpx` (async) | Waits on 50 requests at once instead of one at a time |
| Concurrency | `asyncio` | Built into Python. No threads, no extra service |
| Database | **PostgreSQL 16+** | Holds the "what did I see last time" memory |
| DB driver | `asyncpg` | Talks to Postgres without blocking the loop |
| Schema changes | numbered `.sql` files | Simplest possible version control for tables |
| Raw data | `gzip` files on disk | If a translator has a bug, re-read the original response instead of losing the day |
| Classification | Claude Haiku 4.5 | Cheap and fast; pulls years-of-experience, visa sponsorship, etc. out of descriptions |
| Notifications | `ntfy` | Free, instant push to your phone. One HTTP POST — no bot account, no inbound port. Top-tier jobs go out at high priority |
| Config | env vars in `.env` | No secrets in git; also what lets the project run anywhere unchanged |
| Tests | `pytest` | One saved response per hiring system; catches breakage before you rely on it |
| Staying alive | `systemd` (Linux) / `launchd` (macOS) | Restarts the program automatically if it crashes |
| Auto-apply, later | Playwright + Typst | Drives a real browser to fill forms; builds tailored PDF resumes |

**Postgres is the only piece that isn't just a Python library.** It runs on the same machine
(`brew install postgresql@17`, or `apt install postgresql`), in a container, or free at Neon or
Supabase. The code never knows the difference — it reads one `DATABASE_URL` from `.env`. Start
on a laptop, move to an always-on box later by changing that one line.

Postgres was chosen over the simpler SQLite deliberately: switching SQLite → Postgres later is
the one change in this project that's genuinely painful (a day of subtle bugs), and it would
most likely become necessary in September — exactly when you need the system running, not
being rebuilt. Starting with Postgres costs ~30 extra minutes. Full reasoning in
`docs/PLAN.md`.

## Where it runs

No hardware assumed — anything that runs Python and stays awake. The tradeoff is reliability:

| Host | Good | Bad |
|---|---|---|
| Your laptop | Free, already have it | Sleep breaks the speed guarantee; closing the lid stops everything |
| Always-on machine at home | Free, never sleeps | Must stay plugged in and online |
| Cheap VPS (~$5/mo) | Never sleeps, not your problem | Costs money |

Start on a laptop for Phase 0 — proving the idea doesn't need reliability. Move to something
always-on before September, since a system asleep at 9am has no speed advantage at all.

## What you'll actually do day to day

**The system does:** checks boards, filters, notifies. Sends you one summary message a day
("checked 412 boards, 23 new jobs, 0 errors") so you know it's alive.

**You do:**
- Read notifications and open the ones worth applying to
- Occasionally add companies to `companies.yaml`
- Later on, approve newly discovered companies (~30 seconds/day)

Notifications are one-way: `ntfy` pushes out, and nothing comes back in. Triage that used to
happen by tapping a button in the notification — recording **Apply** / **Snooze** / **Not
relevant**, approving a discovered company — has no input channel yet. See `docs/PLAN.md`.

If notifications stop, check in order: is the machine awake and online, is the program running
(`systemctl status xjobscore`, or `launchctl list | grep xjobscore` on macOS), did the daily
summary arrive.

That daily summary is the most important safety feature here. Without it, a broken program
looks exactly like a quiet job market.

## Build order

Each step ends with something that *works*. Don't move on until it does.

| Phase | What you build | Done when |
|---|---|---|
| **0** | Greenhouse only, 20 companies, no filtering | A real job hits your phone in under 2 minutes |
| **1** | Add Lever, measure speed | You can state your median lead time as a number |
| **2** | Filtering + scoring + notification tiers | Notifications are mostly relevant |
| **3** | Grow to ~400 companies | The company list is full |
| **4** | Auto-discover new companies | New candidates show up for approval |
| **5** | Workday (the hard one) | Big companies covered |
| **6** | Remaining smaller systems | Long tail covered |
| **7–9** | Auto-apply | Applications take ~15 seconds each |

**Phase 0 is the important one.** It proves the entire idea with the least work. If a job
can't reach your phone in 2 minutes with 20 companies, nothing later fixes that.

There's one test you shouldn't skip, at the end of Phase 2: take 30 real postings, note when
*you* found them, then watch for when they appear on LinkedIn. If your lead time is hours,
the project works. If it's 20 minutes, the speed advantage is smaller than hoped and the real
value is the filtering — still useful, but worth knowing early.

## Auto-apply, later

The plan is to automate application *preparation*, not submission:

1. Write down every answer you'd ever give on a form once, in a file.
2. While you apply manually, a recorder learns where each field lives on each form.
3. After ~20 applications it can fill out a new one completely, take a screenshot, and stop
   before submitting.
4. You review the filled-in form and approve it.

That's ~15 seconds per application instead of 20 minutes, and because a human still approves
before submit, you're not violating any site's terms or risking your accounts. Fully unattended
submission is deliberately left undecided until there's data on how many forms are simple
enough to trust.

## Vocabulary

- **ATS** (Applicant Tracking System) — the software companies use to post jobs and manage
  applicants. Greenhouse, Lever, Ashby, Workday. Our data source.
- **Polling** — repeatedly asking "anything new?" on a timer. The opposite would be the
  company notifying us, which they won't.
- **Endpoint** — a web address that returns data instead of a web page.
- **ETag** — a short code a server sends with data. Send it back next time and the server
  replies "nothing changed" instead of resending everything. Saves bandwidth and keeps us
  polite.
- **Slug** — a company's short name in a URL. In `boards.greenhouse.io/stripe`, the slug is
  `stripe`.
- **Adapter** — one of our per-ATS translator files.
- **Idempotent** — safe to run twice. If the program crashes mid-job, re-running causes no
  duplicates or lost data. Ours is idempotent because we compare against the whole board every
  time.
- **Rate limit** — how often a server tolerates being asked before it blocks you.
- **Fixture** — a saved real response, used in tests so they don't need the internet.
- **PostgreSQL** — the database. Runs as a background service; code connects to it with a URL.
- **`DATABASE_URL`** — the one string saying where the database is, e.g.
  `postgresql://user:pass@localhost:5432/xjobscore`. Changing it moves the whole system.
- **Migration** — a schema change. Ours are numbered `.sql` files applied in order.
- **`timestamptz`** — a Postgres date type that remembers the timezone. We always store UTC,
  because this project measures time differences and a timezone mistake would silently make
  every measurement wrong.

## Setup (once Phase 0 exists)

```bash
# 1. Postgres — pick one
brew install postgresql@17 && brew services start postgresql@17   # macOS
sudo apt install postgresql                                        # Debian/Ubuntu
# ...or a free hosted database at neon.tech, and skip to step 2

createdb xjobscore

# 2. The app
uv sync                              # install dependencies
cp .env.example .env                 # add DATABASE_URL, ntfy topic, Anthropic API key
uv run python -m xjobscore           # start it (applies schema on first boot)
uv run pytest                        # run tests
```

To run unattended, a `systemd` unit (Linux) or `launchd` plist (macOS) restarts it if it dies.
On a laptop you also need to stop the machine sleeping — `caffeinate -s` on macOS,
`systemd-inhibit` on Linux. Setup files come with Phase 0.

Back up with `pg_dump` on a schedule. If the app and database share one machine, that machine
is a single point of failure for both.