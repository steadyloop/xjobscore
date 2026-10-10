# Skills revision retention audit

This historical audit evaluates every canonical entry in the former skills
revision draft for a Computer Science student applying broadly to US software and finance roles,
including quantitative development, with a longer-term focus on backend,
infrastructure, AI infrastructure, and platform engineering.

The audit is a recommendation record. It does not modify the taxonomy.
The unused draft dictionary was removed during repository cleanup on 2026-10-07;
`skills.yaml` remains the active taxonomy.

## Decision standard

A canonical skill should remain independently searchable when its presence can
reasonably change at least one of these actions:

- which resume evidence the applicant should emphasize;
- what technical material the applicant should prepare for an interview;
- whether the role belongs to a materially different technical track.

Frequency is evidence about current usage, not a substitute for judgment. A
rare protocol or systems technique can be highly valuable because one mention
strongly identifies the work. Conversely, a frequent editor, industry label,
or company-boilerplate term can add almost no useful information.

## Corpus evidence

The exact current revision was compiled and searched against the complete local
corpus rather than reusing the older benchmark:

| Measure | Result |
|---|---:|
| Job-description rows | 132,276 |
| Canonical skills audited | 1,046 |
| Canonicals with at least one match | 1,019 |
| Canonicals with zero matches | 27 |
| Alias ownership collisions | 0 |
| Broken display-group references | 0 |

The corpus is all-job evidence, not an internship-only sample. Counts can be
inflated by employer boilerplate, product links, and ordinary-language uses.
For example, `AI` matched 68,604 descriptions, `fintech` matched 8,480, GitHub
matched 2,660, and bare `futures` matched 818. Those figures do not mean each
term is a useful resume recommendation.

## Final disposition rule

Every canonical ID not named in a change table below has disposition **KEEP**.
This includes every low-frequency concrete language, library, framework,
datastore, protocol, file format, systems technique, mathematical subject, and
finance technique not explicitly rejected below. This is intentional: rare but
specific terms have much more diagnostic value than frequent generic terms.

## Remove

These do not justify an independent skill result for the stated use case.

| Canonical ID | Reason |
|---|---|
| `software-development` | Ubiquitous job-family wording; it adds no preparation detail. |
| `financial-technology` | Industry label rather than a learnable capability. |
| `mobile-computing` | Vague umbrella already expressed by mobile development and concrete platforms. |
| `processes` | Generic and safely unmatchable; operating-systems coverage remains. |
| `posix-internals` | Vague umbrella with no corpus evidence; concrete POSIX skills remain. |
| `model-privacy` | Corpus uses concrete privacy methods instead of this phrase. |
| `responsible-ai-evaluation` | Compositional duplicate of Responsible AI and model evaluation. |
| `embedded-processors` | Broad hardware class analogous to the already-rejected CPU/GPU keywords. |
| `sensors` | Too broad to identify a useful preparation or resume change. |
| `robotics-hardware` | Vague umbrella; concrete robotics and hardware entries remain. |
| `jira` | Workflow-product familiarity is not meaningful interview preparation or resume differentiation. |
| `confluence` | Documentation-product familiarity adds little beyond documentation/process methods. |
| `visual-studio-code` | General editor choice is not a meaningful job capability. |
| `intellij-idea` | General IDE choice is not a meaningful job capability. |
| `pycharm` | General IDE choice is not a meaningful job capability. |
| `eclipse` | General IDE choice is not a meaningful job capability. |
| `rstudio` | IDE usage adds little beyond R and the actual analytical stack. |
| `google-colab` | One corpus match and little independent preparation value. |
| `github-copilot` | Tool mention is weak resume evidence and usually workflow boilerplate. |
| `microsoft-word` | Generic office proficiency does not differentiate the covered roles. |
| `dbeaver` | Database GUI choice adds little beyond SQL and the underlying datastore. |
| `pgadmin` | Database GUI choice adds little beyond PostgreSQL. |
| `insomnia` | API-client choice adds little beyond API testing; Postman already supplies product recall. |
| `github` | Bare matches are heavily polluted by repository links; Git and GitHub Actions retain actionable coverage. |

Specialized development environments remain. Xcode and Android Studio identify
mobile work; Visual Studio identifies a Microsoft/.NET toolchain; Jupyter
identifies notebook-based data work.

## Merge or decompose

These phrases are useful, but they should not remain separate canonicals.
Aliases should move to the target canonical. For decompositions, the extractor
should emit the independently meaningful component skills.

| Current canonical(s) | Recommended target | Why |
|---|---|---|
| `jupyter-notebook`, `jupyterlab` | `jupyter` | Same notebook environment for resume/interview purposes. |
| `continuous-integration`, `continuous-delivery`, `continuous-deployment`, `ci-cd` | `ci-cd` | One delivery-automation capability; retain every spelling as an alias. |
| `data-cleaning`, `data-preprocessing`, `data-wrangling` | `data-preparation` | Substantially interchangeable job-description wording. Keep data transformation separate. |
| `numerical-analysis`, `numerical-methods` | `numerical-methods` | Same preparation signal in job descriptions. |
| `computer-organization`, `computer-architecture` | `computer-architecture` | Academic naming variation at this granularity. |
| `representational-state-transfer`, `restful-apis` | `rest` | REST and RESTful APIs should resolve to one capability. |
| `partitioning`, `data-partitioning` | `data-partitioning` | Generic and qualified forms describe the same data-systems technique. |
| `replication`, `data-replication` | `data-replication` | Same distributed-data technique. |
| `probability`, `probability-theory` | `probability` | No useful resume/interview distinction. |
| `cybersecurity`, `information-security` | `cybersecurity` | Near-synonymous umbrella terms at this taxonomy level. |
| `threads`, `multithreading` | `multithreading` | Concept and implementation wording for the same capability. |
| `concurrency`, `concurrent-programming` | `concurrency` | Concept and implementation wording for the same preparation area. |
| `parallel-computing`, `parallel-programming` | `parallel-computing` | Same preparation area; keep parallelism-related aliases. |
| `posix-sockets`, `socket-programming` | `socket-programming` | POSIX is a useful qualifier, not a separate resume skill here. |
| `factor-models`, `factor-modeling` | `factor-modeling` | Model noun and modeling activity represent one quant capability. |
| `security-operations-center`, `security-operations` | `security-operations` | SOC identifies the environment in which the same capability is practiced. |
| `data-quality-testing`, `data-validation` | `data-validation` | Data-quality testing is a form of data validation. |
| `machine-learning-model-development`, `deep-learning-model-development`, `computer-vision-model-development`, `generative-ai-development`, `large-language-model-development` | `model-development` plus the relevant domain concept | These are compositional phrases, not five independent skills. |
| `distributed-systems-engineering` | `distributed-systems` | The compound adds job wording, not new preparation content. |
| `application-security-testing` | `application-security` plus `security-testing` | The two component skills are independently useful. |
| `cloud-security-assessment` | `cloud-security` plus `risk-assessment` | The compound is completely represented by its components. |
| `market-data-analysis` | `market-data` plus `data-analysis` | The compound is completely represented by its components. |
| `authentication-implementation` | `authentication` | Implementation wording does not justify a separate canonical. |

The current production matcher uses leftmost-longest, non-overlapping output.
Before relying on decomposition, the revision matcher must be able to emit both
component skills from overlapping phrases such as `application security
testing`. Otherwise flattening compounds would silently reduce recall.

## Replace or repair

| Current canonical | Recommended change | Corpus evidence or rationale |
|---|---|---|
| `power-query-m` | Replace with a `power-query` tool/platform canonical; keep `Power Query M` as a specific alias. | Actual descriptions say `Power Query`; none said `Power Query M`. |
| `software-stress-testing` | Replace with `stress-testing`; retain software and financial contextual spellings. | The current over-specific phrase matched zero, while stress testing is useful across software and finance. |
| `lseg-workspace-refinitiv-workspace` | Rename to a neutral Refinitiv workspace/terminal canonical and add `Eikon`, `Reuters Eikon`, and `Refinitiv Eikon`. | Current aliases matched zero; valid Eikon evidence exists. |
| `swagger-ui` | Replace with `openapi`; retain `Swagger UI` only if product-level UI usage is wanted. | Swagger UI matched 1; safe OpenAPI aliases matched 164 descriptions. |
| `itch-ouch-protocols` | Split into `itch-protocol` and `ouch-protocol`, with a display group if desired. | ITCH is primarily market data and OUCH is primarily order entry; they are not aliases. |
| `triton-kernel-language` | Add `Triton kernel` and `Triton kernels`. | The existing long name matched zero, but multiple valid kernel-development descriptions exist. |
| `celery` | Add case-sensitive `Celery`; retain contextual aliases. | Bare capitalized uses were technical task-queue/framework references. |
| `apache-hive` | Add case-sensitive `Hive` and `Hive Metastore`. | Valid data-platform uses exist; `Apache Hive` itself matched zero. |
| `markov-chains` | Add singular `Markov chain`. | Safe grammatical variant; retain despite current low evidence. |
| `artificial-intelligence` | Move bare `AI` out of unconditional matching or mark it context-only. | It matched 68,604 descriptions, frequently from company/product boilerplate rather than role requirements. |
| `resilience` | Replace bare `resilience` with `operational resilience`, `system resilience`, `resilient systems`, and similar technical phrases. | Bare wording spans people, organizations, finance, and technical systems. |
| `futures` | Make bare `futures` ambiguous; match `futures trading`, `futures markets`, and `futures contracts`. | Bare hits include organization names and `concurrent futures`, not only financial instruments. |
| `requests` | Keep fail-closed until contextual matching exists. | Bare `requests` is overwhelmingly ordinary English. |
| `ninja` | Keep fail-closed until build-system context exists. | Bare hits include Ninja RMM and figurative job titles. |
| `poetry` | Add only with Python/package-manager context. | Valid corpus uses exist, but the ordinary word remains ambiguous. |
| `uv` | Add only with Python/package-manager context. | The product is lowercase and cannot be made safe with casing alone. |
| `cargo` | Add only with Rust/package-manager context. | Valid Rust uses coexist with logistics uses. |

## Category corrections

These items belong in the dictionary but their current top-level label is
misleading.

| Canonical(s) | Current | Recommended |
|---|---|---|
| `streamlit`, `gradio` | tool | framework |
| `apache-airflow` | tool | platform |
| `simulink`, `labview`, `maple` | platform | tool |
| `business-intelligence` | method | concept |
| `vectorized-query-execution` | concept | method |
| `kv-cache-optimization`, `dynamic-batching`, `continuous-batching`, `speculative-decoding`, `prefill-decode-disaggregation`, `pagedattention` | concept | method |
| `pci-express`, `nvlink`, `infiniband`, `roce` | hardware | protocol |

`NVSwitch` remains hardware. FPGA, ASIC, microcontrollers, tensor cores, HBM,
and the ARM/x86 architecture entries remain useful hardware signals.

## Material coverage additions

These candidates were added after alias-level marginal-gain and sample audits
against the same 132,276-row corpus. The counts are their final emitted
document frequencies in the combined dictionary.

| Candidate | Document frequency |
|---|---:|
| Vector databases | 903 |
| Apache Iceberg | 422 |
| Service mesh | 376 |
| Pulumi | 317 |
| API gateway | 311 |
| Delta Lake | 302 |
| Dagster | 287 |
| Istio | 216 |
| Trino | 178 |
| OpenAPI | 165 |
| pgvector | 94 |
| Apache Druid | 94 |
| Weaviate | 82 |
| NGINX | 71 |
| Dask | 61 |
| PyTorch Lightning | 59 |
| Temporal | 57 |
| Milvus | 44 |
| KServe | 40 |
| Ray Serve | 40 |
| Qdrant | 40 |
| Apache Hudi | 40 |
| FAISS | 29 |
| containerd | 28 |
| Envoy Proxy | 17 |
| BentoML | 12 |
| ChromaDB | 12 |

Low-count or conservative-filter candidates worth a second pass, but not yet a
blind addition, are Consul, NATS, Presto, Prefect, Seldon Core, Hugging Face
Accelerate, Pinecone, Jaeger, Grafana Loki, QuantLib, Bloomberg Query Language,
Refinitiv Eikon, and FIXP.

## Rare entries that should remain

Zero or very low frequency is not grounds to remove a concrete, role-defining
keyword. In particular, retain the niche systems, AI-infrastructure, quant,
scientific-computing, FPGA, protocol, and language entries unless they appear
in a remove/merge table above. Examples include PACELC, stationarity, pure
functions, wait-free programming, io_uring, epoll, memory-mapped I/O, SBE,
ITCH/OUCH after splitting, LoRA, chunked prefill, GARCH, ARIMA, CUDA/Triton
kernel work, Common Lisp, Scheme, Prolog, Chapel, numerical libraries, and
specialized datastores. A future posting containing one of these terms gives a
stronger preparation signal than thousands of generic `AI` or editor matches.

## Recommended execution order

1. Fix unsafe aliases before interpreting counts or exposing resume advice.
2. Apply removals, merges, decompositions, and category corrections.
3. Upgrade overlapping-compound extraction before deleting compound
   canonicals whose components overlap.
4. Add the corpus-supported backend and AI-infrastructure gaps.
5. Re-run the full corpus and sample every newly nonzero broad or ambiguous
   alias.
6. Maintain separate output intent for resume evidence, interview concepts,
   and contextual role/domain labels so a broad concept is never presented as
   a concrete resume skill.
