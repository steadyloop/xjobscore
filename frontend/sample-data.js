(function (root) {
  "use strict";

  // A per-page suffix is appended: "…enabled.application", "…enabled.resume".
  // The bare key is a v1 leftover and is never read; clearing it is not worth a
  // migration, since an unread key changes nothing.
  const ENABLED_KEY = "oneshot.sample-data.enabled";
  const STATE_KEY = "oneshot.sample-data.state.v7";
  const VISITOR_STATE_KEY = "oneshot.visitor-data.state.v1";
  // Mirrors oneshot_ui/repository.py's DEFAULT_JOB_TYPES: the live API reads no
  // job_type filter as this classified scope, not as "everything" — job_type is
  // NULL when no vendor rule matched, and that pool is opt-in via the
  // "unclassified" token.
  const DEFAULT_JOB_TYPES = ["intern", "coop", "student_program", "research", "part_time"];
  // The fixture mirrors the live read-time policy for the normalized cities it
  // contains. The full production metro map remains server-side.
  const SAMPLE_US_HUBS = new Set([
    "NY|New York City", "CA|San Francisco", "CA|San Diego", "TX|Austin",
    "MA|Boston", "MA|Cambridge", "DC|Washington", "IL|Chicago",
    "WA|Seattle",
  ]);
  const SAMPLE_US_SUBURBS = new Set(["CA|Berkeley"]);
  const SAMPLE_NORCAL = new Set(["CA|San Francisco", "CA|Berkeley"]);
  const SAMPLE_SOCAL = new Set(["CA|San Diego"]);
  const SAMPLE_SEATTLE = new Set(["WA|Seattle"]);
  // Mirrors repository.list_jobs: `unclassified` is not a stored value, it is
  // the NULL pool, so it is matched separately from the named types.
  const matchesType = (types, job) =>
    job.job_type === null || job.job_type === undefined
      ? types.includes("unclassified")
      : types.includes(job.job_type);
  const clone = value => JSON.parse(JSON.stringify(value));
  const isoIn = (days = 0, hours = 0) => new Date(Date.now() + days * 86400000 + hours * 3600000).toISOString();
  const dateIn = (days = 0) => new Date(Date.now() + days * 86400000).toISOString().slice(0, 10);
  const escapeRegex = value => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const locationBucket = job => {
    const locations = Array.isArray(job?.locations) ? job.locations : [];
    if (!locations.length) return 4;
    const us = locations.filter(location => String(location?.country || "").toUpperCase() === "US");
    if (!us.length) return null;
    const keys = us.map(location => `${String(location.state || "").toUpperCase()}|${location.city || ""}`);
    if (keys.some(key => SAMPLE_US_HUBS.has(key))) return 1;
    if (keys.some(key => SAMPLE_US_SUBURBS.has(key))) return 2;
    return 3;
  };
  const proximityPriority = job => {
    const keys = (Array.isArray(job?.locations) ? job.locations : [])
      .filter(location => String(location?.country || "").toUpperCase() === "US")
      .map(location => `${String(location.state || "").toUpperCase()}|${location.city || ""}`);
    if (keys.some(key => SAMPLE_NORCAL.has(key))) return 1;
    if (keys.some(key => SAMPLE_SOCAL.has(key))) return 2;
    if (keys.some(key => SAMPLE_SEATTLE.has(key))) return 3;
    return 4;
  };
  const recencyDay = job => {
    const value = job?.posted_at || job?.first_seen_at;
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "";
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(date).filter(part => part.type !== "literal").map(part => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
  };

  // Mirrors oneshot_ui/resume_workspace.py's TECH_VOCABULARY — kept in sync by hand.
  const TECH_VOCABULARY = [
    "Python", "C", "C++", "C#", "Java", "JavaScript", "TypeScript", "Go", "Rust",
    "Kotlin", "Swift", "Scala", "R", "Julia", "MATLAB", "SQL", "Bash", "OCaml",
    "Haskell", "React", "Vue", "Angular", "Node.js", "Django", "Flask", "FastAPI",
    "Spring", "NumPy", "pandas", "SciPy", "scikit-learn", "PyTorch", "TensorFlow",
    "JAX", "Keras", "XGBoost", "Spark", "Hadoop", "Kafka", "Airflow", "dbt",
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "DuckDB", "Snowflake",
    "Docker", "Kubernetes", "Terraform", "AWS", "GCP", "Azure", "Linux", "Git",
    "CI/CD", "GraphQL", "gRPC", "REST", "WebSocket", "CUDA", "OpenMP", "MPI",
    "machine learning", "deep learning", "reinforcement learning", "NLP",
    "computer vision", "linear algebra", "probability", "statistics",
    "stochastic processes", "optimization", "numerical methods", "PDE",
    "Monte Carlo", "time series", "regression", "A/B testing", "data structures",
    "algorithms", "distributed systems", "operating systems", "networking",
    "concurrency", "functional programming", "object-oriented",
    // GPU / ML systems
    "Triton", "CUTLASS", "cuBLAS", "NCCL", "TensorRT", "vLLM", "SGLang", "PTX",
    "ROCm", "HIP", "MLIR", "LLVM", "XLA", "TVM", "Nsight", "GPU", "tensor cores",
    "kernel optimization", "quantization", "mixed precision", "FP8", "FP4", "GEMM",
    "attention", "transformers", "KV cache", "inference", "model serving",
    "batching", "roofline", "memory bandwidth", "SIMD", "vectorization", "HPC",
    "parallel computing", "RDMA", "InfiniBand", "profiling", "compilers",
    "computer architecture",
    // Low latency / trading systems
    "low latency", "lock-free", "kernel bypass", "DPDK", "io_uring", "NUMA",
    "cache optimization", "market data", "order book", "market microstructure",
    "FIX protocol", "ITCH", "floating point", "numerical linear algebra",
    "queueing theory",
    // Infrastructure
    "eBPF", "CMake", "Bazel", "gdb", "Raft", "consensus", "Prometheus", "Grafana",
  ];

  function createResumeState() {
    const profile = {
      full_name: "Wren Adeyemi",
      email: "w.adeyemi@gatech.edu",
      phone: "404-555-0182",
      location: "Atlanta, GA",
      links: [
        { label: "GitHub", url: "https://github.com/wadeyemi" },
        { label: "wren.dev/perf", url: "https://wren.dev/perf" },
        { label: "Codeforces", url: "https://codeforces.com/profile/wadeyemi" },
        { label: "LinkedIn", url: "https://linkedin.com/in/wadeyemi" },
      ],
    };
    const entries = [
      {
        id: 1, kind: "education", org: "Georgia Institute of Technology",
        role: "B.S. Computer Science and B.S. Applied Mathematics", location: "Atlanta, GA",
        start_date: "Aug 2024", end_date: "May 2028", url: "",
        detail: "Systems & Architecture thread", position: 1,
        bullets: [],
      },
      {
        id: 2, kind: "experience", org: "NVIDIA", role: "Deep Learning Software Engineering Intern",
        location: "Santa Clara, CA", start_date: "Jan 2026", end_date: "Jun 2026", url: "",
        detail: "6-month co-op", position: 1,
        bullets: [
          {
            id: 101, entry_id: 2, position: 1, audience: "mlsys",
            tags: ["cuda", "attention", "blackwell"],
            text: "Optimized a fused multi-head attention kernel for Blackwell SM100, raising achieved throughput from 61% to 83% of peak FP8 tensor-core FLOPs at 4K sequence length on B200",
            defense_notes: "Warp-specialized producer/consumer pipeline with TMA async copies. Verified in Nsight Compute across 8 head-dim configurations. The final 6 points came from eliminating a shared-memory bank conflict in the V tile.",
          },
          {
            id: 102, entry_id: 2, position: 2, audience: "all",
            tags: ["memory bandwidth", "kv cache", "profiling"],
            text: "Cut HBM traffic 38% in a decode-phase attention kernel by recoalescing KV-cache reads into 128-byte segments, lifting measured bandwidth utilization to 91% of achievable peak",
            defense_notes: "Root cause was a stride-2 access pattern across the head dimension. Nsight memory workload analysis showed 4 sectors fetched per 32-byte request before the fix and 1.1 after.",
          },
          {
            id: 103, entry_id: 2, position: 3, audience: "mlsys",
            tags: ["quantization", "fp4", "numerics"],
            text: "Built an NVFP4 post-training quantization harness holding perplexity within 0.4% of BF16 across 6 model families while cutting weight footprint 3.6x",
            defense_notes: "Per-block E4M3 scales with outlier-channel isolation, calibrated on 512 sequences. Failure mode was activation outliers in the first and last two layers, which stay in higher precision.",
          },
          {
            id: 104, entry_id: 2, position: 4, audience: "all",
            tags: ["ci", "benchmarking"],
            text: "Shipped 4 kernels into an internal benchmark suite now running in nightly CI across A100, H100, and B200",
            defense_notes: "Wrote the regression gate too: a run fails if median throughput drops more than 2% against the rolling 7-day baseline, which caught 3 real regressions.",
          },
        ],
      },
      {
        id: 3, kind: "research", org: "Georgia Tech — School of Computational Science & Engineering",
        role: "Undergraduate Research Assistant", location: "Atlanta, GA",
        start_date: "May 2025", end_date: "Present", url: "", detail: "Parallel systems group", position: 1,
        bullets: [
          {
            id: 105, entry_id: 3, position: 1, audience: "mlsys",
            tags: ["nccl", "distributed", "profiling"],
            text: "Traced a 22% training-step regression on a 64-GPU cluster to an NCCL ring topology mismatch across NVLink domains, recovering it through tuned all-reduce algorithm selection",
            defense_notes: "Found it in Nsight Systems timelines — the all-reduce was crossing the NVSwitch boundary twice per step. Fix was pinning NCCL_ALGO and correcting the rank-to-device mapping.",
          },
          {
            id: 106, entry_id: 3, position: 2, audience: "all",
            tags: ["roofline", "performance modeling", "applied math"],
            text: "Derived a roofline model for a 3D stencil solver predicting measured performance within 7% across 5 problem sizes, directing a tiling rewrite worth 2.4x end to end",
            defense_notes: "Arithmetic intensity computed per tile size against measured STREAM bandwidth. The model said the kernel was bandwidth-bound well before the compute roof, so the rewrite targeted reuse rather than vectorization.",
          },
          {
            id: 107, entry_id: 3, position: 3, audience: "mlsys",
            tags: ["numerics", "quantization", "error analysis"],
            text: "Proved an error bound for block-scaled FP4 quantization matching empirical MSE within 9% across 3 tensor distributions, now used to select block size in the group's compression work",
            defense_notes: "Bound follows from treating per-block scaling as a conditional quantizer and bounding the tail contribution of outliers. Tightest for Gaussian-like tensors, loosest for heavy-tailed activations.",
          },
          {
            id: 108, entry_id: 3, position: 4, audience: "all",
            tags: ["publication"],
            text: "Co-authored a workshop paper on overlapping gradient all-reduce with backward-pass compute, accepted at an SC'26 workshop",
            defense_notes: "Second author. Contributed the profiling methodology and the overlap scheduler; the theory section was the postdoc's.",
          },
        ],
      },
      {
        id: 4, kind: "project", org: "vLLM and SGLang", role: "Open-source contributor",
        location: "", start_date: "Jan 2025", end_date: "Present",
        url: "https://github.com/wadeyemi", detail: "CUDA, Triton, Python", position: 1,
        bullets: [
          {
            id: 109, entry_id: 4, position: 1, audience: "mlsys",
            tags: ["vllm", "sglang", "paged attention"],
            text: "Merged 11 pull requests across vLLM and SGLang, including a paged-attention block-table fix that removed a 3.1% throughput regression on long-context decode",
            defense_notes: "The block table was being rebuilt per step instead of incrementally appended, so cost grew with context length. Caught it by profiling a 32K-context run against an 8K one.",
          },
          {
            id: 110, entry_id: 4, position: 2, audience: "mlsys",
            tags: ["triton", "kernel fusion"],
            text: "Contributed a Triton kernel fusing RMSNorm with residual add, cutting normalization time 44% and now the default path on Hopper and Blackwell",
            defense_notes: "Fusion removes one full read and write of the hidden state. Had to keep an unfused fallback for head dims that overflow shared memory.",
          },
          {
            id: 111, entry_id: 4, position: 3, audience: "all",
            tags: ["debugging", "numerics"],
            text: "Bisected a numerical divergence between FP8 and BF16 attention that had blocked a release, isolating an unsafe softmax rescale within 2 days",
            defense_notes: "The max-subtraction was done in FP8 after scaling rather than before, so large logits saturated. Reproduced deterministically with a seeded 3-token case.",
          },
        ],
      },
      {
        id: 5, kind: "project", org: "Tick-to-book market data feed handler", role: "Solo project",
        location: "", start_date: "Jun 2025", end_date: "Present",
        url: "https://github.com/wadeyemi/itch-handler", detail: "C++20, Linux, lock-free", position: 2,
        bullets: [
          {
            id: 112, entry_id: 5, position: 1, audience: "quant",
            tags: ["low latency", "itch", "c++"],
            text: "Built a NASDAQ ITCH 5.0 feed handler in C++20 sustaining 4.2M messages/sec on a single core at 780 ns median and 2.1 us p99.9 tick-to-book latency",
            defense_notes: "Order book is an intrusive price-level list with a flat array for the top 64 levels. Parser is a branch-light switch on message type with no dynamic allocation after startup.",
          },
          {
            id: 113, entry_id: 5, position: 2, audience: "quant",
            tags: ["lock-free", "concurrency", "allocator"],
            text: "Cut p99.9 latency 6.3x against a std::deque baseline using a lock-free SPSC ring buffer and arena allocation to keep the hot path free of allocator jitter",
            defense_notes: "The tail was almost entirely malloc arena contention. Ring buffer uses acquire/release on a power-of-two index with cache-line padding to avoid false sharing between producer and consumer.",
          },
          {
            id: 114, entry_id: 5, position: 3, audience: "quant",
            tags: ["measurement", "benchmarking"],
            text: "Measured with rdtsc timestamping and HdrHistogram under core isolation and disabled C-states, holding run-to-run p99 variance under 3%",
            defense_notes: "isolcpus plus taskset pinning, hugepages for the book arena, and turbo disabled so the TSC-to-nanosecond conversion stays stable. Ran 30 trials per configuration.",
          },
        ],
      },
      {
        id: 6, kind: "project", org: "SGEMM from scratch — 6 optimization stages", role: "Solo project",
        location: "", start_date: "Mar 2025", end_date: "Aug 2025",
        url: "https://wren.dev/perf/sgemm", detail: "CUDA C++, Nsight Compute", position: 3,
        bullets: [
          {
            id: 115, entry_id: 6, position: 1, audience: "mlsys",
            tags: ["cuda", "gemm", "optimization"],
            text: "Wrote an SGEMM kernel from scratch reaching 91% of cuBLAS on H100 across 6 documented stages, from naive to warp-tiled with double buffering",
            defense_notes: "Stages: naive, global coalescing, shared-memory tiling, 1D then 2D register tiling, vectorized 128-bit loads, double buffering. Each stage has its own Nsight trace in the writeup.",
          },
          {
            id: 116, entry_id: 6, position: 2, audience: "mlsys",
            tags: ["gpu mode", "fp4", "competition"],
            text: "Placed 7th of 340 entries on the GPU MODE NVFP4 kernel leaderboard for a block-scaled matmul on B200",
            defense_notes: "Win came from staging the FP4 scale factors in shared memory once per tile rather than re-reading them per fragment.",
          },
          {
            id: 117, entry_id: 6, position: 3, audience: "all",
            tags: ["writing", "teaching"],
            text: "Published a 6-part optimization writeup that drew 40,000+ reads and 2 upstream fixes submitted by readers",
            defense_notes: "One reader found my occupancy calculation assumed the wrong register budget for SM90, which I corrected in a follow-up post.",
          },
        ],
      },
      {
        id: 7, kind: "project", org: "Raft-backed distributed key-value store", role: "Solo project",
        location: "", start_date: "Sep 2025", end_date: "Feb 2026",
        url: "https://github.com/wadeyemi/raftkv", detail: "Go, gRPC, Docker", position: 4,
        bullets: [
          {
            id: 118, entry_id: 7, position: 1, audience: "swe",
            tags: ["raft", "consensus", "distributed systems"],
            text: "Implemented Raft leader election, log replication, and snapshotting in Go, passing 200+ randomized fault-injection tests including network partitions and clock skew",
            defense_notes: "Followed the extended Raft paper. Hardest part was snapshot installation racing with in-flight AppendEntries; solved by versioning the snapshot index.",
          },
          {
            id: 119, entry_id: 7, position: 2, audience: "swe",
            tags: ["throughput", "linearizability"],
            text: "Sustained 48,000 writes/sec at 9 ms p99 across a 5-node cluster, with linearizability checked against 1M-operation generated histories",
            defense_notes: "Batching AppendEntries by 10 ms windows was the single biggest throughput win. History checking used a Jepsen-style linearizability checker.",
          },
          {
            id: 120, entry_id: 7, position: 3, audience: "swe",
            tags: ["latency", "failover"],
            text: "Cut recovery after leader loss from 4.1 s to 640 ms by tuning election timeouts and pre-voting to suppress disruptive candidates",
            defense_notes: "Pre-vote prevents a partitioned node from bumping the term on rejoin. Timeout range picked so the spread exceeds one round-trip and split votes stay rare.",
          },
        ],
      },
      {
        id: 8, kind: "leadership", org: "ICPC and Codeforces", role: "Team captain",
        location: "Atlanta, GA", start_date: "Aug 2024", end_date: "Present", url: "", detail: "", position: 1,
        bullets: [
          {
            id: 121, entry_id: 8, position: 1, audience: "quant",
            tags: ["competitive programming"],
            text: "Reached Codeforces Master (peak rating 2,187), placing in the top 2% of 42,000+ rated contest participants",
            defense_notes: "Strongest at graph and greedy problems, weakest at heavy combinatorics. Roughly 180 rated contests.",
          },
          {
            id: 122, entry_id: 8, position: 2, audience: "quant",
            tags: ["icpc"],
            text: "Captained a 3-person ICPC team to 4th of 62 at the Southeast Regional, advancing to the North America Championship",
            defense_notes: "Ran the team's problem triage: read all problems in the first 12 minutes, sorted by expected solve time, split by strength.",
          },
          {
            id: 123, entry_id: 8, position: 3, audience: "all",
            tags: ["mentoring"],
            text: "Coached 25 underclassmen through a 10-week contest training series, with 6 advancing to regionals",
            defense_notes: "",
          },
        ],
      },
      {
        id: 9, kind: "leadership", org: "Georgia Tech GPU Programming Group", role: "Founder",
        location: "Atlanta, GA", start_date: "Jan 2025", end_date: "Present", url: "", detail: "", position: 2,
        bullets: [
          {
            id: 124, entry_id: 9, position: 1, audience: "mlsys",
            tags: ["community", "teaching"],
            text: "Founded a GPU programming reading group that grew to 45 weekly attendees over 3 semesters",
            defense_notes: "",
          },
          {
            id: 125, entry_id: 9, position: 2, audience: "all",
            tags: ["teaching", "cuda"],
            text: "Ran a 6-week CUDA workshop series for 80+ students with open-sourced materials since reused in a graduate course",
            defense_notes: "Labs build up to a tiled matmul. Materials are on GitHub with a Colab fallback for students without a local GPU.",
          },
        ],
      },
      {
        id: 10, kind: "experience", org: "Georgia Tech — CS 2200 Computer Systems & Networks",
        role: "Undergraduate Teaching Assistant", location: "Atlanta, GA",
        start_date: "Aug 2025", end_date: "Dec 2025", url: "", detail: "", position: 2,
        bullets: [
          {
            id: 126, entry_id: 10, position: 1, audience: "all",
            tags: ["teaching", "architecture"],
            text: "Taught weekly recitation for 65 students on cache hierarchy, virtual memory, pipelining, and TCP congestion control",
            defense_notes: "",
          },
          {
            id: 127, entry_id: 10, position: 2, audience: "swe",
            tags: ["testing", "tooling"],
            text: "Rebuilt 3 autograder suites around a deterministic timing harness, cutting spurious failures on performance labs 78%",
            defense_notes: "Old harness used wall-clock on shared machines. Replaced with instruction counts from perf counters, which are stable under load.",
          },
        ],
      },
    ];
    const skills = [
      { id: 201, category: "language", name: "C++", audience: "all" },
      { id: 202, category: "language", name: "Python", audience: "all" },
      { id: 203, category: "language", name: "CUDA", audience: "mlsys" },
      { id: 204, category: "language", name: "C", audience: "all" },
      { id: 205, category: "language", name: "Go", audience: "swe" },
      { id: 206, category: "language", name: "Rust", audience: "swe" },
      { id: 207, category: "language", name: "SQL", audience: "swe" },
      { id: 208, category: "language", name: "Bash", audience: "all" },
      { id: 209, category: "framework", name: "Triton", audience: "mlsys" },
      { id: 210, category: "framework", name: "PyTorch", audience: "mlsys" },
      { id: 211, category: "framework", name: "CUTLASS", audience: "mlsys" },
      { id: 212, category: "framework", name: "cuBLAS", audience: "mlsys" },
      { id: 213, category: "framework", name: "NCCL", audience: "mlsys" },
      { id: 214, category: "framework", name: "TensorRT", audience: "mlsys" },
      { id: 215, category: "framework", name: "vLLM", audience: "mlsys" },
      { id: 216, category: "framework", name: "SGLang", audience: "mlsys" },
      { id: 217, category: "framework", name: "MPI", audience: "all" },
      { id: 218, category: "framework", name: "OpenMP", audience: "all" },
      { id: 219, category: "framework", name: "gRPC", audience: "swe" },
      { id: 220, category: "tool", name: "Nsight", audience: "mlsys" },
      { id: 221, category: "tool", name: "Linux", audience: "all" },
      { id: 222, category: "tool", name: "gdb", audience: "all" },
      { id: 223, category: "tool", name: "Git", audience: "all" },
      { id: 224, category: "tool", name: "CMake", audience: "all" },
      { id: 225, category: "tool", name: "Docker", audience: "swe" },
      { id: 226, category: "tool", name: "Kubernetes", audience: "swe" },
      { id: 227, category: "tool", name: "eBPF", audience: "swe" },
      { id: 228, category: "tool", name: "HdrHistogram", audience: "quant" },
      { id: 229, category: "tool", name: "profiling", audience: "all" },
      { id: 230, category: "tool", name: "Prometheus", audience: "swe" },
      { id: 231, category: "coursework", name: "Computer Architecture", audience: "all" },
      { id: 232, category: "coursework", name: "Operating Systems", audience: "all" },
      { id: 233, category: "coursework", name: "Distributed Systems", audience: "swe" },
      { id: 234, category: "coursework", name: "Parallel Computing", audience: "mlsys" },
      { id: 235, category: "coursework", name: "Compilers", audience: "mlsys" },
      { id: 236, category: "coursework", name: "Numerical Methods", audience: "mlsys" },
      { id: 237, category: "coursework", name: "Linear Algebra", audience: "all" },
      { id: 238, category: "coursework", name: "Probability", audience: "quant" },
      { id: 239, category: "coursework", name: "Statistics", audience: "quant" },
      { id: 240, category: "coursework", name: "Stochastic Processes", audience: "quant" },
      { id: 241, category: "coursework", name: "Real Analysis", audience: "quant" },
      { id: 242, category: "coursework", name: "Algorithms", audience: "all" },
      { id: 243, category: "coursework", name: "Data Structures", audience: "all" },
      { id: 244, category: "coursework", name: "Networking", audience: "swe" },
      { id: 245, category: "coursework", name: "Optimization", audience: "mlsys" },
      { id: 246, category: "math", name: "quantization", audience: "mlsys" },
      { id: 247, category: "math", name: "mixed precision", audience: "mlsys" },
      { id: 248, category: "math", name: "roofline", audience: "mlsys" },
      { id: 249, category: "math", name: "memory bandwidth", audience: "mlsys" },
      { id: 250, category: "math", name: "KV cache", audience: "mlsys" },
      { id: 251, category: "math", name: "numerical linear algebra", audience: "mlsys" },
      { id: 252, category: "math", name: "floating point", audience: "mlsys" },
      { id: 253, category: "math", name: "concurrency", audience: "all" },
      { id: 254, category: "math", name: "low latency", audience: "quant" },
      { id: 255, category: "math", name: "lock-free", audience: "quant" },
      { id: 256, category: "math", name: "market data", audience: "quant" },
      { id: 257, category: "math", name: "order book", audience: "quant" },
      { id: 258, category: "math", name: "NUMA", audience: "quant" },
      { id: 259, category: "math", name: "queueing theory", audience: "all" },
      { id: 260, category: "math", name: "Monte Carlo", audience: "quant" },
    ];
    const targetJobs = [
      {
        id: 301, company: "NVIDIA", title: "Deep Learning Inference Software Engineer Intern — Summer 2027",
        url: "https://example.com/apply/sample-mlsys",
        jd_text: "Join the inference performance team building the kernels and runtimes behind large-scale model serving. "
          + "You will profile and optimize GPU kernels, improve memory bandwidth utilization, and push quantization "
          + "to FP8 and FP4 without losing accuracy. Strong C++ and CUDA required; experience with Triton, CUTLASS, "
          + "or Nsight is a plus. Familiarity with attention, KV cache management, batching, and transformers "
          + "expected. Background in computer architecture, numerical methods, and linear algebra valued. "
          + "Contributions to open-source inference projects such as vLLM or TensorRT are strongly preferred.",
        date_posted: dateIn(-9), note: "Referral: Dele, GPU group alum, now on the kernels team.",
        created_at: isoIn(-9),
      },
      {
        id: 302, company: "Anthropic", title: "Software Engineer Intern, Compute Infrastructure — Summer 2027",
        url: "https://example.com/apply/sample-infra",
        jd_text: "Build and operate the training and serving infrastructure behind frontier models. Work spans "
          + "cluster scheduling, high-performance networking, storage, and observability. We look for strong "
          + "systems fundamentals: Linux, concurrency, distributed systems, and networking. Experience with Go, "
          + "Rust, Python, Kubernetes, Docker, and gRPC is relevant. Familiarity with RDMA, InfiniBand, NCCL, "
          + "or GPU fleet operations is a plus. You should be comfortable profiling and debugging production "
          + "systems and reasoning about failure modes at scale.",
        date_posted: dateIn(-4), note: "Applied 2026-08-06. OA expected within a week — keep DS&A warm.",
        created_at: isoIn(-4),
      },
      {
        id: 303, company: "Optiver", title: "Quantitative Software Engineer Intern — Summer 2027",
        url: "https://example.com/apply/sample-quant",
        jd_text: "Work alongside traders and researchers on the low latency systems that price and execute in "
          + "global markets. You will write modern C++ on Linux, profile hot paths down to the nanosecond, and "
          + "care deeply about cache behavior, NUMA effects, and lock-free concurrency. Exposure to market data "
          + "feeds, order book construction, or FIX protocol is a plus. We value strong probability, statistics, "
          + "and algorithms, and a track record in competitive programming. Kernel bypass and io_uring experience "
          + "welcome but not required.",
        date_posted: dateIn(-11), note: "Opened early August — ~70% of offers sign by mid-October. Apply first.",
        created_at: isoIn(-11),
      },
    ];
    const resumes = [
      {
        id: 401, name: "ML Systems — Summer 2027", target_job_id: 301, variant: "mlsys",
        availability_line: "Available for Summer 2027 internships and Spring 2027 co-op (6 months)",
        education_note: "GPA: 3.91/4.00",
        selection: {
          entries: [
            { entry_id: 1, bullets: [] },
            { entry_id: 2, bullets: [{ bullet_id: 101 }, { bullet_id: 102 }, { bullet_id: 103 }] },
            { entry_id: 3, bullets: [{ bullet_id: 105 }, { bullet_id: 106 }, { bullet_id: 107 }] },
            { entry_id: 4, bullets: [{ bullet_id: 109 }, { bullet_id: 110 }] },
            { entry_id: 6, bullets: [{ bullet_id: 115 }, { bullet_id: 116 }] },
          ],
          skills: [201, 202, 203, 209, 211, 213, 214, 215, 220, 221, 231, 234, 236, 237,
                   246, 247, 248, 249, 251, 252],
        },
        created_at: isoIn(-8), updated_at: isoIn(-1),
      },
      {
        id: 402, name: "Systems SWE — Summer 2027", target_job_id: 302, variant: "swe",
        availability_line: "Available for Summer 2027 internships and Spring 2027 co-op (6 months)",
        education_note: "GPA: 3.91/4.00",
        selection: {
          entries: [
            { entry_id: 1, bullets: [] },
            { entry_id: 2, bullets: [{ bullet_id: 102 }, { bullet_id: 104 }] },
            { entry_id: 10, bullets: [{ bullet_id: 126 }, { bullet_id: 127 }] },
            { entry_id: 7, bullets: [{ bullet_id: 118 }, { bullet_id: 119 }, { bullet_id: 120 }] },
            { entry_id: 3, bullets: [{ bullet_id: 105 }, { bullet_id: 106 }, { bullet_id: 108 }] },
            { entry_id: 4, bullets: [{ bullet_id: 109 }, { bullet_id: 111 }] },
          ],
          skills: [201, 202, 205, 206, 207, 208, 217, 219, 221, 222, 223, 224, 225, 226,
                   227, 229, 230, 231, 232, 233, 244, 253, 259],
        },
        created_at: isoIn(-6), updated_at: isoIn(-2),
      },
      {
        id: 403, name: "Quant Dev — Summer 2027", target_job_id: 303, variant: "quant",
        availability_line: "Available for Summer 2027 internships and Spring 2027 co-op (6 months)",
        education_note: "GPA: 3.91/4.00",
        selection: {
          entries: [
            { entry_id: 1, bullets: [] },
            { entry_id: 5, bullets: [{ bullet_id: 112 }, { bullet_id: 113 }, { bullet_id: 114 }] },
            { entry_id: 2, bullets: [{ bullet_id: 101 }, { bullet_id: 102 }, { bullet_id: 104 }] },
            { entry_id: 6, bullets: [{ bullet_id: 115 }, { bullet_id: 116 }] },
            { entry_id: 8, bullets: [{ bullet_id: 121 }, { bullet_id: 122 }, { bullet_id: 123 }] },
            { entry_id: 3, bullets: [{ bullet_id: 106 }] },
          ],
          skills: [201, 202, 204, 221, 222, 228, 231, 238, 239, 240, 242,
                   253, 254, 255, 256, 257, 258, 260],
        },
        created_at: isoIn(-10), updated_at: isoIn(-1),
      },
    ];
    return {
      profile, entries, skills, targetJobs, resumes,
      nextEntryId: 11, nextBulletId: 128, nextSkillId: 261, nextTargetJobId: 304, nextResumeId: 404,
    };
  }

  function createVisitorState() {
    return {
      jobs: [],
      applications: [],
      nextApplicationId: 1,
      applicationIdPrefix: "visitor-app",
      resume: {
        profile: { full_name: "", email: "", phone: "", location: "", links: [] },
        entries: [],
        skills: [],
        targetJobs: [],
        resumes: [],
        nextEntryId: 1,
        nextBulletId: 1,
        nextSkillId: 1,
        nextTargetJobId: 1,
        nextResumeId: 1,
      },
    };
  }

  function createState() {
    const jobs = [
      {
        id: "sample-research",
        company: "Northwind Robotics",
        title: "Student Researcher, Distributed Training",
        location: "Remote, United States",
        locations: [{ country: "US", state: null, city: null }],
        posted_at: isoIn(0, -2),
        first_seen_at: isoIn(0, -2),
        last_seen_at: isoIn(0, -1),
        status: "active",
        source: "greenhouse:northwind-robotics",
        source_url: "https://example.com/jobs/sample-research",
        summary: "Research sharded optimizer state and fault-tolerant training for multi-node machine-learning workloads. You will prototype ideas, evaluate them on production-scale clusters, and present results to the research systems team.",
        job_type: "research",
        remote: "yes",
        attributes: { version: 1, term: "Spring 2027", skills: ["JAX", "Python", "Ray"], degree_fields: ["computer_science", "mathematics"], deadline_at: dateIn(18) },
        apply_url: "https://example.com/apply/sample-research",
      },
      {
        id: "sample-quant",
        company: "Vantage Quant",
        title: "Summer Quant Systems Intern",
        location: "New York, NY",
        locations: [{ country: "US", state: "NY", city: "New York City" }],
        posted_at: isoIn(-1),
        first_seen_at: isoIn(-1),
        last_seen_at: isoIn(0, -3),
        status: "active",
        source: "lever:vantage-quant",
        source_url: "https://example.com/jobs/sample-quant",
        summary: "Build research infrastructure used to test and deploy systematic trading strategies. Projects include low-latency data processing, simulation tooling, and production monitoring.",
        job_type: "intern",
        remote: "unknown",
        attributes: { version: 1, term: "Summer 2027", skills: ["Python", "C++", "Linux", "Microsoft Excel", "FIX Protocol"], degree_fields: ["mathematics"] },
        apply_url: "https://example.com/apply/sample-quant",
      },
      {
        id: "sample-software",
        company: "Halcyon Systems",
        title: "Software Engineering Intern",
        location: "San Diego, CA",
        locations: [{ country: "US", state: "CA", city: "San Diego" }],
        posted_at: isoIn(-2),
        first_seen_at: isoIn(-2),
        last_seen_at: isoIn(0, -4),
        status: "active",
        source: "ashby:halcyon-systems",
        source_url: "https://example.com/jobs/sample-software",
        summary: "Join the platform team building services for fleet operations. You will own a scoped backend project, write production code, and work with engineers across infrastructure and product.",
        job_type: "intern",
        remote: "hybrid",
        attributes: { version: 1, skills: ["Go", "Kubernetes", "PostgreSQL"], degree_fields: ["computer_science"] },
        apply_url: "https://example.com/apply/sample-software",
      },
      {
        id: "sample-embedded",
        company: "Cobalt Foundry",
        title: "Fall Embedded Software Co-op",
        location: "Austin, TX",
        locations: [{ country: "US", state: "TX", city: "Austin" }],
        posted_at: isoIn(-3),
        first_seen_at: isoIn(-3),
        last_seen_at: isoIn(-1),
        status: "active",
        source: "workday:cobalt-foundry",
        source_url: "https://example.com/jobs/sample-embedded",
        summary: "Develop firmware and systems software for industrial control hardware. The role includes driver development, hardware bring-up, automated testing, and debugging real-time systems.",
        job_type: "coop",
        remote: "unknown",
        attributes: { version: 1, term: "Fall 2027", skills: ["C++", "FreeRTOS", "Linux", "FPGA"], degree_fields: ["computer_science"] },
        apply_url: "https://example.com/apply/sample-embedded",
      },
      {
        id: "sample-platform",
        company: "Arbor Signal",
        title: "Backend Platform Intern",
        location: "Boston, MA",
        locations: [{ country: "US", state: "MA", city: "Boston" }],
        posted_at: isoIn(-4),
        first_seen_at: isoIn(-4),
        last_seen_at: isoIn(0, -5),
        status: "active",
        source: "smartrecruiters:arbor-signal",
        source_url: "https://example.com/jobs/sample-platform",
        summary: "Build internal APIs and data services for a growing observability platform. You will ship a production project with the infrastructure team.",
        job_type: "intern",
        remote: "hybrid",
        attributes: { version: 1, term: "Summer 2027", skills: ["TypeScript", "PostgreSQL", "AWS", "Docker", "JSON"] },
        apply_url: "https://example.com/apply/sample-platform",
      },
      {
        id: "sample-ml-research",
        company: "Kepler Bio",
        title: "Machine Learning Research Intern",
        location: "Cambridge, MA",
        locations: [{ country: "US", state: "MA", city: "Cambridge" }],
        posted_at: isoIn(-5),
        first_seen_at: isoIn(-5),
        last_seen_at: isoIn(0, -6),
        status: "active",
        source: "icims:kepler-bio",
        source_url: "https://example.com/jobs/sample-ml-research",
        summary: "Develop evaluation methods for biological foundation models and collaborate with research scientists on reproducible experiments.",
        job_type: "intern",
        remote: "unknown",
        attributes: { version: 1, term: "Summer 2027", skills: ["Python", "PyTorch", "Hugging Face Transformers", "Model evaluation", "Artificial intelligence"], degree_fields: ["biotech"] },
        apply_url: "https://example.com/apply/sample-ml-research",
      },
      {
        id: "sample-graduate-research",
        company: "Pacific Materials Lab",
        title: "Graduate Research Appointment",
        location: "Berkeley, CA",
        locations: [{ country: "US", state: "CA", city: "Berkeley" }],
        posted_at: isoIn(-6),
        first_seen_at: isoIn(-6),
        last_seen_at: isoIn(0, -7),
        status: "active",
        source: "greenhouse:pacific-materials",
        source_url: "https://example.com/jobs/sample-graduate-research",
        summary: "Investigate efficient simulation methods with the computational materials group and publish reproducible results.",
        job_type: "research",
        remote: "hybrid",
        attributes: { version: 1, skills: ["Python", "JAX"] },
        apply_url: "https://example.com/apply/sample-graduate-research",
      },
      {
        id: "sample-fellowship",
        company: "Civic AI Institute",
        title: "AI Policy Fellowship",
        location: "Washington, DC",
        locations: [{ country: "US", state: "DC", city: "Washington" }],
        posted_at: isoIn(-7),
        first_seen_at: isoIn(-7),
        last_seen_at: isoIn(-1),
        status: "active",
        source: "lever:civic-ai",
        source_url: "https://example.com/jobs/sample-fellowship",
        summary: "Join a twelve-week cohort studying practical governance approaches for deployed AI systems.",
        job_type: "student_program",
        remote: "hybrid",
        attributes: { version: 1, term: "Summer 2027", skills: ["Policy research", "Python"] },
        apply_url: "https://example.com/apply/sample-fellowship",
      },
      {
        id: "sample-apprentice",
        company: "Forge Automation",
        title: "Software Engineering Apprentice",
        location: "Chicago, IL",
        locations: [{ country: "US", state: "IL", city: "Chicago" }],
        posted_at: isoIn(-8),
        first_seen_at: isoIn(-8),
        last_seen_at: isoIn(-1),
        status: "active",
        source: "workday:forge-automation",
        source_url: "https://example.com/jobs/sample-apprentice",
        summary: "Learn production software development through mentored rotations across platform and automation teams.",
        job_type: "student_program",
        remote: "unknown",
        attributes: { version: 1, skills: ["Java", "SQL"] },
        apply_url: "https://example.com/apply/sample-apprentice",
      },
      {
        id: "sample-new-grad",
        company: "Atlas Compute",
        title: "Software Engineer, New Grad",
        location: "San Francisco, CA",
        locations: [{ country: "US", state: "CA", city: "San Francisco" }],
        posted_at: isoIn(-9),
        first_seen_at: isoIn(-9),
        last_seen_at: isoIn(-2),
        status: "active",
        source: "ashby:atlas-compute",
        source_url: "https://example.com/jobs/sample-new-grad",
        summary: "Start in a structured engineering cohort with rotations through distributed systems and developer infrastructure.",
        job_type: "student_program",
        remote: "unknown",
        attributes: { version: 1, skills: ["Go", "Linux"] },
        apply_url: "https://example.com/apply/sample-new-grad",
      },
      {
        id: "sample-working-student",
        company: "Helix Cloud",
        title: "Working Student, Developer Infrastructure",
        location: "Berlin, Germany",
        locations: [{ country: "DE", state: "16", city: "Berlin" }],
        posted_at: isoIn(-10),
        first_seen_at: isoIn(-10),
        last_seen_at: isoIn(-2),
        status: "active",
        source: "smartrecruiters:helix-cloud",
        source_url: "https://example.com/jobs/sample-working-student",
        summary: "Support build tooling and internal developer workflows alongside your university studies.",
        job_type: "student_program",
        remote: "hybrid",
        attributes: { version: 1, skills: ["TypeScript", "CI/CD"] },
        apply_url: "https://example.com/apply/sample-working-student",
      },
      {
        // Deliberately outside DEFAULT_OPPORTUNITY_TYPES, mirroring the live
        // pool of postings the classifier has not selected for the board.
        id: "sample-unclassified",
        company: "Bellwether Analytics",
        title: "Senior Data Platform Manager",
        location: "New York, NY",
        locations: [{ country: "US", state: "NY", city: "New York City" }],
        posted_at: isoIn(-11),
        first_seen_at: isoIn(-11),
        last_seen_at: isoIn(0, -12),
        status: "active",
        source: "greenhouse:bellwether-analytics",
        source_url: "https://example.com/jobs/sample-unclassified",
        summary: null,
        job_type: null,
        remote: "hybrid",
        attributes: { version: 1, skills: ["SQL", "Apache Spark"] },
        apply_url: "https://example.com/apply/sample-unclassified",
      },
    ];

    const jobSkillDetails = {
      "Apache Spark": { id: "apache-spark", category: "platform" },
      "Artificial intelligence": { id: "artificial-intelligence", name: "Artificial intelligence (AI)", label: "AI", category: "concept" },
      AWS: { id: "amazon-web-services", name: "Amazon Web Services (AWS)", label: "AWS", category: "platform" },
      "C++": { id: "cpp", category: "language" },
      "CI/CD": { id: "ci-cd", category: "method" },
      Docker: { id: "docker", category: "tool" },
      FPGA: { id: "fpga", category: "hardware" },
      "FIX Protocol": { id: "fix-protocol", category: "protocol" },
      FreeRTOS: { id: "freertos", category: "platform" },
      Go: { id: "go", category: "language" },
      "Hugging Face Transformers": { id: "hugging-face-transformers", label: "HF Transformers", category: "library" },
      Java: { id: "java", category: "language" },
      JAX: { id: "jax", category: "library" },
      JSON: { id: "json", category: "format" },
      Kubernetes: { id: "kubernetes", category: "platform" },
      Linux: { id: "linux", category: "platform" },
      "Microsoft Excel": { id: "microsoft-excel", label: "Excel", category: "tool" },
      "Model evaluation": { id: "model-evaluation", category: "method" },
      PostgreSQL: { id: "postgresql", category: "datastore" },
      Python: { id: "python", category: "language" },
      PyTorch: { id: "pytorch", category: "library" },
      Ray: { id: "ray", category: "framework" },
      SQL: { id: "sql", category: "language" },
      TypeScript: { id: "typescript", category: "language" },
    };

    for (const job of jobs) {
      job.other_sources = [];
      job.skills = (job.attributes.skills || []).map(name => ({
        id: jobSkillDetails[name]?.id || String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
        ...jobSkillDetails[name],
        name: jobSkillDetails[name]?.name || name,
        label: jobSkillDetails[name]?.label || name,
        category: jobSkillDetails[name]?.category || null,
      }));
    }

    const applications = [
      {
        id: "sample-app-considering",
        job_id: "sample-quant",
        company: "Vantage Quant",
        title: "Summer Quant Systems Intern",
        stage: "considering",
        closed_reason: null,
        next_action: "Review the role and decide whether to apply",
        next_due_at: isoIn(4),
        external: false,
        submitted_at: null,
        apply_url_snapshot: "https://example.com/apply/sample-quant",
        updated_at: isoIn(-1),
        note: "",
        events: [{ occurred_at: isoIn(-1), event_type: "considering", to_stage: "considering", note: "Added from Discover." }],
      },
      {
        id: "sample-app-assessment",
        job_id: "sample-research",
        company: "Northwind Robotics",
        title: "Student Researcher, Distributed Training",
        stage: "assessment",
        closed_reason: null,
        next_action: "Complete the systems exercise",
        next_due_at: isoIn(1),
        external: false,
        submitted_at: isoIn(-6),
        apply_url_snapshot: "https://example.com/apply/sample-research",
        updated_at: isoIn(-1),
        note: "",
        events: [
          { occurred_at: isoIn(-6), event_type: "applied", to_stage: "applied", note: "Application submitted." },
          { occurred_at: isoIn(-1), event_type: "assessment", to_stage: "assessment", note: "Systems exercise received." },
        ],
      },
      {
        id: "sample-app-interview",
        company: "Meridian Labs",
        title: "Robotics Software Co-op",
        stage: "interview",
        closed_reason: null,
        next_action: "Prepare architecture examples",
        next_due_at: isoIn(7),
        external: true,
        submitted_at: isoIn(-10),
        apply_url_snapshot: "https://example.com/apply/meridian",
        updated_at: isoIn(-2),
        note: "Technical interview scheduled with the platform team.",
        events: [{ occurred_at: isoIn(-2), event_type: "interview", to_stage: "interview", note: "Technical interview scheduled." }],
      },
      {
        id: "sample-app-offer",
        company: "Atlas Grid",
        title: "Site Reliability Intern",
        stage: "offer",
        closed_reason: null,
        next_action: "Review the written offer",
        next_due_at: isoIn(10),
        external: true,
        submitted_at: isoIn(-18),
        apply_url_snapshot: "https://example.com/apply/atlas",
        updated_at: isoIn(-1),
        note: "Compare the start date and relocation package.",
        events: [{ occurred_at: isoIn(-1), event_type: "offer", to_stage: "offer", note: "Written offer received." }],
      },
    ];

    return { jobs, applications, nextApplicationId: 5, resume: createResumeState() };
  }

  const jsonResponse = (payload, status = 200) => new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
  const parseBody = options => {
    try { return JSON.parse(options?.body || "{}"); }
    catch (_) { return {}; }
  };
  const findJob = (state, id) => state.jobs.find(job => String(job.id) === String(id));
  const findApplication = (state, id) => state.applications.find(application => String(application.id) === String(id));

  function applicationFromJob(state, job) {
    return {
      id: `${state.applicationIdPrefix || "sample-app"}-${state.nextApplicationId++}`,
      job_id: job.id,
      company: job.company,
      title: job.title,
      location: job.location,
      stage: "considering",
      closed_reason: null,
      next_action: null,
      next_due_at: null,
      external: false,
      submitted_at: null,
      apply_url_snapshot: job.apply_url,
      updated_at: new Date().toISOString(),
      note: "",
      events: [{ occurred_at: new Date().toISOString(), event_type: "stage_changed", to_stage: "considering", note: null }],
    };
  }

  async function handle(input, options = {}, state = createState()) {
    const url = new URL(typeof input === "string" ? input : input.url, "http://oneshot.local");
    const method = String(options.method || "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api(?=\/)/, "");

    if (method === "GET" && path === "/health") {
      return jsonResponse({ ok: true });
    }
    if (method === "GET" && path === "/jobs") {
      const q = String(url.searchParams.get("q") || "").trim().toLowerCase();
      const requested = url.searchParams.getAll("job_type");
      const types = requested.length ? requested : DEFAULT_JOB_TYPES;
      const degreeFields = url.searchParams.getAll("degree_field");
      const excludedJobIds = new Set(url.searchParams.getAll("exclude_job_id"));
      const applicationJobIds = new Set(state.applications.map(application => application.job_id).filter(Boolean));
      const available = state.jobs.filter(job => {
        if (locationBucket(job) === null) return false;
        const searchable = `${job.title} ${job.company} ${(job.skills || []).map(skill => skill.name).join(" ")}`.toLowerCase();
        const postingFields = job.attributes.degree_fields || [];
        const matchesDegree = !degreeFields.length || degreeFields.some(field =>
          field === "bioengineering"
            ? ["biotech", "med_device", "clinical_affairs"].some(value => postingFields.includes(value))
            : postingFields.includes(field));
        return !applicationJobIds.has(job.id) && !excludedJobIds.has(String(job.id)) && (!q || searchable.includes(q)) && matchesDegree;
      });
      const job_type_counts = Object.fromEntries(DEFAULT_JOB_TYPES.map(type => [
        type, available.filter(job => job.job_type === type).length,
      ]));
      const filtered = available.filter(job => matchesType(types, job));
      const sort = url.searchParams.get("sort") || "fresh";
      filtered.sort((a, b) => sort === "company"
        ? a.company.localeCompare(b.company)
        : sort === "title"
          ? a.title.localeCompare(b.title)
          : recencyDay(b).localeCompare(recencyDay(a))
            || locationBucket(a) - locationBucket(b)
            || proximityPriority(a) - proximityPriority(b)
            || String(b.posted_at || b.first_seen_at || "").localeCompare(String(a.posted_at || a.first_seen_at || "")));
      const limit = Math.max(1, Math.min(500, Number(url.searchParams.get("limit")) || 100));
      const offset = Math.max(0, Number(url.searchParams.get("offset")) || 0);
      const page = filtered.slice(offset, offset + limit);
      return jsonResponse({ jobs: clone(page), total: filtered.length, job_type_counts, limit, offset });
    }

    const addMatch = path.match(/^\/jobs\/([^/]+)\/applications$/);
    if (method === "POST" && addMatch) {
      const job = findJob(state, decodeURIComponent(addMatch[1]));
      if (!job) return jsonResponse({ detail: "Job not found" }, 404);
      const existing = state.applications.find(application => application.job_id === job.id);
      if (existing) return jsonResponse({ ...clone(existing), already_exists: true });
      const application = applicationFromJob(state, job);
      state.applications.unshift(application);
      return jsonResponse(clone(application), 201);
    }

    if (method === "POST" && path === "/applications/external") {
      const body = parseBody(options);
      const application = {
        id: `${state.applicationIdPrefix || "sample-app"}-${state.nextApplicationId++}`,
        company: body.company,
        title: body.title,
        stage: "considering",
        closed_reason: null,
        next_action: null,
        next_due_at: null,
        external: true,
        submitted_at: null,
        apply_url_snapshot: body.url,
        updated_at: new Date().toISOString(),
        location: body.location || "",
        note: body.note || "",
        events: [{ occurred_at: new Date().toISOString(), event_type: "stage_changed", to_stage: "considering", note: body.note || null }],
      };
      state.applications.unshift(application);
      return jsonResponse(clone(application), 201);
    }

    if (method === "GET" && path === "/applications") {
      const applications = state.applications.map(({ events, ...application }) => application);
      return jsonResponse({ applications: clone(applications) });
    }

    const applicationMatch = path.match(/^\/applications\/([^/]+)$/);
    if (method === "GET" && applicationMatch) {
      const application = findApplication(state, decodeURIComponent(applicationMatch[1]));
      return application ? jsonResponse(clone(application)) : jsonResponse({ detail: "Application not found" }, 404);
    }
    if (method === "PATCH" && applicationMatch) {
      const application = findApplication(state, decodeURIComponent(applicationMatch[1]));
      if (!application) return jsonResponse({ detail: "Application not found" }, 404);
      const body = parseBody(options),previousStage=application.stage;
      if (application.external) {
        if (typeof body.company === "string" && body.company.trim()) application.company = body.company.trim();
        if (typeof body.title === "string" && body.title.trim()) application.title = body.title.trim();
        if (typeof body.apply_url_snapshot === "string") application.apply_url_snapshot = body.apply_url_snapshot.trim();
      }
      if (body.stage) application.stage = body.stage;
      if (body.closed_reason !== undefined) application.closed_reason = body.closed_reason;
      if (application.stage !== "closed") application.closed_reason = null;
      if (body.submitted_at !== undefined) application.submitted_at = body.submitted_at;
      if (body.next_action !== undefined) application.next_action = body.next_action;
      if (body.next_due_at !== undefined) application.next_due_at = body.next_due_at;
      if (body.note !== undefined) application.note = body.note;
      application.updated_at = new Date().toISOString();
      if (application.stage !== previousStage) application.events.push({ occurred_at: application.updated_at, event_type: "stage_changed", to_stage: application.stage, note: "" });
      return jsonResponse(clone(application));
    }
    if (method === "DELETE" && applicationMatch) {
      const index = state.applications.findIndex(application => String(application.id) === String(decodeURIComponent(applicationMatch[1])));
      if (index < 0) return jsonResponse({ detail: "Application not found" }, 404);
      state.applications.splice(index, 1);
      return new Response(null, { status: 204 });
    }

    if (method === "GET" && path === "/source-coverage") {
      return jsonResponse({
        sources: [
          { source: "greenhouse", type: "ATS", board_count: 1, coverage: 1, last_poll_at: isoIn(0, -1), health: "Healthy", added_today: 1 },
          { source: "lever", type: "ATS", board_count: 2, coverage: 2, last_poll_at: isoIn(0, -3), health: "Healthy", added_today: 1 },
          { source: "smartrecruiters", type: "ATS", board_count: 1, coverage: 1, last_poll_at: isoIn(-1), health: "Needs attention", added_today: 0 },
        ],
        source_count: 3,
        total_coverage: 4,
        total_added_today: 2,
      });
    }

    // A state persisted before the resume workspace existed has no `resume` key.
    const resumeState = state.resume || (state.resume = createResumeState());

    if (method === "GET" && path === "/resume/profile") {
      return jsonResponse(clone(resumeState.profile));
    }
    if (method === "PUT" && path === "/resume/profile") {
      const body = parseBody(options);
      ["full_name", "email", "phone", "location", "links"].forEach(key => {
        if (body[key] !== undefined) resumeState.profile[key] = body[key];
      });
      return jsonResponse(clone(resumeState.profile));
    }

    if (method === "GET" && path === "/resume/entries") {
      return jsonResponse({ entries: clone(resumeState.entries) });
    }
    if (method === "POST" && path === "/resume/entries") {
      const body = parseBody(options);
      const siblings = resumeState.entries.filter(entry => entry.kind === body.kind);
      const entry = {
        id: resumeState.nextEntryId++, kind: body.kind, org: body.org,
        role: body.role || "", location: body.location || "",
        start_date: body.start_date || "", end_date: body.end_date || "",
        url: body.url || "", detail: body.detail || "",
        position: siblings.length + 1, bullets: [],
      };
      resumeState.entries.push(entry);
      return jsonResponse(clone(entry), 201);
    }
    const entryMatch = path.match(/^\/resume\/entries\/(\d+)$/);
    if (method === "PATCH" && entryMatch) {
      const entry = resumeState.entries.find(row => String(row.id) === entryMatch[1]);
      if (!entry) return jsonResponse({ detail: "Entry not found" }, 404);
      const body = parseBody(options);
      ["kind", "org", "role", "location", "start_date", "end_date", "url", "detail", "position"].forEach(key => {
        if (body[key] !== undefined) entry[key] = body[key];
      });
      return jsonResponse(clone(entry));
    }
    if (method === "DELETE" && entryMatch) {
      const index = resumeState.entries.findIndex(row => String(row.id) === entryMatch[1]);
      if (index < 0) return jsonResponse({ detail: "Entry not found" }, 404);
      resumeState.entries.splice(index, 1);
      return new Response(null, { status: 204 });
    }

    const entryBulletsMatch = path.match(/^\/resume\/entries\/(\d+)\/bullets$/);
    if (method === "POST" && entryBulletsMatch) {
      const entry = resumeState.entries.find(row => String(row.id) === entryBulletsMatch[1]);
      if (!entry) return jsonResponse({ detail: "Entry not found" }, 404);
      const body = parseBody(options);
      const bullet = {
        id: resumeState.nextBulletId++, entry_id: entry.id,
        text: body.text, audience: body.audience || "all",
        tags: body.tags || [], defense_notes: body.defense_notes || "",
        position: entry.bullets.length + 1,
      };
      entry.bullets.push(bullet);
      return jsonResponse(clone(bullet), 201);
    }
    const bulletMatch = path.match(/^\/resume\/bullets\/(\d+)$/);
    if (method === "PATCH" && bulletMatch) {
      const bullet = resumeState.entries.flatMap(entry => entry.bullets).find(row => String(row.id) === bulletMatch[1]);
      if (!bullet) return jsonResponse({ detail: "Bullet not found" }, 404);
      const body = parseBody(options);
      ["text", "audience", "tags", "defense_notes", "position"].forEach(key => {
        if (body[key] !== undefined) bullet[key] = body[key];
      });
      return jsonResponse(clone(bullet));
    }
    if (method === "DELETE" && bulletMatch) {
      for (const entry of resumeState.entries) {
        const index = entry.bullets.findIndex(row => String(row.id) === bulletMatch[1]);
        if (index >= 0) {
          entry.bullets.splice(index, 1);
          return new Response(null, { status: 204 });
        }
      }
      return jsonResponse({ detail: "Bullet not found" }, 404);
    }

    if (method === "GET" && path === "/resume/skills") {
      return jsonResponse({ skills: clone(resumeState.skills) });
    }
    if (method === "POST" && path === "/resume/skills") {
      const body = parseBody(options);
      const duplicate = resumeState.skills.some(skill =>
        skill.category === body.category && skill.name.toLowerCase() === String(body.name).toLowerCase());
      if (duplicate) return jsonResponse({ detail: "Skill already exists in that category" }, 409);
      const skill = { id: resumeState.nextSkillId++, category: body.category, name: body.name, audience: body.audience || "all" };
      resumeState.skills.push(skill);
      return jsonResponse(clone(skill), 201);
    }
    const skillMatch = path.match(/^\/resume\/skills\/(\d+)$/);
    if (method === "DELETE" && skillMatch) {
      const index = resumeState.skills.findIndex(row => String(row.id) === skillMatch[1]);
      if (index < 0) return jsonResponse({ detail: "Skill not found" }, 404);
      resumeState.skills.splice(index, 1);
      return new Response(null, { status: 204 });
    }

    if (method === "GET" && path === "/resume/jobs") {
      return jsonResponse({ jobs: clone(resumeState.targetJobs) });
    }
    if (method === "POST" && path === "/resume/jobs") {
      const body = parseBody(options);
      const job = {
        id: resumeState.nextTargetJobId++, company: body.company, title: body.title,
        url: body.url || "", jd_text: body.jd_text || "", date_posted: body.date_posted || "",
        note: body.note || "", created_at: new Date().toISOString(),
      };
      resumeState.targetJobs.unshift(job);
      return jsonResponse(clone(job), 201);
    }
    const targetJobMatch = path.match(/^\/resume\/jobs\/(\d+)$/);
    if (method === "PATCH" && targetJobMatch) {
      const job = resumeState.targetJobs.find(row => String(row.id) === targetJobMatch[1]);
      if (!job) return jsonResponse({ detail: "Target job not found" }, 404);
      const body = parseBody(options);
      ["company", "title", "url", "jd_text", "date_posted", "note"].forEach(key => {
        if (body[key] !== undefined) job[key] = body[key];
      });
      return jsonResponse(clone(job));
    }
    if (method === "DELETE" && targetJobMatch) {
      const index = resumeState.targetJobs.findIndex(row => String(row.id) === targetJobMatch[1]);
      if (index < 0) return jsonResponse({ detail: "Target job not found" }, 404);
      const deletedId = resumeState.targetJobs[index].id;
      resumeState.targetJobs.splice(index, 1);
      resumeState.resumes.forEach(record => {
        if (record.target_job_id === deletedId) record.target_job_id = null;
      });
      return new Response(null, { status: 204 });
    }

    const coverageMatch = path.match(/^\/resume\/jobs\/(\d+)\/coverage$/);
    if (method === "GET" && coverageMatch) {
      const job = resumeState.targetJobs.find(row => String(row.id) === coverageMatch[1]);
      if (!job) return jsonResponse({ detail: "Target job not found" }, 404);
      const jd = (job.jd_text || "").toLowerCase();
      const mentions = name => new RegExp(`(?<![a-z0-9])${escapeRegex(name.toLowerCase())}(?![a-z0-9])`).test(jd);
      const inJd = resumeState.skills.filter(skill => skill.name && mentions(skill.name));
      const resumeIdParam = url.searchParams.get("resume_id");
      let shownIds = new Set();
      if (resumeIdParam) {
        const linkedResume = resumeState.resumes.find(row => String(row.id) === resumeIdParam);
        if (linkedResume) shownIds = new Set(linkedResume.selection.skills || []);
      }
      const bankNames = new Set(resumeState.skills.map(skill => skill.name.toLowerCase()));
      const missing = TECH_VOCABULARY.filter(term => !bankNames.has(term.toLowerCase()) && mentions(term)).sort();
      return jsonResponse({
        job_id: job.id,
        matched: clone(inJd.filter(skill => shownIds.has(skill.id))),
        have_not_shown: clone(inJd.filter(skill => !shownIds.has(skill.id))),
        missing,
      });
    }

    if (method === "GET" && path === "/resume/resumes") {
      return jsonResponse({ resumes: clone(resumeState.resumes), typst_available: false });
    }
    if (method === "POST" && path === "/resume/resumes") {
      const body = parseBody(options);
      const resumeRecord = {
        id: resumeState.nextResumeId++, name: body.name, target_job_id: body.target_job_id ?? null,
        variant: body.variant || "swe", availability_line: body.availability_line || "",
        education_note: body.education_note || "", selection: body.selection || { entries: [], skills: [] },
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      resumeState.resumes.unshift(resumeRecord);
      return jsonResponse(clone(resumeRecord), 201);
    }
    const resumeIdMatch = path.match(/^\/resume\/resumes\/(\d+)$/);
    if (method === "GET" && resumeIdMatch) {
      const record = resumeState.resumes.find(row => String(row.id) === resumeIdMatch[1]);
      return record ? jsonResponse(clone(record)) : jsonResponse({ detail: "Resume not found" }, 404);
    }
    if (method === "PATCH" && resumeIdMatch) {
      const record = resumeState.resumes.find(row => String(row.id) === resumeIdMatch[1]);
      if (!record) return jsonResponse({ detail: "Resume not found" }, 404);
      const body = parseBody(options);
      ["name", "target_job_id", "variant", "availability_line", "education_note", "selection"].forEach(key => {
        if (body[key] !== undefined) record[key] = body[key];
      });
      record.updated_at = new Date().toISOString();
      return jsonResponse(clone(record));
    }
    if (method === "DELETE" && resumeIdMatch) {
      const index = resumeState.resumes.findIndex(row => String(row.id) === resumeIdMatch[1]);
      if (index < 0) return jsonResponse({ detail: "Resume not found" }, 404);
      resumeState.resumes.splice(index, 1);
      return new Response(null, { status: 204 });
    }

    const checkMatch = path.match(/^\/resume\/resumes\/(\d+)\/check$/);
    if (method === "GET" && checkMatch) {
      const record = resumeState.resumes.find(row => String(row.id) === checkMatch[1]);
      if (!record) return jsonResponse({ detail: "Resume not found" }, 404);
      const bulletById = new Map(resumeState.entries.flatMap(entry => entry.bullets).map(bullet => [bullet.id, bullet]));
      const selected = (record.selection?.entries || []).flatMap(entry =>
        (entry.bullets || []).map(pick => pick.text_override || bulletById.get(pick.bullet_id)?.text).filter(Boolean));
      const problems = [];
      if (!resumeState.profile.email) problems.push({ level: "error", message: "Profile has no email address." });
      if (!record.availability_line) problems.push({ level: "warn", message: "No availability line (e.g. 'Available for Spring 2027 co-op') — recruiters filter on legible availability." });
      if (!selected.length) problems.push({ level: "error", message: "No bullets selected." });
      selected.forEach(text => {
        if (!/\d/.test(text)) problems.push({ level: "info", message: `No metric in: "${text.slice(0, 70)}…" — quantify if honestly possible.` });
      });
      return jsonResponse({
        problems,
        page_count: null,
        typst_available: false,
        compile_error: "Browser-local resumes require sign-in for Typst export.",
      });
    }

    if (path.startsWith("/resume/resumes/") && (path.endsWith("/source") || path.endsWith("/pdf"))) {
      return jsonResponse({ detail: "Sign in to export this browser-local resume." }, 501);
    }

    return jsonResponse({ detail: `No sample response for ${method} ${path}` }, 404);
  }

  let state;
  let visitorState;
  let accountPromise;
  const storageAvailable = () => Boolean(root.localStorage);

  // Which page you are on decides which switch you are flipping. Only the two
  // stateful workspaces expose fixture mode: Opportunities remains a live job
  // feed, and Source Coverage remains a live report on the harvest itself.
  //
  // Scoping by pathname rather than by an argument is what keeps every caller
  // unchanged — a page only ever requests its own data, so "is sample mode on"
  // and "is sample mode on for this page" are the same question at every call
  // site (api-client.js's transport, resume/app.js's private api()).
  const PAGES = Object.freeze({
    "/application/": "application",
    "/resume/": "resume",
  });

  // Every shell answers on two URLs — the canonical /application/ the nav links
  // to, and /static/application/index.html from the StaticFiles mount. Both are
  // the same page, so both have to resolve to the same switch, or the mount
  // path would show a switch that silently stores nothing.
  const scopeOf = path => PAGES[String(path || "").replace(/^\/static/, "").replace(/index\.html$/, "")];
  const enabledKey = () => {
    const scope = scopeOf(root.location?.pathname);
    return scope ? `${ENABLED_KEY}.${scope}` : null;
  };

  const isEnabled = () => {
    const key = enabledKey();
    return Boolean(key) && storageAvailable() && root.localStorage.getItem(key) === "1";
  };
  const save = () => { if (storageAvailable()) root.localStorage.setItem(STATE_KEY, JSON.stringify(state)); };
  const loadState = () => {
    if (state) return state;
    try { state = JSON.parse(root.localStorage.getItem(STATE_KEY)) || createState(); }
    catch (_) { state = createState(); }
    return state;
  };
  const setEnabled = value => {
    const key = enabledKey();
    if (!key || !storageAvailable()) return;
    root.localStorage.setItem(key, value ? "1" : "0");
    // The fixture store stays shared across pages — an application created from
    // a sample job has to be the same record on both. So seed it only when
    // there is nothing stored; re-seeding on every flip would throw away the
    // edits made on the other page, which now has its own independent switch.
    if (value && !root.localStorage.getItem(STATE_KEY)) { state = createState(); save(); }
  };

  // Called when the account chip resolves to a real session: sample data is a
  // guest affordance, and a flag left on from browsing signed out would show a
  // signed-in user fixtures in place of their own workspace, with no visible
  // switch to explain it. Returns whether *this* page was the one affected, so
  // the caller can reload only when the page in front of you is now wrong.
  const disableAll = () => {
    if (!storageAvailable()) return false;
    const wasOn = isEnabled();
    Object.values(PAGES).forEach(scope => root.localStorage.removeItem(`${ENABLED_KEY}.${scope}`));
    return wasOn;
  };
  const request = async (path, options = {}) => {
    const result = await handle(path, options, loadState());
    if (String(options.method || "GET").toUpperCase() !== "GET") save();
    return result;
  };

  const loadVisitorState = () => {
    if (visitorState) return visitorState;
    try { visitorState = JSON.parse(root.localStorage.getItem(VISITOR_STATE_KEY)) || createVisitorState(); }
    catch (_) { visitorState = createVisitorState(); }
    return visitorState;
  };
  const saveVisitorState = () => {
    if (!storageAvailable()) return;
    try { root.localStorage.setItem(VISITOR_STATE_KEY, JSON.stringify(visitorState)); }
    catch (_) { /* The in-memory workspace still works for this page load. */ }
  };
  const visitorRequest = async (path, options = {}) => {
    const result = await handle(path, options, loadVisitorState());
    if (String(options.method || "GET").toUpperCase() !== "GET") saveVisitorState();
    return result;
  };
  const account = async () => {
    if (!accountPromise) {
      accountPromise = (async () => {
        const response = await root.fetch("/api/auth/me");
        if (response.status === 401) return null;
        if (!response.ok) throw new Error(`Identity check failed (${response.status})`);
        return response.json();
      })();
    }
    return accountPromise;
  };
  const isLocal = async () => (await account()) === null;
  const rememberJob = job => {
    if (!job) return;
    const visitor = loadVisitorState();
    const index = visitor.jobs.findIndex(row => String(row.id) === String(job.id));
    if (index >= 0) visitor.jobs[index] = clone(job);
    else visitor.jobs.push(clone(job));
  };
  const applicationJobIds = () => loadVisitorState().applications
    .map(application => application.job_id)
    .filter(id => id != null);
  // Called once below for the shells that write their switch into the HTML.
  const syncToggle = () => {
    const toggle = root.document?.querySelector('[data-action="sample-toggle"]');
    if (!toggle) return;
    const active = isEnabled();
    toggle.classList.toggle("active", active);
    toggle.setAttribute("aria-checked", String(active));
    toggle.setAttribute("aria-label", `Turn ${active ? "off" : "on"} sample data`);
    const status = toggle.querySelector("small");
    if (status) status.textContent = active ? "On" : "Off";
  };

  if (typeof module !== "undefined" && module.exports) module.exports = { createState, createVisitorState, handle, PAGES };
  root.OneShotSampleData = { request, isEnabled, disableAll, sync: syncToggle };
  root.OneShotVisitorData = {
    account,
    isLocal,
    request: visitorRequest,
    rememberJob,
    applicationJobIds,
  };
  if (!root.document || !storageAvailable()) return;

  root.document.addEventListener("click", event => {
    const toggle = event.target.closest('[data-action="sample-toggle"]');
    // A switch on a page with no scope stores nothing, so reloading would only
    // look like a failure. Only the two scoped pages carry one today, so this is
    // a guard against a stray copy, not a live case.
    if (!toggle || !enabledKey()) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setEnabled(!isEnabled());
    root.location.reload();
  }, true);
  syncToggle();
})(typeof window !== "undefined" ? window : globalThis);
