# Job goals — three targets, one skill spine

Companion to `resume-strategy.md` (*how* to write the resume) and `template.md`
(*what fields* the studio stores). This file is *what to aim at* and *why* — the
three resume variants and the roles each one is pointed at.

---

## Selection criteria

These targets were chosen against five constraints, in this order:

1. **Maximize compensation** — top of the software pay distribution, not merely good
2. **Broad net with overlapping preparation** — one deep skill investment, several exits
3. **Not prestige-gated** — demonstrable skill beats school name
4. **Compounds with experience** — worth more at year 10 than year 2
5. **CS *and* applied math both pay rent**

Constraints 3 and 5 pull against each other. The most math-intensive roles (quant
research, ML research) are the most credential- and PhD-gated. Resolving that
tension by leaning **systems** is deliberate: it keeps the door open, and the math
becomes a differentiator rather than an entry requirement.

---

## The shared spine

Three resumes, **one content bank**. This is the whole thesis — the core below is
`audience: all` and appears on every variant. Only the top third of each page
actually differs.

- **Modern C++** at performance level — not syntax, but knowing what the compiler emits
- **Computer architecture** — cache hierarchy, memory bandwidth as the binding
  constraint, NUMA, SIMD, branch behavior
- **Performance engineering as a discipline** — profiling, benchmark methodology,
  roofline analysis; knowing your intuition about where time goes is wrong
- **Concurrency** — lock-free structures, memory ordering, false sharing
- **Linux internals** — `perf`, eBPF, `io_uring`, scheduling, hugepages
- **Floating point behavior** — where numerics stops being abstract

The overlap between HFT low-latency work and ML inference work is larger than either
camp assumes: both are about squeezing tail latency out of a hardware/software stack.
Kernel bypass and RDMA are cousins. Crossing between them is a short step.

**Where the applied math actually bites** — aim at these sub-niches deliberately,
because generic systems work doesn't need the degree:

- Quantization numerics — error propagation, calibration, outlier handling in fp8/int4
- Scheduling and batching — continuous batching under SLOs is queueing theory plus
  constrained optimization
- Compiler optimization — polyhedral methods, loop transformation, autotuning as search
- Performance modeling — roofline, Little's Law, Amdahl/Gustafson

---

## Target 1 — GPU / ML Performance Engineer

Absorbs both inference serving and compiler/kernel work; early-career they are one
story: *I make computation on accelerators faster, and I can prove it with numbers.*

**Titles:** Inference Engineer · ML Systems Engineer · GPU Kernel Engineer · Deep
Learning Compiler Engineer · Performance Engineer, ML · Deep Learning Software
Engineer (Nvidia's phrasing) · Software Engineer, Model Serving · AI Infrastructure
Engineer · Member of Technical Staff, Inference (frontier-lab phrasing)

**Where:** Nvidia, AMD, Modular, Together, Fireworks, Baseten, Databricks, Modal,
Cerebras, Groq, Tenstorrent. Frontier labs are upside, not plan.

**Must prove:** you reason about memory bandwidth before FLOPs, you benchmark with
methodology rather than vibes, and you have touched real kernels.

**Metrics that belong here:** tokens/sec · TTFT and inter-token latency · achieved
TFLOPs vs peak · bandwidth utilization · occupancy

**Skills to load:** CUDA, Triton, PTX, NCCL, TensorRT-LLM, MLIR/LLVM, quantization
(fp8/int4), paged attention, continuous batching, speculative decoding

**Why it survives criterion 3:** this is the *least* prestige-gated of the three.
The work is publicly verifiable — a fused attention kernel with honest benchmarks, or
real contributions to vLLM / SGLang / PyTorch / Triton, function as the credential.
Supply is constrained enough that demonstrated work beats pedigree.

---

## Target 2 — Systems / Infrastructure Engineer

**The on-ramp, not the fallback.** Point it at teams adjacent to accelerators — GPU
cluster scheduling, high-performance networking, training-data storage — so the
lateral move into Target 1 is a step, not a career change.

**Titles:** Software Engineer, Infrastructure · Software Engineer, Distributed
Systems · Systems Software Engineer · Software Engineer, Platform · Kernel Engineer ·
Software Engineer, Storage / Networking / Compute

**Avoid:** generic "Backend Engineer" (dilutes the spine) and SRE — adjacent, but a
different track with a different compounding curve. Don't drift into it by accident.

**Must prove:** systems that stay correct and fast under load.

**Metrics:** p99 latency · throughput · QPS · node and fleet counts · failure-mode
handling

---

## Target 3 — Low-Latency / Quantitative Developer

Note the framing: **low-latency systems developer**, not quant researcher and not
trader. Different roles, different gates — applying to those with this resume wastes
the shot.

**Titles:** Quantitative Developer · Software Engineer, Core Infrastructure · C++
Developer, Trading Systems · Low Latency Engineer · Trading Systems Engineer · Market
Data Engineer · Platform Engineer, Trading

**Where, given criterion 3:** lead with the second tier — Optiver, IMC, DRW, Old
Mission, Akuna, Belvedere, Headlands — plus exchange and market-data infrastructure
(CME, Nasdaq, the vendor ecosystem), which is the most open door in the space. Treat
Jane Street, HRT, and Citadel Securities as lottery tickets bought with a Codeforces
rating, not as the plan.

**Must prove:** you control *tail* latency and you measure honestly.

**Metrics:** nanoseconds and microseconds · jitter · p99.9

**Skills:** lock-free structures, kernel bypass, cache-line discipline, deterministic
behavior. This is the only variant where probability and competitive-programming
signal earn their place on the page.

**The honest caveat:** this is the *most* prestige-gated of the three, in direct
tension with criterion 3. Interviews are meritocratic; the resume screen is not. Two
workarounds, both load-bearing: competitive programming as the accepted non-pedigree
credential, and the second-tier firm list above.

---

## Intern/co-op reality — the priority inverts

The ordering above is the **career destination**. At intern level the pipelines look
different, and this matters more than the long-arc ranking right now:

| Target | Intern pipeline | Practical read |
|---|---|---|
| 1 — GPU / ML perf | **Thin.** Few teams run undergrad programs; Nvidia and AMD are the real exceptions | Portfolio-build + convert later |
| 2 — Systems / infra | **Abundant.** Standard big-tech + infra startups | Primary volume play |
| 3 — Quant dev | **Excellent.** Large, structured, well-defined programs | Most legible pipeline of the three |

So: apply broadly to **2 and 3**, and build toward **1** through open source and
project work while entering via the other two. A general SWE internship at a company
that operates GPU infrastructure is a legitimate Target 1 on-ramp.

**Co-op is an advantage worth exploiting.** A six-month co-op is far more attractive
to small infrastructure teams than a 12-week intern, because the ramp cost finally
amortizes. Teams that decline interns will often take a co-op — this is the single
best lever for reaching Target 1 early.

**Timing asymmetry (as of August 2026):** quant is the urgent one. Per the verified
addendum in `resume-strategy.md`, quant firms opened in August and roughly 70% of
Citadel's offers are signed by mid-October even though portals stay open into
December. Target 3 applications should go out first, and now.

---

## Studio wiring

The `variant` field is currently `swe | quant | research`, and so is bullet
`audience`. The three targets above are ML-systems / infra-SWE / quant-dev — two are
"swe"-flavored and `research` goes unused.

Two options:

1. Repurpose `research` to mean the ML/GPU resume. Works mechanically, since variant
   only drives bullet filtering — but reads wrong forever.
2. Widen both enums to `mlsys | swe | quant`. Contained edit: the two `Literal`s in
   `oneshot_ui/resume_models.py`, the two `<select>` blocks in
   `frontend/resume/index.html`, and the variant list in
   `frontend/resume/app.js`.

Option 2 is preferred; not yet done.

---

## Risk to manage

Target 1 is a hot market, and hot markets cool. The protection is to build the
**durable layer** — architecture, numerics, parallelism, performance methodology —
and treat current serving frameworks as disposable. Deep CUDA and memory-hierarchy
fluency compounds; configuration expertise in this quarter's inference stack does not.

Target 3 compounds steeply *within* the industry but is less portable outward, and
carries firm-level and performance-linked comp volatility. Higher starting number,
narrower later optionality.

*Compensation and market claims here are strategic rather than sourced; the timing
and process claims come from the verified addendum in `resume-strategy.md`.*
