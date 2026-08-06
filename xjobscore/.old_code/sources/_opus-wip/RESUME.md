# Resume notes — source-list-opus.md build

Halted 2026-08-01 mid-sweep. Everything needed to continue is in this directory.

## State

**Done (7 of 14 domain sections, ~518 KB):**

| File | Section |
|---|---|
| `00-header.md` | Front matter — schema legend, tier legend, recruiting calendar, build order (hand-written, not agent output) |
| `01-general-aggregators.md` | General aggregators, meta-search & their APIs |
| `02-ats-platforms.md` | ATS platforms — the direct-from-employer firehose |
| `03-student-intern-platforms.md` | Student/intern platforms + curated GitHub lists |
| `04-startup-vc-ecosystem.md` | Startup & VC ecosystem |
| `05-bigtech-structured-programs.md` | Big Tech & named structured / early-identity programs |
| `07-nontech-fortune-a.md` | Non-tech Fortune 1000 — part A (retail, CPG, healthcare, pharma, hospitality, ag) |
| `09-government-labs.md` | Government, national labs, public sector, cleared defense research |

**Remaining (7 domain sections + critic + 3 gap-fill):**

- `06` Quant finance, trading, banking, insurance-tech — *the highest-value lane for Applied Math; do this one first*
- `08` Non-tech Fortune 1000 part B (aero/defense, industrial, auto, energy, telecom, media, logistics, consulting, real estate)
- `10` Research programs, REUs, math-specific programs, AI labs & fellowships
- `11` Affinity & diversity pipelines, communities, Discord/Slack/Reddit, newsletters
- `12` Part-time, freelance, contract, gig, AI-data work & remote-first boards
- `13` International markets & university co-op / career-services systems
- `14` Alternative discovery signals, cold-outreach infrastructure & meta-sources
- Completeness critic (reads all 14, names blind spots adversarially)
- Gap fill ×3 → sections `15` (industry verticals), `16` (search operators / schema.org / sitemaps / Common Crawl), `17` (remainder + verification queue)

## How to continue

`workflow.js` in this directory is the full workflow, already patched for resume:

- `DIR` points here (`xjobscore/sources/_opus-wip`) instead of the session scratchpad
- `DONE` is a hand-maintained set of completed section slugs; the sweep skips them

```
Workflow({ scriptPath: "/Users/mr.po/Documents/GitHub/xjobscore/xjobscore/sources/_opus-wip/workflow.js" })
```

**Update `DONE` from `ls` before each run** — workflow scripts have no filesystem access, so completion
cannot be auto-detected.

Do **not** use `resumeFromRunId` — that cache is same-session only and the original run (`wf_f1bbd35e-198`)
is gone. Re-running is the correct path; the skipped sections cost nothing.

## Final assembly

Once `06`, `08`, `10`–`14`, and `15`–`17` exist, concatenate in numeric order (`00-header.md` first) to
`xjobscore/sources/source-list-opus.md`, then delete this directory.

Projected size is 1–1.5 MB, which is past comfortable single-file reading length. Worth deciding at assembly
time whether to ship the monolith as requested, or split per-section with a slim index — the per-section files
already exist, so the split is nearly free.

## Caveats on what is already written

- Section content is agent-generated and **not independently verified**. Researchers were told to mark
  uncertain URLs `low` confidence and to record dead/renamed sources as `T0` rather than dropping them, but
  spot-checking before relying on any Tier-1 endpoint is still warranted.
- The completeness critic has **not run yet**, so the 7 finished sections have had no adversarial review.
  Expect it to find gaps in them, and expect section `17` Part B to supplement them.
