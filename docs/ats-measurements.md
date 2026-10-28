# ATS measurements

2026-10-06. Measured request-start rates; completed runs have zero 429s.

workable
---
- Verification: widget accounts endpoint, no `details=true`; mixed live/dead accounts, 1 request in flight, 0 retries.
- 0.247 req/s: 150 requests / 604 s; 0 429s.
- 0.487 req/s: 300 requests / 614 s; 0 429s.
- 0.999 req/s: 429 on request 102 / 101 s; after 450 earlier requests on the same exit.
- 0.749 req/s: 450 requests / 599 s; 0 429s.
- 0.874 req/s: 429 on request 45 / 50 s; after 450 earlier requests on the same exit.
- 0.400 req/s: 600 requests / 1,498 s; 0 429s.
- 0.450 req/s: 429 on request 348 / 772 s; after 600 earlier requests on the same exit.
- 0.425 req/s: 638 requests / 1,500 s; 0 429s. Peak 510 requests / 20 min.
- Excluded: 0.500 req/s (429 #132), 0.4373 req/s (429 #308); competing validation sweeps.
- Best 25-minute passing point: 0.4248 req/s = 25.49/min. Permanent maximum and polling ceiling unproven.
- Saved 429 headers: `Retry-After: 0` or `86400`; client cooldown cap 30 s; sweep cooldowns 60/300/900 s.
- Polling and validation use separate pacers; validation target is 1 req/s.
- Earlier polling-only notes (`details=true`): 5,546 polls / 2 h, peak 61/min, 0 429s; separate from these experiments.

Raw results: `oneshot-ui/experiments/workable-rate/summary.json` and `requests.jsonl`.
