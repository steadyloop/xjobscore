# Skill keyword suggestions for US internships

This is a candidate vocabulary for extracting skill keywords from US internship
descriptions relevant to a Computer Science / Applied Mathematics student. It
covers software engineering, data engineering, data science, machine learning,
AI, infrastructure, security, scientific computing, operations research,
quantitative finance, risk, and finance analytics.

This is a search vocabulary, not a checklist for one resume. A candidate should
only claim a skill that they can support with coursework, a project, research,
or work experience. “All” below means a deliberately broad recall-oriented seed
set; it should still be expanded from unmatched phrases observed in the actual
posting corpus.

## Inclusion rules

A keyword belongs here when it:

- names a concrete, learnable capability rather than a personality trait;
- plausibly appears in a US internship description for at least one covered
  track;
- is useful for search, matching, gap analysis, or truthful resume tailoring;
- is not merely a job title, degree, credential, eligibility rule, or industry;
- is canonicalized once and reused across tracks rather than duplicated.

The six requested categories are used below:

- **Language:** a programming, scripting, query, markup, stylesheet, hardware
  description, or domain-specific language.
- **Library:** code imported, linked, or called by an application. Frameworks
  are not silently mislabeled as libraries.
- **Tool:** software a person directly operates to build, test, inspect,
  analyze, design, or manage work.
- **Platform:** an execution, deployment, compute, operating-system, managed
  service, or multi-product ecosystem.
- **Method:** an action, technique, workflow, or analysis someone performs.
- **Concept:** a theory, paradigm, subject, or body of technical/domain
  knowledge someone understands.

Current inventory: 920 proposed keywords across 77 languages, 88 libraries,
125 tools, 108 platforms, 236 methods, and 286 concepts, plus 99 candidates in
the adjacent framework, datastore, protocol, format, and hardware categories.

## Corpus-count snapshot

Counts were generated on 2026-08-21 from 2,197 active `job_posting` rows with a
non-empty description and `job_type` equal to `intern`, `coop`,
`student_program`, or `research`. Those rows contain 1,906 distinct description
texts. At snapshot time the live database did not yet expose the repository's
new `country` column, so this count snapshot covers student opportunities in
all available countries; the proposed vocabulary itself remains US-focused.

`(N)` is document frequency: the number of posting rows for which the
repository's `SkillMatcher.extract()` emitted that skill. Repetition inside one
description counts once, matching production enrichment behavior. All 1,019
main and adjacent candidates compile without alias collisions and are reachable
through at least one generated or curated alias. Of those candidates, 545 have
a nonzero corpus match and 474 have zero matches.

Ambiguous single words are deliberately not counted from an unsafe bare alias.
For example, `Make`, `Requests`, `Scheme`, `Spring`, `Processes`, and `Options`
use contextual aliases; `C`, `R`, `Go`, `q`, `Ray`, and `Spark` follow the same
fail-closed principle. Consequently, `(0)` means “no safe match in this corpus,”
not necessarily that the underlying technology never appears. These are
extractor-validation counts, not estimates of a candidate's importance and not
permission to add an unearned keyword to a resume.

Frameworks, datastores, protocols, formats, and hardware cannot be classified
accurately into these six types. They are listed at the end as adjacent keyword
types that the final taxonomy should add rather than force into a wrong bucket.

## Full-corpus alias additions

These safe aliases were found in the complete posting corpus and are applied to
the existing candidate for these counts rather than added as separate skills.
For candidates not yet in production `skills.yaml`, this table is the alias
specification to use when they are promoted.

| Existing candidate | Added aliases |
|---|---|
| Stata programming language | `Stata` |
| Visual Basic .NET | `VB.NET`, `VB Net`, `Visual Basic` |
| Beautiful Soup | `BeautifulSoup`, `bs4` |
| RAPIDS cuDF | `cuDF`, `NVIDIA RAPIDS`, `RAPIDS` |
| Google OR-Tools | `OR-Tools` |
| Sentence Transformers | `SentenceTransformers`, `sentence-transformers` |
| Intel oneMKL | `oneMKL`, `Intel MKL`, `MKL` |
| Git LFS | `git-lfs` |
| Google Cloud CLI | `gcloud`, `gcloud CLI`, `Google Cloud SDK` |
| Intel VTune Profiler | `VTune` |
| Apache TVM | `TVM` |
| Gurobi Optimizer | `Gurobi` |
| IBM ILOG CPLEX Optimization Studio | `CPLEX` |
| Azure Virtual Machines | `Azure VM`, `Azure VMs`, `Azure virtual machine` |
| Google Compute Engine | `Compute Engine`, `GCE` |
| Google Cloud Dataproc | `Dataproc` |
| Apache ActiveMQ | `ActiveMQ` |
| Precedent-transaction analysis | `precedent transactions`, `transaction comps` |
| Publish-subscribe architecture | `publish-subscribe`, `publish/subscribe`, `pub/sub`, `pubsub` |
| Vectorized query execution | `vectorized execution` |
| Martingales | `martingale` |
| Frequentist statistics | `frequentist`, `classical statistics` |
| Floating-point arithmetic | `floating-point`, `floating point` |
| Mixed-precision computing | `mixed precision`, `mixed-precision training` |
| Roofline model | `roofline` |
The production `Polars` entry was also corrected to accept bare `Polars`;
full-corpus samples consistently use it as the dataframe library.

## Language

### General software and systems

- C (2)
- C++ (394)
- C# (39)
- Java (134)
- Python (738)
- JavaScript (75)
- TypeScript (129)
- Go (14)
- Rust (71)
- Zig (2)
- SQL (145)
- PL/SQL (1)
- Transact-SQL (T-SQL) (0)
- Bash (54)
- PowerShell (17)
- Assembly language (1)
- x86 assembly (0)
- ARM assembly (0)
- Ada (6)

### Data, statistics, mathematics, and scientific computing

- R (0)
- MATLAB (72)
- Julia (5)
- Fortran (0)
- SAS (7)
- Stata programming language (0)
- AMPL (0)
- GAMS (0)
- MiniZinc (0)

### Web, mobile, and application development

- HTML (65)
- CSS (14)
- Sass / SCSS (3)
- PHP (5)
- Ruby (17)
- Swift (6)
- Objective-C (2)
- Kotlin (11)
- Dart (0)
- Cython (0)

### JVM, functional, and concurrent programming

- Scala (6)
- Clojure (0)
- Groovy (0)
- OCaml (18)
- Haskell (3)
- F# (1)
- Erlang (0)
- Elixir (0)
- Common Lisp (0)
- Scheme (0)
- Prolog (0)
- Chapel (0)

### Systems, accelerator, and hardware-description programming

- CUDA C++ (0)
- Triton Kernel Language (0)
- Verilog (47)
- SystemVerilog (23)
- VHDL (33)
- GLSL (0)
- HLSL (1)
- Metal Shading Language (0)
- WebGPU Shading Language (WGSL) (0)

### Data, infrastructure, and domain-specific languages

- GraphQL (11)
- Cypher (0)
- SPARQL (0)
- Kusto Query Language (KQL) (0)
- PromQL (0)
- DAX (3)
- Power Query M (0)
- HashiCorp Configuration Language (HCL) (0)
- Solidity (1)
- q (0)
- Tcl (7)
- Visual Basic .NET (5)
- Visual Basic for Applications (VBA) (16)
- ABAP (1)
- COBOL (1)
- Lua (5)
- Perl (29)
- LaTeX (1)

## Library

### Python data and scientific computing

- NumPy (24)
- pandas (30)
- SciPy (6)
- Polars (3)
- PyArrow (0)
- statsmodels (1)
- NetworkX (0)
- xarray (0)
- GeoPandas (0)
- Shapely (0)
- Requests (0)
- Beautiful Soup (0)
- SQLAlchemy (3)
- Pydantic (4)
- SimPy (0)
- Numba (0)
- CuPy (0)
- RAPIDS cuDF (0)

### Statistical learning and optimization

- scikit-learn (12)
- XGBoost (0)
- LightGBM (1)
- CatBoost (0)
- SHAP (0)
- CVXPY (0)
- Google OR-Tools (0)
- PyMC (0)

### Deep learning and AI

- PyTorch (102)
- torchvision (0)
- torchaudio (0)
- TensorFlow (54)
- Keras (2)
- JAX (42)
- Flax (0)
- Hugging Face Transformers (0)
- Sentence Transformers (0)
- spaCy (0)
- NLTK (0)
- OpenCV (7)
- Pillow (0)
- ONNX Runtime (2)
- TensorRT (2)
- TensorRT-LLM (0)
- vLLM (3)
- SGLang (3)
- FlashAttention (2)
- FasterTransformer (0)
- llama.cpp (0)
- PyTorch Distributed (2)
- Apple MLX (0)

### Visualization

- Matplotlib (1)
- Seaborn (2)
- Plotly (0)
- Altair (0)
- Bokeh (0)
- D3.js (0)

### C and C++

- C++ Standard Library (0)
- Boost (18)
- Eigen (3)
- BLAS (0)
- LAPACK (0)
- Intel oneMKL (0)
- GNU Scientific Library (GSL) (0)
- FFTW (0)
- PETSc (0)
- Trilinos (0)
- GoogleTest (0)
- Google Benchmark (0)
- Catch2 (0)

### GPU and high-performance computing

- cuBLAS (0)
- cuDNN (0)
- NCCL (1)
- CUTLASS (0)
- CUDA Thrust (0)
- OpenMP (0)
- MPI (0)
- DPDK (1)
- Solarflare OpenOnload (0)

### Java, JavaScript, and general application libraries

- React (124)
- Redux (29)
- RxJS (0)
- jQuery (0)
- Lodash (1)
- Axios (0)
- Three.js (1)
- JUnit (0)
- Mockito (0)

### Go, Rust, and serialization libraries

- Tokio (1)
- ZeroMQ (0)

## Tool

### Source control and collaboration

- Git (63)
- Git LFS (0)
- Jira (32)
- Confluence (11)

### Editors, IDEs, notebooks, and research authoring

- Visual Studio Code (0)
- Visual Studio (1)
- IntelliJ IDEA (2)
- PyCharm (2)
- Eclipse (0)
- Xcode (0)
- Android Studio (0)
- RStudio (0)
- Jupyter Notebook (0)
- JupyterLab (0)
- Google Colab (0)
- Figma (20)
- GitHub Copilot (1)

### Compilers, build systems, and package management

- GCC (2)
- Clang (0)
- LLVM (1)
- Microsoft Visual C++ (MSVC) (0)
- Make (1)
- CMake (1)
- Ninja (0)
- Bazel (3)
- Maven (2)
- Gradle (25)
- npm (2)
- Yarn (0)
- pnpm (0)
- pip (2)
- Conda (1)
- Poetry (0)
- uv (0)
- Cargo (0)
- NuGet (0)
- AWS CLI (0)
- Azure CLI (0)
- Google Cloud CLI (0)
- kubectl (0)

### Containers, infrastructure, and delivery

- Docker (32)
- Docker Compose (0)
- Terraform (15)
- Ansible (11)
- Helm (3)
- Packer (1)
- Vagrant (0)
- Jenkins (6)
- GitHub Actions (9)
- GitLab CI/CD (1)
- CircleCI (4)
- Travis CI (0)
- Argo CD (0)

### Testing and API development

- pytest (5)
- Selenium (1)
- Cypress (1)
- Playwright (1)
- Cucumber (0)
- Postman (0)
- Insomnia (0)
- Swagger UI (0)
- Apache JMeter (0)
- k6 (0)
- Locust (0)

### Debugging, profiling, and systems inspection

- gdb (1)
- lldb (0)
- Valgrind (1)
- AddressSanitizer (1)
- ThreadSanitizer (0)
- clang-tidy (0)
- Linux perf (0)
- Nsight Systems (0)
- Nsight Compute (0)
- Intel VTune Profiler (3)
- strace (0)
- ftrace (0)
- FlameGraph (0)
- Wireshark (2)
- tcpdump (0)
- PyTorch Inductor (0)
- TorchScript (0)
- torch.compile (0)
- Apache TVM (1)
- MLIR (2)
- XLA (2)

### Data analysis, visualization, and business intelligence

- Microsoft Excel (23)
- Microsoft PowerPoint (0)
- Microsoft Word (16)
- Microsoft Access (0)
- Power BI (44)
- Tableau (17)
- Looker (3)
- Qlik Sense (0)
- Alteryx (0)
- dbt (4)
- DBeaver (0)
- pgAdmin (0)
- SQL Server Management Studio (SSMS) (7)
- Gurobi Optimizer (0)
- IBM ILOG CPLEX Optimization Studio (0)

### Data, ML, and experiment workflows

- Apache Airflow (1)
- MLflow (2)
- Weights & Biases (2)
- TensorBoard (0)
- Data Version Control (DVC) (0)
- Label Studio (0)
- Streamlit (2)
- Gradio (0)

### Security

- Nmap (0)
- Burp Suite (0)
- Metasploit (0)
- Nessus (0)
- Tenable (0)
- HashiCorp Vault (0)
- OpenSSL (0)
- SonarQube (1)
- Snyk (0)
- Trivy (0)

### Finance and market data

- Bloomberg Terminal (0)
- S&P Capital IQ (0)
- FactSet (0)
- LSEG Workspace / Refinitiv Workspace (0)
- PitchBook (0)
- Morningstar Direct (0)
- QuickBooks (1)

## Platform

### Operating systems and device platforms

- Linux (173)
- Unix (38)
- Windows (46)
- macOS (10)
- iOS (4)
- Android (10)
- FreeRTOS (1)
- QNX (0)
- VxWorks (0)

### Cloud platforms

- Amazon Web Services (AWS) (83)
- Microsoft Azure (0)
- Google Cloud Platform (GCP) (44)
- Oracle Cloud Infrastructure (OCI) (0)
- IBM Cloud (0)
- OpenStack (0)
- Cloudflare (26)

### Major AWS services

- Amazon EC2 (0)
- AWS Lambda (0)
- Amazon S3 (0)
- Amazon ECS (0)
- Amazon EKS (0)
- AWS Fargate (0)
- Amazon SageMaker (0)
- Amazon Bedrock (0)
- Amazon EMR (0)
- AWS Glue (0)
- Amazon Kinesis (0)

### Major Azure services

- Azure Virtual Machines (0)
- Azure Functions (0)
- Azure App Service (0)
- Azure Kubernetes Service (AKS) (4)
- Azure Machine Learning (0)
- Azure Databricks (1)
- Azure Synapse Analytics (0)

### Major Google Cloud services

- Google Compute Engine (0)
- Google Cloud Run (0)
- Google Kubernetes Engine (GKE) (1)
- Vertex AI (0)
- Google Cloud Dataflow (0)
- Google Cloud Dataproc (0)

### Containers, orchestration, and source hosting

- Kubernetes (47)
- Red Hat OpenShift (0)
- HashiCorp Nomad (0)
- VMware vSphere (0)
- GitHub (72)
- GitLab (11)
- Bitbucket (2)

### Language runtimes and application platforms

- Java Virtual Machine (JVM) (0)
- .NET (5)
- Node.js (35)
- Deno (0)
- Bun (0)
- WebAssembly (0)
- eBPF (1)
- CUDA (20)
- ROCm (10)
- OpenCL (3)
- Firebase (2)
- Heroku (0)
- Vercel (14)
- Netlify (0)

### Distributed data and compute platforms

- Apache Spark (0)
- Hadoop (4)
- Hadoop Distributed File System (HDFS) (0)
- Apache Flink (8)
- Apache Kafka (9)
- Apache Pulsar (0)
- Databricks (16)
- Snowflake (30)
- Apache Hive (0)
- Apache HBase (0)
- Amazon Redshift (0)
- Google BigQuery (1)
- Teradata (0)
- Cloudera (0)
- Palantir Foundry (14)

### Messaging, workflow, and distributed execution

- RabbitMQ (5)
- Apache ActiveMQ (0)
- Redpanda (1)
- Redis Streams (0)
- Slurm (0)

### ML, AI, and model-serving platforms

- Hugging Face Hub (0)
- Kubeflow (0)
- NVIDIA Triton Inference Server (0)
- TensorFlow Serving (0)
- TorchServe (0)
- Hugging Face Text Generation Inference (TGI) (0)
- OpenAI API (1)
- Azure OpenAI Service (0)
- Anthropic API (0)
- Google Gemini API (0)

### Observability and operations platforms

- Datadog (12)
- New Relic (0)
- Grafana (16)
- Prometheus (1)
- OpenTelemetry (2)
- Elastic Stack (ELK) (0)
- Splunk (4)

### Enterprise, simulation, and robotics platforms

- Salesforce (41)
- SAP (21)
- ServiceNow (4)
- Simulink (5)
- LabVIEW (4)
- Maple (0)
- Robot Operating System (ROS) (0)
- ROS 2 (3)
- Unity (6)
- Unreal Engine (8)

## Method

### Software engineering and product development

- Software development (149)
- Front-end development (5)
- Back-end development (0)
- Full-stack development (14)
- Mobile application development (0)
- Systems programming (23)
- Embedded software development (6)
- Firmware development (20)
- API development (2)
- API design (3)
- Object-oriented design (13)
- Software architecture (4)
- System design (37)
- Database design (8)
- Data modeling (12)
- Requirements analysis (0)
- Technical documentation (46)
- Code review (12)
- Refactoring (4)
- Debugging (149)
- Troubleshooting (113)
- Root-cause analysis (22)
- Performance profiling (7)
- Performance optimization (23)
- Memory optimization (0)
- Algorithm design (5)
- Complexity analysis (1)

### Testing and software quality

- Unit testing (9)
- Integration testing (1)
- End-to-end testing (0)
- Regression testing (2)
- Acceptance testing (5)
- Test automation (13)
- Test-driven development (TDD) (5)
- Behavior-driven development (BDD) (0)
- Property-based testing (1)
- Fuzz testing (0)
- Load testing (0)
- Software stress testing (0)
- Performance testing (4)
- Static analysis (0)
- Dynamic analysis (0)

### Delivery, infrastructure, and reliability

- Version control (45)
- Continuous integration (15)
- Continuous delivery (1)
- Continuous deployment (1)
- CI/CD (58)
- Agile software development (1)
- Scrum (12)
- Kanban (4)
- Infrastructure as code (5)
- Containerization (4)
- Cloud deployment (0)
- Configuration management (12)
- Release engineering (0)
- Build automation (10)
- Observability (53)
- Monitoring (164)
- Logging (15)
- Distributed tracing (0)
- Capacity planning (2)
- Fault injection (0)
- Chaos engineering (0)
- Benchmarking (27)
- Microbenchmarking (0)

### Data engineering and analytics

- Data collection (30)
- Data analysis (159)
- Data ingestion (13)
- Data integration (3)
- Data cleaning (5)
- Data cleansing (1)
- Data preprocessing (1)
- Data transformation (3)
- Data wrangling (1)
- Exploratory data analysis (EDA) (11)
- Data validation (2)
- Data quality testing (0)
- Data visualization (32)
- Dashboard development (3)
- Business intelligence (9)
- ETL (16)
- ELT (3)
- Batch processing (2)
- Stream processing (5)
- Data pipeline development (0)
- Feature engineering (11)
- Feature extraction (5)
- Feature selection (0)
- Dimensionality reduction (1)
- Data mining (31)
- Geospatial analysis (1)
- Web scraping (3)

### Statistics, experimentation, and applied mathematics

- Statistical analysis (66)
- Statistical modeling (14)
- Hypothesis testing (2)
- Experimental design (10)
- A/B testing (9)
- Regression analysis (0)
- Classification (23)
- Clustering (4)
- Time-series analysis (21)
- Forecasting (33)
- Survival analysis (1)
- Econometric modeling (0)
- Anomaly detection (11)
- Causal inference (3)
- Bayesian inference (0)
- Monte Carlo simulation (4)
- Markov chain Monte Carlo (MCMC) (0)
- Stochastic simulation (0)
- Numerical analysis (3)
- Numerical optimization (0)
- Convex optimization (1)
- Discrete optimization (0)
- Combinatorial optimization (0)
- Linear programming (0)
- Integer programming (0)
- Mixed-integer programming (0)
- Dynamic programming (0)
- Constraint programming (0)
- Simulation modeling (0)
- Sensitivity analysis (0)
- Scenario analysis (1)
- Uncertainty quantification (0)
- Decision analysis (0)
- Signal processing (11)
- Image processing (8)
- Finite-difference methods (0)
- Finite-element methods (0)

### Machine learning and AI

- Machine learning model development (2)
- Model training (31)
- Model evaluation (5)
- Model validation (1)
- Model calibration (0)
- Cross-validation (0)
- Hyperparameter optimization (0)
- Predictive modeling (13)
- Deep-learning model development (0)
- Computer-vision model development (0)
- Generative AI development (0)
- Large language model development (0)
- Prompt engineering (11)
- Retrieval-augmented generation (RAG) (16)
- Fine-tuning (25)
- Parameter-efficient fine-tuning (PEFT) (3)
- Ranking (9)
- MLOps (11)
- Model deployment (5)
- Model monitoring (0)
- Distributed training (18)
- Distributed inference (2)
- Inference optimization (0)
- Quantization (7)
- Model pruning (0)
- Knowledge distillation (1)
- Model compression (1)
- Model explainability (0)
- Responsible AI evaluation (0)

### Systems, networking, and performance engineering

- Concurrent programming (1)
- Parallel programming (5)
- Multithreading (6)
- Multiprocessing (1)
- Asynchronous programming (1)
- GPU programming (8)
- Vectorization (0)
- SIMD optimization (0)
- Kernel optimization (0)
- Cache optimization (0)
- Lock-free programming (1)
- Network programming (11)
- Socket programming (1)
- Protocol implementation (0)
- Binary protocol parsing (0)
- Serialization (1)
- Distributed-systems engineering (0)
- Low-latency engineering (75)
- Real-time systems development (0)
- Compiler development (0)
- Compiler optimization (0)
- Formal verification (8)
- Clock synchronization (0)

### Cybersecurity

- Secure coding (1)
- Security testing (0)
- Threat modeling (2)
- Vulnerability assessment (0)
- Vulnerability management (5)
- Penetration testing (2)
- Security monitoring (1)
- Log analysis (1)
- Security operations (12)
- Incident response (20)
- Malware analysis (1)
- Digital forensics (1)
- Reverse engineering (0)
- Network traffic analysis (0)
- Authentication implementation (0)
- Cryptographic implementation (0)
- Application security testing (0)
- Cloud security assessment (0)
- Risk assessment (1)

### Quantitative finance, risk, and financial analytics

- Quantitative modeling (28)
- Financial modeling (7)
- Mathematical modeling (6)
- Market-data analysis (3)
- Algorithmic trading (53)
- Strategy research (0)
- Strategy implementation (4)
- Backtesting (8)
- Trade execution analysis (0)
- Transaction-cost analysis (1)
- Portfolio optimization (2)
- Asset allocation (6)
- Risk modeling (1)
- Factor modeling (1)
- Derivatives pricing (4)
- Option pricing (1)
- Fixed-income modeling (0)
- Credit-risk modeling (0)
- Market-risk modeling (0)
- Value at Risk (VaR) (0)
- Financial-statement analysis (2)
- Equity research (7)
- Fundamental analysis (0)
- Credit analysis (3)
- Underwriting analysis (0)
- Valuation (28)
- Discounted cash-flow analysis (DCF) (0)
- Comparable-company analysis (0)
- Precedent-transaction analysis (0)
- Leveraged-buyout modeling (LBO) (0)
- Budgeting (13)
- Financial forecasting (0)
- Variance analysis (4)
- Cash-flow analysis (0)
- Due diligence (18)
- Actuarial modeling (0)

## Concept

### Core computer science

- Data structures (97)
- Algorithms (234)
- Computational complexity (0)
- Object-oriented programming (OOP) (28)
- Functional programming (9)
- Programming-language theory (0)
- Type systems (2)
- Design patterns (3)
- Software engineering (224)
- Software development lifecycle (SDLC) (15)
- Operating systems (83)
- Computer architecture (40)
- Computer organization (4)
- Computer networks (1)
- Database systems (9)
- Compilers (30)
- Distributed systems (58)
- Parallel computing (4)
- Cloud computing (20)
- Edge computing (1)
- Mobile computing (0)
- Embedded systems (46)
- Real-time systems (13)
- High-performance computing (HPC) (36)
- Human-computer interaction (HCI) (7)
- Computer graphics (8)
- Scientific computing (2)
- Data science (124)
- Data engineering (31)
- Big data (17)
- Formal methods (6)
- Computational geometry (0)
- Robotics (179)
- Autonomous systems (69)
- Quantum computing (0)
- Bioinformatics (1)
- Computational biology (1)

### Software and distributed architecture

- Client-server architecture (1)
- Service-oriented architecture (1)
- Microservices architecture (16)
- Event-driven architecture (7)
- Layered architecture (0)
- Model-view-controller (MVC) (1)
- Representational state transfer (REST) (9)
- RESTful APIs (10)
- Remote procedure calls (RPC) (2)
- Publish-subscribe architecture (2)
- Message queues (2)
- Event sourcing (0)
- Command Query Responsibility Segregation (CQRS) (0)
- Distributed consensus (4)
- CAP theorem (0)
- Consistency models (2)
- Eventual consistency (0)
- Distributed transactions (0)
- Distributed locking (0)
- Fault tolerance (1)
- High availability (3)
- Scalability (64)
- Resilience (24)
- Load balancing (0)
- Partitioning (0)
- Sharding (0)
- Replication (0)
- Caching (11)
- Consistent hashing (0)
- Rate limiting (0)
- Backpressure (0)
- Gossip protocols (0)
- Idempotency (0)
- Disaster recovery (7)

### Databases and data systems

- Relational databases (10)
- NoSQL databases (1)
- Document databases (0)
- Key-value stores (0)
- Graph databases (0)
- Time-series databases (0)
- In-memory databases (0)
- Data warehouses (2)
- Data lakes (0)
- Lakehouse architecture (0)
- Data marts (0)
- Object storage (1)
- Online transaction processing (OLTP) (0)
- Online analytical processing (OLAP) (0)
- ACID transactions (0)
- Transaction isolation (0)
- Multiversion concurrency control (MVCC) (0)
- Indexing (3)
- Query optimization (0)
- Columnar storage (0)
- Vectorized query execution (0)
- Write-ahead logging (0)
- Star schema (0)
- Snowflake schema (0)
- Data partitioning (0)
- Data replication (0)
- Data governance (7)
- Data lineage (0)
- Data quality (19)
- Master data management (0)
- Structured data (10)
- Semi-structured data (0)
- Unstructured data (12)

### Mathematics and statistics

- Calculus (1)
- Multivariable calculus (1)
- Linear algebra (9)
- Numerical linear algebra (0)
- Differential equations (1)
- Ordinary differential equations (ODEs) (0)
- Partial differential equations (PDEs) (1)
- Discrete mathematics (0)
- Graph theory (0)
- Combinatorics (0)
- Probability (29)
- Probability theory (16)
- Statistics (181)
- Mathematical statistics (0)
- Stochastic processes (0)
- Stochastic calculus (3)
- Markov chains (0)
- Martingales (0)
- Bayesian statistics (0)
- Frequentist statistics (1)
- Statistical inference (0)
- Optimization theory (0)
- Convex analysis (0)
- Operations research (32)
- Game theory (18)
- Information theory (0)
- Coding theory (0)
- Queueing theory (0)
- Dynamical systems (1)
- Control theory (5)
- Numerical methods (8)

### Machine learning, AI, and data science

- Artificial intelligence (AI) (957)
- Machine learning (ML) (418)
- Supervised learning (1)
- Unsupervised learning (0)
- Semi-supervised learning (0)
- Self-supervised learning (2)
- Reinforcement learning (43)
- Deep learning (121)
- Neural networks (9)
- Transformer architecture (7)
- Large language models (LLMs) (151)
- Generative AI (27)
- Natural language processing (NLP) (37)
- Computer vision (68)
- Speech recognition (3)
- Recommender systems (5)
- Information retrieval (7)
- Ranking systems (0)
- Representation learning (3)
- Transfer learning (0)
- Ensemble learning (0)
- Gradient boosting (0)
- Decision trees (2)
- Random forests (1)
- Support vector machines (0)
- Linear models (15)
- Generalized linear models (0)
- Time-series models (0)
- Explainable AI (1)
- Responsible AI (40)
- AI safety (1)
- Model fairness (0)
- Model privacy (0)
- Feature stores (0)
- Autoregressive decoding (0)
- KV-cache optimization (1)
- Dynamic batching (0)
- Continuous batching (2)
- Speculative decoding (2)
- Prefill-decode disaggregation (0)
- PagedAttention (0)

### Systems and performance

- Memory management (5)
- Virtual memory (7)
- Processes (0)
- Threads (8)
- Concurrency (11)
- Synchronization (0)
- Race conditions (0)
- Deadlocks (0)
- Atomics (0)
- Mutexes (0)
- Semaphores (0)
- Condition variables (0)
- Coroutines (1)
- POSIX threads (0)
- Inter-process communication (IPC) (4)
- Asynchronous I/O (0)
- Non-blocking I/O (0)
- CPU cache locality (0)
- NUMA (0)
- SIMD (2)
- Zero-copy architecture (0)
- Kernel bypass (2)
- POSIX sockets (0)
- UDP multicast (0)
- GPU architecture (6)
- GPU memory hierarchy (0)
- Memory coalescing (0)
- Memory bandwidth (0)
- GPU virtualization (0)
- Floating-point arithmetic (0)
- Mixed-precision computing (0)
- Roofline model (0)
- Low-latency systems (9)
- Real-time scheduling (0)

### Security

- Cybersecurity (42)
- Information security (38)
- Application security (3)
- Network security (9)
- Cloud security (9)
- Data security (40)
- Systems security (2)
- Software supply-chain security (0)
- Zero-trust architecture (0)
- Identity and access management (IAM) (11)
- Authentication (14)
- Authorization (0)
- Cryptography (12)
- Public-key infrastructure (PKI) (3)
- Encryption (6)
- Hashing (0)
- Digital signatures (0)
- Secure software development lifecycle (SSDLC) (0)
- Security operations center (SOC) (24)
- Security information and event management (SIEM) (5)
- Threat intelligence (10)
- Incident management (1)
- Privacy engineering (1)
- Data privacy (44)

### Quantitative finance, markets, and risk

- Quantitative finance (31)
- Financial mathematics (0)
- Financial engineering (12)
- Econometrics (11)
- Economics (159)
- Microeconomics (0)
- Macroeconomics (1)
- Corporate finance (8)
- Accounting (80)
- Financial statements (9)
- Capital markets (11)
- Equity markets (1)
- Fixed-income markets (3)
- Foreign-exchange markets (0)
- Commodities markets (6)
- Derivatives (26)
- Options (13)
- Futures (19)
- Swaps (3)
- Market microstructure (10)
- Order books (2)
- Market data (70)
- Electronic trading (24)
- High-frequency trading (10)
- Quantitative research (92)
- Portfolio theory (0)
- Asset pricing (0)
- Factor models (0)
- Arbitrage (2)
- Risk management (76)
- Market risk (2)
- Credit risk (3)
- Liquidity risk (0)
- Operational risk (12)
- Model risk (1)
- Counterparty risk (0)
- Volatility (7)
- Yield curves (0)
- Term structure (0)
- Discounted cash flow (1)
- Actuarial science (4)
- Financial technology (fintech) (55)
- Computational finance (0)
- Blockchain (18)
- Distributed ledger technology (0)

### Engineering processes and operations

- DevOps (23)
- Site reliability engineering (SRE) (4)
- Reliability engineering (5)
- Quality assurance (22)
- Technical debt (0)
- Open-source software (15)
- POSIX internals (10)

## Adjacent categories required for complete recall

These are applicable internship keywords, but assigning them to one of the six
requested categories would be inaccurate. They should become first-class
categories if the production taxonomy is intended to capture the full market.

### Framework candidates

- Angular (5)
- Vue.js (4)
- Next.js (23)
- Nuxt (1)
- Express.js (0)
- NestJS (1)
- Django (8)
- Flask (0)
- FastAPI (10)
- Ruby on Rails (1)
- Spring (0)
- Spring Boot (6)
- ASP.NET Core (0)
- Laravel (0)
- Symfony (0)
- React Native (8)
- Flutter (3)
- SwiftUI (1)
- UIKit (0)
- Qt (0)
- .NET MAUI (0)
- Apache Beam (0)
- Celery (0)
- Ray (0)
- LangChain (10)
- LlamaIndex (2)
- Hadoop MapReduce (0)
- Megatron-LM (0)
- DeepSpeed (2)
- Horovod (0)

### Datastore candidates

- PostgreSQL (39)
- MySQL (16)
- MariaDB (0)
- SQLite (0)
- Microsoft SQL Server (6)
- Oracle Database (0)
- IBM Db2 (0)
- MongoDB (6)
- Redis (7)
- Apache Cassandra (0)
- Amazon DynamoDB (0)
- Elasticsearch (23)
- OpenSearch (1)
- Neo4j (0)
- DuckDB (1)
- ClickHouse (2)
- CockroachDB (0)
- InfluxDB (1)
- TimescaleDB (1)
- RocksDB (0)
- LMDB (0)

### Protocol candidates

- HTTP / HTTPS (182)
- TCP/IP (27)
- UDP (3)
- WebSockets (6)
- gRPC (4)
- OAuth 2.0 (0)
- OpenID Connect (OIDC) (0)
- SAML (2)
- SSH (0)
- TLS / SSL (13)
- DNS (23)
- DHCP (5)
- SMTP (0)
- MQTT (0)
- AMQP (0)
- FIX Protocol (0)
- ITCH/OUCH Protocols (0)
- Simple Binary Encoding (SBE) (0)
- RDMA (1)

### Format and schema candidates

- JSON (3)
- XML (5)
- YAML (0)
- CSV (1)
- Apache Parquet (1)
- Apache Arrow (0)
- Apache ORC (0)
- Apache Avro (0)
- Protocol Buffers (0)
- FlatBuffers (0)
- ONNX (1)
- GGUF (0)
- HDF5 (0)
- NetCDF (0)

### Hardware candidates

- FPGA (54)
- ASIC (37)
- Microcontrollers (18)
- Embedded processors (2)
- ARM architecture (0)
- x86 architecture (2)
- Tensor Cores (0)
- High Bandwidth Memory (HBM) (0)
- PCI Express (PCIe) (20)
- NVLink (5)
- NVSwitch (0)
- InfiniBand (2)
- RoCE (0)
- Sensors (64)
- Robotics hardware (2)

## Full-corpus audit follow-ups

### Ambiguous or context-dependent filters

These should not receive an unconditional bare alias without a contextual
matching rule:

- Scheme — `scheme` is overwhelmingly ordinary prose.
- Metal Shading Language — `MSL` collides with unrelated organizations/metrics.
- Requests — `requests` is overwhelmingly ordinary prose.
- CUDA Thrust — `thrust` is mostly aerospace language.
- Ninja — bare occurrences are Ninja RMM or figurative titles, not the build tool.
- Cargo — occurrences mix Rust tooling with shipping/logistics.
- Maple — bare hits are unrelated entities/protocols.
- Processes — the ordinary word occurs in 41,621 postings.
- Poetry — sampled bare hits are the Python tool, but the name remains ambiguous.
- uv — lowercase hits are the Python tool; uppercase UV has unrelated meanings.
- Apache Hive — bare Hive is often technical but still needs data-platform context.
- Celery — sampled hits are the Python queue, but a context rule is safer than a
  universal case-insensitive alias.
- ITCH/OUCH Protocols — uppercase `ITCH` and `OUCH` are valid in 21 postings,
  but the current canon cannot make only selected aliases case-sensitive.
- Apache ORC — the production ORC entry already accepts `ORC format`, but bare
  uppercase `ORC` needs per-alias case sensitivity to avoid lowercase `orc`.

### Wrong granularity requiring a taxonomy decision

These are distinct from the existing broader keyword, so they were not dropped:

- Common Lisp — `Lisp` evidence does not prove Common Lisp.
- Power Query M — Power Query usage does not prove use of the M language.
- Google Gemini API — Google Gemini usage does not necessarily imply API usage.
- Software stress testing — generic stress testing spans software, hardware, and
  finance.
- Responsible AI evaluation — narrower than the existing Responsible AI concept.
- Markov chains — related to, but not synonymous with, MCMC.
- Convex analysis — related to, but not synonymous with, convex optimization.
- Autoregressive decoding — neither `autoregressive` nor `decoding` alone proves
  the compound skill.
- POSIX sockets — narrower than general socket programming.

### Wrong keyword requiring a taxonomy decision

- LSEG Workspace / Refinitiv Workspace — corpus evidence is for predecessor
  product Eikon, which should be a separate candidate.
- Trade execution analysis — postings use `trade execution`; decide whether to
  rename this candidate or preserve the narrower analysis meaning.
- Model privacy — postings use concrete terms such as differential privacy and
  privacy-preserving machine learning.

## Matching notes

- Preserve one canonical entry per skill and store alternate spellings as
  aliases; do not duplicate a skill by track.
- Prefer exact phrase and token-boundary matching over substring matching.
- Require contextual matching for ambiguous aliases such as `C`, `R`, `Go`,
  `ML`, `AI`, `REST`, `Spark`, `Ray`, `Excel`, `SAS`, `PE`, and `IR`.
- Keep related but distinct skills separate: Java is not JavaScript; React is
  not JavaScript; PostgreSQL is not SQL; XGBoost is not gradient boosting;
  financial modeling is not Microsoft Excel.
- Record the matched evidence span and posting section. Weight required and
  preferred qualifications more heavily than responsibilities, team-stack
  descriptions, training outcomes, and employer boilerplate.
- Mine unmatched capitalized tokens, noun phrases, and comma-separated
  qualification lists from the posting corpus, then manually review candidates.
- Track frequency, document frequency, first seen, last seen, and covered role
  tracks outside the canonical skill definition.

## Representative current US internship evidence

Accessed 2026-08-21:

- [Amazon Software Development Engineer Internship](https://www.amazon.jobs/en/jobs/3116030/software-development-engineer-internship-summer-2026-us)
  names general-purpose languages, data structures, algorithms, object-oriented
  design, cloud platforms, databases, version control, debugging, SDLC, CI/CD,
  monitoring, and distributed systems.
- [Apple Software Undergrad Engineering Internships](https://jobs.apple.com/en-us/details/200664785-3810/software-undergrad-engineering-internships)
  spans back-end, web, mobile, operating systems, data, distributed computing,
  embedded systems, Java, C/C++, PHP, Python, Ruby, Scala, JavaScript, HTML/CSS,
  Swift, Objective-C, SQL databases, Spark, Hadoop, and API design.
- [Palantir Software Engineer Internship](https://jobs.lever.co/palantir/7d69cf8a-06fd-4f05-bd84-27149db29c4d)
  targets CS and mathematics students and names Java, Go, TypeScript, Python,
  C++, JavaScript, Cassandra, Spark, Elasticsearch, React, Redux, Gradle,
  GitHub, storage systems, cloud infrastructure, and data structures.
- [JPMorgan Chase Software Engineer Internship](https://careers.jpmorgan.com/us/en/students/programs/software-engineer-summer)
  emphasizes Python, JavaScript, Java, C++, C#, databases, data structures,
  algorithms, SDLC, Agile, CI/CD, resiliency, security, cloud, AI/ML, and mobile.
- [Bank of America Quantitative Data Analytics Summer Analyst](https://careers.bankofamerica.com/en-us/students/job-detail/14420/quantitative-data-analyst-summer-analyst-program-multiple-locations)
  explicitly targets math, statistics, economics, data science, CS, and
  financial engineering students and names Python, SQL, R, C++, MATLAB, Java,
  Tableau, Power BI, Hadoop, data cleaning, modeling, and risk analysis.
- [Jane Street Quantitative Research Internship](https://www.janestreet.com/join-jane-street/closed-internship/quantitative-researcher-may-august-nyc/)
  emphasizes programming, OCaml, noisy-data analysis, model building, signal
  discovery, strategy research, markets, and trading.
- [Jane Street Data Engineer Internship](https://www.janestreet.com/join-jane-street/position/8631973002/)
  names Python, SQL, OCaml, structured and unstructured data, dataset
  investigation, market data, and data engineering.
