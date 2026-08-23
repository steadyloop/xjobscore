# Remaining skills alias audit

This audit covers the 996 canonical skills that predated the latest 27
additions to `skills-revision.yaml`. It does not modify the dictionary.

## Method

- Corpus: all 132,276 local job descriptions.
- Counts are document frequencies, not repeated mentions.
- `+N` is the number of descriptions newly found for that canonical by the
  alias, compared with its current aliases.
- Every current alias was checked independently and through the intended
  leftmost-longest matching behavior.
- Candidate aliases came from display names, safe punctuation variants, and
  the old `skills.yaml`; broad terms were then checked against description
  samples and current alias ownership.
- `ambiguous_aliases` are documentation only and were not counted as matches.

## Result

| Measure | Result |
|---|---:|
| Remaining canonicals audited | 996 |
| Current zero-hit canonicals | 22 |
| Corpus-supported candidate aliases found before review | 54 |
| Existing aliases completely hidden by overlap | 0 |
| Recommended alias additions or promotions | 49 |

## Recommended aliases

All aliases below add at least one currently unmatched description and passed
the precision and ownership review. `Exact` means the alias belongs in
`case_sensitive_aliases`; all others belong in `aliases`.

| Canonical | Alias | Match mode | Hits | New |
|---|---|---|---:|---:|
| `apache-hive` | `Hive` | Exact | 161 | +161 |
| `stream-processing` | `real-time streaming` | Insensitive | 141 | +132 |
| `stream-processing` | `real-time data processing` | Insensitive | 101 | +84 |
| `celery` | `Celery` | Exact; move from ambiguous | 77 | +77 |
| `transformer-architecture` | `transformer models` | Insensitive | 74 | +70 |
| `power-query-m` | `Power Query` | Exact | 58 | +58 |
| `software-stress-testing` | `Stress tests` | Insensitive | 55 | +55 |
| `backtesting` | `back-testing` | Insensitive | 57 | +54 |
| `transformer-architecture` | `attention mechanisms` | Insensitive | 52 | +50 |
| `distributed-consensus` | `consensus algorithms` | Insensitive | 38 | +32 |
| `memory-management` | `memory leaks` | Insensitive | 30 | +24 |
| `lseg-workspace-refinitiv-workspace` | `Eikon` | Exact | 27 | +27 |
| `software-stress-testing` | `Stress test` | Insensitive | 30 | +30 |
| `software-stress-testing` | `Stress testing` | Insensitive | 263 | +263 |
| `triton-kernel-language` | `Triton kernels` | Insensitive | 15 | +15 |
| `apache-hive` | `Hive Metastore` | Insensitive | 15 | +15 |
| `kernel-bypass` | `kernel-bypass` | Insensitive | 14 | +14 |
| `cpu-cache-locality` | `cache locality` | Insensitive | 12 | +12 |
| `transformer-architecture` | `transformer model` | Insensitive | 12 | +11 |
| `triton-kernel-language` | `Triton kernel` | Insensitive | 11 | +11 |
| `poetry` | `Poetry` | Exact; move from ambiguous | 10 | +10 |
| `docker-compose` | `docker-compose` | Insensitive | 9 | +9 |
| `triton-kernel-language` | `OpenAI Triton` | Insensitive | 6 | +6 |
| `kv-cache-optimization` | `prefix caching` | Insensitive | 27 | +5 |
| `multithreading` | `multi threaded` | Insensitive | 5 | +5 |
| `memory-bandwidth` | `memory-bandwidth` | Insensitive | 6 | +4 |
| `transformer-architecture` | `multi-head attention` | Insensitive | 4 | +4 |
| `microbenchmarking` | `micro-benchmarking` | Insensitive | 4 | +4 |
| `object-storage` | `S3 API` | Insensitive | 6 | +3 |
| `memory-management` | `memory leak` | Insensitive | 4 | +3 |
| `design-patterns` | `Gang of Four` | Insensitive | 4 | +3 |
| `backpressure` | `back pressure` | Insensitive | 4 | +3 |
| `transformer-architecture` | `cross-attention` | Insensitive | 3 | +3 |
| `high-bandwidth-memory` | `HBM3` | Exact | 3 | +3 |
| `distributed-locking` | `distributed locks` | Insensitive | 3 | +3 |
| `market-microstructure` | `order book dynamics` | Insensitive | 11 | +2 |
| `object-storage` | `S3-compatible storage` | Insensitive | 4 | +2 |
| `memory-management` | `memory pools` | Insensitive | 3 | +2 |
| `transformer-architecture` | `decoder-only` | Insensitive | 2 | +2 |
| `backtesting` | `backtest framework` | Insensitive | 2 | +2 |
| `addresssanitizer` | `ASAN` | Exact | 1 | +1 |
| `ci-cd` | `CI CD` | Insensitive | 2 | +1 |
| `algorithmic-trading` | `algo execution` | Insensitive | 2 | +1 |
| `transformer-architecture` | `attention mechanism` | Insensitive | 1 | +1 |
| `threadsanitizer` | `TSAN` | Exact | 1 | +1 |
| `gossip-protocols` | `gossip protocol` | Insensitive | 1 | +1 |
| `apache-hive` | `HiveQL` | Exact | 1 | +1 |
| `google-gemini-api` | `Gemini API` | Insensitive | 3 | +3 |
| `autoregressive-decoding` | `Autoregressive generation` | Insensitive | 1 | +1 |

The `Hits` figures for aliases on the same canonical overlap. They must not be
summed to obtain a canonical count.

## Canonical repairs

These are more accurate than adding aliases without changing the label.

| Current canonical | Recommended change |
|---|---|
| `power-query-m` | Rename to `power-query`, display as `Power Query`, and reclassify from language to tool. Retain `Power Query M` as an alias. |
| `software-stress-testing` | Rename to `stress-testing` / `Stress testing`. The corpus uses the method across software, hardware, and finance, so `software` is falsely narrow. |
| `lseg-workspace-refinitiv-workspace` | Keep the current mapping but add predecessor product name `Eikon`; do not add bare `Reuters`. |

## Do not add

These candidates had hits but failed precision, ownership, or granularity.

| Candidate | Observed evidence | Decision |
|---|---:|---|
| `REST` as case-insensitive | 8,563 hits, +6,018 | Reject. It matches ordinary `rest`; exact `REST` is already present and matched 2,487 descriptions. |
| `OR Tools` | 182 hits, +182 | Reject. Nearly all are ordinary phrases ending in “or tools.” |
| `event driven` | 94 hits, +45 | Reject as too broad; retain the explicit architecture aliases. |
| `zero-copy` / `zero copy` | 43 / 3 hits | Reject. Matches Snowflake and Workday product wording that does not imply zero-copy architecture. |
| lowercase `rust` | 2,711 hits, +23 | Reject. Exact `Rust` already captures the technology without metal/corrosion noise. |
| `FP8`, `BF16`, `FP16` for mixed precision | 77 / 27 / 12 hits | Reject collision. These already belong to `floating-point-arithmetic`; do not double-own them. |
| `deterministic execution` | 21 hits, +16 | Reject as not synonymous with low-latency engineering. |
| `user-space networking` | 7 hits, +6 | Reject as broader than kernel bypass. |
| `encoder-decoder` | 5 hits, +5 | Reject as broader than transformer architecture. |
| `assisted generation` | 5 hits, +5 | Reject as ambiguous outside explicit speculative-decoding context. |
| `round robin` / `round-robin` | 3 / 9 hits | Reject. Samples include sales-lead assignment and unrelated schedulers. |
| lowercase `tcp` | 1,233 hits, +2 | Reject. Exact `TCP` is already present; the marginal gain does not justify broadening. |
| lowercase `raft` | 25 hits, +2 | Reject. Exact `Raft` is already present. |
| `draft models` | 6 hits, +2 | Reject. Samples include ordinary AI-assisted drafting. |
| `benchmark library` | 1 hit, +1 | Reject. The hit is a finance-comparables collection, not Google Benchmark. |
| `survivorship bias` | 1 hit, +1 | Reject as a related concern, not an alias for backtesting. |
| bare `Scheme` | 509 hits | Reject. Samples are employee-benefit and pension schemes. |
| bare `MSL` | 120 hits | Reject. Samples refer to marketing stages and organization names, not Metal Shading Language. |
| bare `Thrust` | 32 hits | Reject. Most samples concern aerospace propulsion; only one sampled use was the CUDA library. |
| bare `Ninja` | 46 hits | Reject. Samples refer to Ninja RMM, company employees, and figurative titles. |
| bare `Cargo` | 59 hits | Keep ambiguous. Valid Rust uses coexist with logistics and unrelated products. |
| bare `Maple` | 3 hits | Reject. All hits were a place, company/protocol name, or customer name. |
| bare `Gemini` | 878 hits | Reject for the API canonical. Many hits indicate general model/tool use rather than API work. |
| `Google Gemini` | 57 hits | Do not map to the API canonical; add a separate model/product canonical only if that distinction is wanted. |
| `Convex optimization` for convex analysis | 40 hits | Reject collision and wrong equivalence; `convex-optimization` already owns it. |

## Current zero-hit canonicals after the proposed repairs

The following still have no safe match in this corpus. Do not promote their
ambiguous short forms merely to make the count nonzero:

`common-lisp`, `scheme`, `metal-shading-language`, `requests`,
`google-benchmark`, `cuda-thrust`, `ninja`, `uv`, `cargo`, `maple`,
`markov-chains`, and `convex-analysis`.

For maximum current-corpus compactness, the strongest removal candidates are
`requests`, `uv`, `maple`, `common-lisp`, `scheme`, and
`metal-shading-language`. The others are concrete and relevant enough to keep
fail-closed even though their safe aliases currently have zero hits.
