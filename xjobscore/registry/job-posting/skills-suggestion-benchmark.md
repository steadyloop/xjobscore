# Full-corpus skill keyword dictionary

This is the complete candidate dictionary from `skills-suggestion.md`,
counted against every non-empty job description in the current
`job_posting` corpus. No student, job-type, status, country, or geography
filter was applied.

`(N)` is document frequency: the number of posting rows for which
`SkillMatcher.extract()` emitted that canonical skill. Repeated mentions
inside one description count once. Counts use the same generated and
curated safe-alias rules as `skills-suggestion.md`; ambiguous bare aliases
remain fail-closed.

## Summary statistics

| Measure | Value |
|---|---:|
| Posting rows searched | 132,276 |
| Distinct description texts | 103,637 |
| Source characters searched | 631,739,931 |
| Canonical skill candidates | 1,019 |
| Search aliases | 1,821 |
| Candidates with at least one match | 993 |
| Candidates with zero matches | 26 |
| Descriptions with at least one match | 109,210 |
| Emitted document-skill IDs | 719,302 |
| Full scan wall time | 140.42 seconds |
| Throughput | 4.50 million characters/second |

Category totals below sum document frequencies; one posting can contribute
to many skills and categories.

| Category | Candidates | Nonzero | Document-skill IDs |
|---|---:|---:|---:|
| Language | 77 | 73 | 79,397 |
| Library | 88 | 86 | 16,492 |
| Tool | 125 | 120 | 37,596 |
| Platform | 108 | 105 | 87,030 |
| Method | 236 | 233 | 165,135 |
| Concept | 286 | 280 | 287,578 |
| Framework | 30 | 29 | 6,706 |
| Datastore | 21 | 21 | 10,560 |
| Protocol | 19 | 18 | 19,920 |
| Format and schema | 14 | 13 | 1,758 |
| Hardware | 15 | 15 | 7,130 |

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

- C (72)
- C++ (7,180)
- C# (1,417)
- Java (5,421)
- Python (23,434)
- JavaScript (3,681)
- TypeScript (6,035)
- Go (1,191)
- Rust (2,711)
- Zig (20)
- SQL (10,061)
- PL/SQL (76)
- Transact-SQL (T-SQL) (30)
- Bash (2,761)
- PowerShell (599)
- Assembly language (19)
- x86 assembly (18)
- ARM assembly (4)
- Ada (21)

### Data, statistics, mathematics, and scientific computing

- R (9)
- MATLAB (1,342)
- Julia (89)
- Fortran (30)
- SAS (190)
- Stata programming language (8)
- AMPL (28)
- GAMS (5)
- MiniZinc (1)

### Web, mobile, and application development

- HTML (3,048)
- CSS (1,208)
- Sass / SCSS (94)
- PHP (384)
- Ruby (775)
- Swift (647)
- Objective-C (103)
- Kotlin (1,256)
- Dart (60)
- Cython (7)

### JVM, functional, and concurrent programming

- Scala (897)
- Clojure (16)
- Groovy (63)
- OCaml (116)
- Haskell (55)
- F# (12)
- Erlang (28)
- Elixir (107)
- Common Lisp (0)
- Scheme (0)
- Prolog (1)
- Chapel (16)

### Systems, accelerator, and hardware-description programming

- CUDA C++ (34)
- Triton Kernel Language (6)
- Verilog (497)
- SystemVerilog (362)
- VHDL (213)
- GLSL (11)
- HLSL (23)
- Metal Shading Language (0)
- WebGPU Shading Language (WGSL) (1)

### Data, infrastructure, and domain-specific languages

- GraphQL (1,065)
- Cypher (53)
- SPARQL (23)
- Kusto Query Language (KQL) (41)
- PromQL (24)
- DAX (43)
- Power Query M (0)
- HashiCorp Configuration Language (HCL) (17)
- Solidity (51)
- q (29)
- Tcl (358)
- Visual Basic .NET (80)
- Visual Basic for Applications (VBA) (248)
- ABAP (94)
- COBOL (18)
- Lua (62)
- Perl (614)
- LaTeX (84)

## Library

### Python data and scientific computing

- NumPy (471)
- pandas (713)
- SciPy (152)
- Polars (78)
- PyArrow (7)
- statsmodels (36)
- NetworkX (7)
- xarray (9)
- GeoPandas (13)
- Shapely (5)
- Requests (0)
- Beautiful Soup (5)
- SQLAlchemy (67)
- Pydantic (127)
- SimPy (3)
- Numba (4)
- CuPy (1)
- RAPIDS cuDF (19)

### Statistical learning and optimization

- scikit-learn (446)
- XGBoost (114)
- LightGBM (56)
- CatBoost (15)
- SHAP (18)
- CVXPY (3)
- Google OR-Tools (11)
- PyMC (5)

### Deep learning and AI

- PyTorch (2,392)
- torchvision (4)
- torchaudio (2)
- TensorFlow (1,314)
- Keras (66)
- JAX (676)
- Flax (13)
- Hugging Face Transformers (39)
- Sentence Transformers (3)
- spaCy (15)
- NLTK (4)
- OpenCV (77)
- Pillow (5)
- ONNX Runtime (61)
- TensorRT (180)
- TensorRT-LLM (190)
- vLLM (507)
- SGLang (270)
- FlashAttention (100)
- FasterTransformer (7)
- llama.cpp (22)
- PyTorch Distributed (132)
- Apple MLX (1)

### Visualization

- Matplotlib (81)
- Seaborn (59)
- Plotly (38)
- Altair (17)
- Bokeh (1)
- D3.js (44)

### C and C++

- C++ Standard Library (1)
- Boost (1,462)
- Eigen (24)
- BLAS (21)
- LAPACK (1)
- Intel oneMKL (1)
- GNU Scientific Library (GSL) (1)
- FFTW (1)
- PETSc (1)
- Trilinos (1)
- GoogleTest (12)
- Google Benchmark (1)
- Catch2 (2)

### GPU and high-performance computing

- cuBLAS (16)
- cuDNN (21)
- NCCL (254)
- CUTLASS (48)
- CUDA Thrust (0)
- OpenMP (21)
- MPI (77)
- DPDK (74)
- Solarflare OpenOnload (5)

### Java, JavaScript, and general application libraries

- React (4,987)
- Redux (217)
- RxJS (38)
- jQuery (35)
- Lodash (7)
- Axios (18)
- Three.js (71)
- JUnit (239)
- Mockito (50)

### Go, Rust, and serialization libraries

- Tokio (46)
- ZeroMQ (34)

## Tool

### Source control and collaboration

- Git (2,632)
- Git LFS (2)
- Jira (3,008)
- Confluence (1,112)

### Editors, IDEs, notebooks, and research authoring

- Visual Studio Code (33)
- Visual Studio (39)
- IntelliJ IDEA (18)
- PyCharm (5)
- Eclipse (71)
- Xcode (93)
- Android Studio (40)
- RStudio (2)
- Jupyter Notebook (22)
- JupyterLab (6)
- Google Colab (1)
- Figma (2,313)
- GitHub Copilot (478)

### Compilers, build systems, and package management

- GCC (119)
- Clang (20)
- LLVM (125)
- Microsoft Visual C++ (MSVC) (2)
- Make (76)
- CMake (180)
- Ninja (0)
- Bazel (355)
- Maven (444)
- Gradle (225)
- npm (154)
- Yarn (35)
- pnpm (15)
- pip (95)
- Conda (11)
- Poetry (0)
- uv (0)
- Cargo (0)
- NuGet (8)
- AWS CLI (2)
- Azure CLI (8)
- Google Cloud CLI (3)
- kubectl (24)

### Containers, infrastructure, and delivery

- Docker (4,147)
- Docker Compose (46)
- Terraform (3,991)
- Ansible (1,241)
- Helm (711)
- Packer (33)
- Vagrant (6)
- Jenkins (1,035)
- GitHub Actions (1,305)
- GitLab CI/CD (119)
- CircleCI (202)
- Travis CI (7)
- Argo CD (96)

### Testing and API development

- pytest (262)
- Selenium (211)
- Cypress (202)
- Playwright (381)
- Cucumber (84)
- Postman (242)
- Insomnia (135)
- Swagger UI (1)
- Apache JMeter (8)
- k6 (39)
- Locust (30)

### Debugging, profiling, and systems inspection

- gdb (154)
- lldb (19)
- Valgrind (37)
- AddressSanitizer (8)
- ThreadSanitizer (1)
- clang-tidy (4)
- Linux perf (2)
- Nsight Systems (34)
- Nsight Compute (19)
- Intel VTune Profiler (37)
- strace (48)
- ftrace (47)
- FlameGraph (1)
- Wireshark (174)
- tcpdump (99)
- PyTorch Inductor (1)
- TorchScript (3)
- torch.compile (17)
- Apache TVM (35)
- MLIR (106)
- XLA (73)

### Data analysis, visualization, and business intelligence

- Microsoft Excel (1,003)
- Microsoft PowerPoint (125)
- Microsoft Word (498)
- Microsoft Access (9)
- Power BI (1,377)
- Tableau (2,227)
- Looker (1,359)
- Qlik Sense (32)
- Alteryx (402)
- dbt (1,498)
- DBeaver (1)
- pgAdmin (3)
- SQL Server Management Studio (SSMS) (53)
- Gurobi Optimizer (19)
- IBM ILOG CPLEX Optimization Studio (14)

### Data, ML, and experiment workflows

- Apache Airflow (117)
- MLflow (385)
- Weights & Biases (101)
- TensorBoard (2)
- Data Version Control (DVC) (31)
- Label Studio (12)
- Streamlit (106)
- Gradio (17)

### Security

- Nmap (21)
- Burp Suite (68)
- Metasploit (16)
- Nessus (43)
- Tenable (68)
- HashiCorp Vault (71)
- OpenSSL (15)
- SonarQube (77)
- Snyk (109)
- Trivy (24)

### Finance and market data

- Bloomberg Terminal (122)
- S&P Capital IQ (5)
- FactSet (48)
- LSEG Workspace / Refinitiv Workspace (0)
- PitchBook (8)
- Morningstar Direct (4)
- QuickBooks (277)

## Platform

### Operating systems and device platforms

- Linux (6,315)
- Unix (979)
- Windows (3,053)
- macOS (873)
- iOS (1,427)
- Android (1,458)
- FreeRTOS (63)
- QNX (59)
- VxWorks (11)

### Cloud platforms

- Amazon Web Services (AWS) (11,452)
- Microsoft Azure (800)
- Google Cloud Platform (GCP) (6,891)
- Oracle Cloud Infrastructure (OCI) (1,006)
- IBM Cloud (5)
- OpenStack (154)
- Cloudflare (962)

### Major AWS services

- Amazon EC2 (3)
- AWS Lambda (91)
- Amazon S3 (18)
- Amazon ECS (5)
- Amazon EKS (30)
- AWS Fargate (10)
- Amazon SageMaker (236)
- Amazon Bedrock (32)
- Amazon EMR (6)
- AWS Glue (64)
- Amazon Kinesis (6)

### Major Azure services

- Azure Virtual Machines (2)
- Azure Functions (32)
- Azure App Service (2)
- Azure Kubernetes Service (AKS) (149)
- Azure Machine Learning (4)
- Azure Databricks (28)
- Azure Synapse Analytics (1)

### Major Google Cloud services

- Google Compute Engine (57)
- Google Cloud Run (2)
- Google Kubernetes Engine (GKE) (265)
- Vertex AI (159)
- Google Cloud Dataflow (2)
- Google Cloud Dataproc (19)

### Containers, orchestration, and source hosting

- Kubernetes (7,945)
- Red Hat OpenShift (7)
- HashiCorp Nomad (1)
- VMware vSphere (11)
- GitHub (2,660)
- GitLab (935)
- Bitbucket (180)

### Language runtimes and application platforms

- Java Virtual Machine (JVM) (261)
- .NET (643)
- Node.js (2,098)
- Deno (14)
- Bun (22)
- WebAssembly (53)
- eBPF (205)
- CUDA (741)
- ROCm (176)
- OpenCL (65)
- Firebase (97)
- Heroku (114)
- Vercel (591)
- Netlify (79)

### Distributed data and compute platforms

- Apache Spark (731)
- Hadoop (320)
- Hadoop Distributed File System (HDFS) (58)
- Apache Flink (641)
- Apache Kafka (2,270)
- Apache Pulsar (3)
- Databricks (2,465)
- Snowflake (2,832)
- Apache Hive (0)
- Apache HBase (1)
- Amazon Redshift (481)
- Google BigQuery (970)
- Teradata (66)
- Cloudera (12)
- Palantir Foundry (107)

### Messaging, workflow, and distributed execution

- RabbitMQ (380)
- Apache ActiveMQ (47)
- Redpanda (77)
- Redis Streams (10)
- Slurm (393)

### ML, AI, and model-serving platforms

- Hugging Face Hub (2)
- Kubeflow (167)
- NVIDIA Triton Inference Server (11)
- TensorFlow Serving (12)
- TorchServe (27)
- Hugging Face Text Generation Inference (TGI) (42)
- OpenAI API (132)
- Azure OpenAI Service (2)
- Anthropic API (9)
- Google Gemini API (0)

### Observability and operations platforms

- Datadog (1,779)
- New Relic (216)
- Grafana (1,929)
- Prometheus (1,428)
- OpenTelemetry (738)
- Elastic Stack (ELK) (361)
- Splunk (586)

### Enterprise, simulation, and robotics platforms

- Salesforce (6,967)
- SAP (3,128)
- ServiceNow (1,805)
- Simulink (285)
- LabVIEW (257)
- Maple (0)
- Robot Operating System (ROS) (15)
- ROS 2 (97)
- Unity (1,230)
- Unreal Engine (312)

## Method

### Software engineering and product development

- Software development (6,928)
- Front-end development (190)
- Back-end development (84)
- Full-stack development (690)
- Mobile application development (45)
- Systems programming (467)
- Embedded software development (175)
- Firmware development (259)
- API development (215)
- API design (1,151)
- Object-oriented design (209)
- Software architecture (775)
- System design (4,223)
- Database design (233)
- Data modeling (1,656)
- Requirements analysis (153)
- Technical documentation (1,822)
- Code review (1,971)
- Refactoring (1,003)
- Debugging (6,895)
- Troubleshooting (9,208)
- Root-cause analysis (3,994)
- Performance profiling (297)
- Performance optimization (1,652)
- Memory optimization (59)
- Algorithm design (106)
- Complexity analysis (13)

### Testing and software quality

- Unit testing (394)
- Integration testing (452)
- End-to-end testing (204)
- Regression testing (415)
- Acceptance testing (677)
- Test automation (1,093)
- Test-driven development (TDD) (318)
- Behavior-driven development (BDD) (73)
- Property-based testing (25)
- Fuzz testing (3)
- Load testing (142)
- Software stress testing (0)
- Performance testing (326)
- Static analysis (159)
- Dynamic analysis (62)

### Delivery, infrastructure, and reliability

- Version control (1,796)
- Continuous integration (1,053)
- Continuous delivery (603)
- Continuous deployment (225)
- CI/CD (7,569)
- Agile software development (207)
- Scrum (1,031)
- Kanban (258)
- Infrastructure as code (1,623)
- Containerization (1,421)
- Cloud deployment (168)
- Configuration management (1,034)
- Release engineering (178)
- Build automation (405)
- Observability (9,284)
- Monitoring (15,893)
- Logging (2,368)
- Distributed tracing (253)
- Capacity planning (1,519)
- Fault injection (120)
- Chaos engineering (113)
- Benchmarking (2,155)
- Microbenchmarking (1)

### Data engineering and analytics

- Data collection (1,516)
- Data analysis (4,673)
- Data ingestion (815)
- Data integration (567)
- Data cleaning (99)
- Data cleansing (35)
- Data preprocessing (52)
- Data transformation (365)
- Data wrangling (57)
- Exploratory data analysis (EDA) (461)
- Data validation (414)
- Data quality testing (15)
- Data visualization (1,251)
- Dashboard development (57)
- Business intelligence (1,107)
- ETL (1,512)
- ELT (484)
- Batch processing (117)
- Stream processing (408)
- Data pipeline development (30)
- Feature engineering (411)
- Feature extraction (43)
- Feature selection (8)
- Dimensionality reduction (13)
- Data mining (282)
- Geospatial analysis (24)
- Web scraping (60)

### Statistics, experimentation, and applied mathematics

- Statistical analysis (747)
- Statistical modeling (324)
- Hypothesis testing (119)
- Experimental design (288)
- A/B testing (939)
- Regression analysis (37)
- Classification (2,382)
- Clustering (292)
- Time-series analysis (135)
- Forecasting (5,790)
- Survival analysis (31)
- Econometric modeling (4)
- Anomaly detection (667)
- Causal inference (390)
- Bayesian inference (25)
- Monte Carlo simulation (239)
- Markov chain Monte Carlo (MCMC) (3)
- Stochastic simulation (3)
- Numerical analysis (63)
- Numerical optimization (58)
- Convex optimization (71)
- Discrete optimization (14)
- Combinatorial optimization (8)
- Linear programming (17)
- Integer programming (5)
- Mixed-integer programming (8)
- Dynamic programming (14)
- Constraint programming (5)
- Simulation modeling (9)
- Sensitivity analysis (111)
- Scenario analysis (314)
- Uncertainty quantification (68)
- Decision analysis (9)
- Signal processing (466)
- Image processing (143)
- Finite-difference methods (1)
- Finite-element methods (20)

### Machine learning and AI

- Machine learning model development (17)
- Model training (1,617)
- Model evaluation (556)
- Model validation (170)
- Model calibration (20)
- Cross-validation (10)
- Hyperparameter optimization (18)
- Predictive modeling (243)
- Deep-learning model development (5)
- Computer-vision model development (1)
- Generative AI development (5)
- Large language model development (2)
- Prompt engineering (1,305)
- Retrieval-augmented generation (RAG) (2,152)
- Fine-tuning (1,511)
- Parameter-efficient fine-tuning (PEFT) (46)
- Ranking (829)
- MLOps (1,065)
- Model deployment (399)
- Model monitoring (123)
- Distributed training (692)
- Distributed inference (108)
- Inference optimization (593)
- Quantization (367)
- Model pruning (9)
- Knowledge distillation (26)
- Model compression (38)
- Model explainability (19)
- Responsible AI evaluation (0)

### Systems, networking, and performance engineering

- Concurrent programming (66)
- Parallel programming (66)
- Multithreading (641)
- Multiprocessing (48)
- Asynchronous programming (112)
- GPU programming (171)
- Vectorization (34)
- SIMD optimization (5)
- Kernel optimization (56)
- Cache optimization (3)
- Lock-free programming (59)
- Network programming (77)
- Socket programming (21)
- Protocol implementation (13)
- Binary protocol parsing (2)
- Serialization (123)
- Distributed-systems engineering (52)
- Low-latency engineering (2,659)
- Real-time systems development (3)
- Compiler development (22)
- Compiler optimization (27)
- Formal verification (211)
- Clock synchronization (17)

### Cybersecurity

- Secure coding (881)
- Security testing (250)
- Threat modeling (636)
- Vulnerability assessment (55)
- Vulnerability management (698)
- Penetration testing (388)
- Security monitoring (272)
- Log analysis (249)
- Security operations (961)
- Incident response (3,458)
- Malware analysis (84)
- Digital forensics (73)
- Reverse engineering (132)
- Network traffic analysis (10)
- Authentication implementation (1)
- Cryptographic implementation (6)
- Application security testing (59)
- Cloud security assessment (2)
- Risk assessment (1,134)

### Quantitative finance, risk, and financial analytics

- Quantitative modeling (777)
- Financial modeling (1,013)
- Mathematical modeling (57)
- Market-data analysis (16)
- Algorithmic trading (371)
- Strategy research (41)
- Strategy implementation (35)
- Backtesting (258)
- Trade execution analysis (0)
- Transaction-cost analysis (34)
- Portfolio optimization (84)
- Asset allocation (132)
- Risk modeling (68)
- Factor modeling (1)
- Derivatives pricing (43)
- Option pricing (26)
- Fixed-income modeling (1)
- Credit-risk modeling (13)
- Market-risk modeling (1)
- Value at Risk (VaR) (193)
- Financial-statement analysis (41)
- Equity research (138)
- Fundamental analysis (22)
- Credit analysis (105)
- Underwriting analysis (4)
- Valuation (3,243)
- Discounted cash-flow analysis (DCF) (53)
- Comparable-company analysis (2)
- Precedent-transaction analysis (13)
- Leveraged-buyout modeling (LBO) (32)
- Budgeting (1,423)
- Financial forecasting (71)
- Variance analysis (529)
- Cash-flow analysis (21)
- Due diligence (3,696)
- Actuarial modeling (7)

## Concept

### Core computer science

- Data structures (1,583)
- Algorithms (4,336)
- Computational complexity (11)
- Object-oriented programming (OOP) (578)
- Functional programming (204)
- Programming-language theory (4)
- Type systems (37)
- Design patterns (1,042)
- Software engineering (10,735)
- Software development lifecycle (SDLC) (2,270)
- Operating systems (2,808)
- Computer architecture (417)
- Computer organization (10)
- Computer networks (35)
- Database systems (329)
- Compilers (532)
- Distributed systems (7,492)
- Parallel computing (73)
- Cloud computing (1,014)
- Edge computing (280)
- Mobile computing (2)
- Embedded systems (1,985)
- Real-time systems (456)
- High-performance computing (HPC) (2,074)
- Human-computer interaction (HCI) (231)
- Computer graphics (84)
- Scientific computing (188)
- Data science (4,989)
- Data engineering (2,677)
- Big data (1,148)
- Formal methods (68)
- Computational geometry (46)
- Robotics (4,881)
- Autonomous systems (1,446)
- Quantum computing (162)
- Bioinformatics (211)
- Computational biology (182)

### Software and distributed architecture

- Client-server architecture (10)
- Service-oriented architecture (96)
- Microservices architecture (2,052)
- Event-driven architecture (1,593)
- Layered architecture (8)
- Model-view-controller (MVC) (95)
- Representational state transfer (REST) (1,198)
- RESTful APIs (2,009)
- Remote procedure calls (RPC) (121)
- Publish-subscribe architecture (261)
- Message queues (210)
- Event sourcing (38)
- Command Query Responsibility Segregation (CQRS) (40)
- Distributed consensus (105)
- CAP theorem (55)
- Consistency models (81)
- Eventual consistency (57)
- Distributed transactions (11)
- Distributed locking (18)
- Fault tolerance (433)
- High availability (944)
- Scalability (7,904)
- Resilience (4,188)
- Load balancing (590)
- Partitioning (308)
- Sharding (172)
- Replication (504)
- Caching (1,086)
- Consistent hashing (12)
- Rate limiting (204)
- Backpressure (154)
- Gossip protocols (8)
- Idempotency (330)
- Disaster recovery (573)

### Databases and data systems

- Relational databases (1,066)
- NoSQL databases (427)
- Document databases (23)
- Key-value stores (21)
- Graph databases (133)
- Time-series databases (97)
- In-memory databases (4)
- Data warehouses (496)
- Data lakes (229)
- Lakehouse architecture (64)
- Data marts (33)
- Object storage (248)
- Online transaction processing (OLTP) (168)
- Online analytical processing (OLAP) (292)
- ACID transactions (18)
- Transaction isolation (8)
- Multiversion concurrency control (MVCC) (17)
- Indexing (622)
- Query optimization (424)
- Columnar storage (35)
- Vectorized query execution (12)
- Write-ahead logging (30)
- Star schema (34)
- Snowflake schema (11)
- Data partitioning (14)
- Data replication (57)
- Data governance (1,384)
- Data lineage (170)
- Data quality (3,144)
- Master data management (116)
- Structured data (598)
- Semi-structured data (30)
- Unstructured data (671)

### Mathematics and statistics

- Calculus (44)
- Multivariable calculus (2)
- Linear algebra (214)
- Numerical linear algebra (8)
- Differential equations (47)
- Ordinary differential equations (ODEs) (2)
- Partial differential equations (PDEs) (12)
- Discrete mathematics (2)
- Graph theory (21)
- Combinatorics (2)
- Probability (479)
- Probability theory (65)
- Statistics (2,726)
- Mathematical statistics (78)
- Stochastic processes (28)
- Stochastic calculus (13)
- Markov chains (0)
- Martingales (1)
- Bayesian statistics (21)
- Frequentist statistics (11)
- Statistical inference (41)
- Optimization theory (9)
- Convex analysis (0)
- Operations research (391)
- Game theory (54)
- Information theory (16)
- Coding theory (1)
- Queueing theory (3)
- Dynamical systems (24)
- Control theory (138)
- Numerical methods (127)

### Machine learning, AI, and data science

- Artificial intelligence (AI) (68,604)
- Machine learning (ML) (16,504)
- Supervised learning (37)
- Unsupervised learning (57)
- Semi-supervised learning (3)
- Self-supervised learning (41)
- Reinforcement learning (861)
- Deep learning (1,463)
- Neural networks (326)
- Transformer architecture (160)
- Large language models (LLMs) (10,606)
- Generative AI (3,361)
- Natural language processing (NLP) (1,127)
- Computer vision (3,524)
- Speech recognition (123)
- Recommender systems (111)
- Information retrieval (247)
- Ranking systems (65)
- Representation learning (116)
- Transfer learning (18)
- Ensemble learning (5)
- Gradient boosting (19)
- Decision trees (81)
- Random forests (15)
- Support vector machines (6)
- Linear models (37)
- Generalized linear models (3)
- Time-series models (10)
- Explainable AI (53)
- Responsible AI (2,658)
- AI safety (767)
- Model fairness (2)
- Model privacy (0)
- Feature stores (210)
- Autoregressive decoding (0)
- KV-cache optimization (154)
- Dynamic batching (4)
- Continuous batching (60)
- Speculative decoding (117)
- Prefill-decode disaggregation (8)
- PagedAttention (23)

### Systems and performance

- Memory management (565)
- Virtual memory (62)
- Processes (0)
- Threads (424)
- Concurrency (1,046)
- Synchronization (510)
- Race conditions (46)
- Deadlocks (10)
- Atomics (118)
- Mutexes (12)
- Semaphores (8)
- Condition variables (2)
- Coroutines (309)
- POSIX threads (1)
- Inter-process communication (IPC) (716)
- Asynchronous I/O (2)
- Non-blocking I/O (2)
- CPU cache locality (34)
- NUMA (77)
- SIMD (98)
- Zero-copy architecture (54)
- Kernel bypass (56)
- POSIX sockets (0)
- UDP multicast (2)
- GPU architecture (131)
- GPU memory hierarchy (6)
- Memory coalescing (7)
- Memory bandwidth (74)
- GPU virtualization (3)
- Floating-point arithmetic (35)
- Mixed-precision computing (47)
- Roofline model (25)
- Low-latency systems (154)
- Real-time scheduling (14)

### Security

- Cybersecurity (3,833)
- Information security (2,011)
- Application security (870)
- Network security (726)
- Cloud security (1,227)
- Data security (2,799)
- Systems security (86)
- Software supply-chain security (104)
- Zero-trust architecture (101)
- Identity and access management (IAM) (1,770)
- Authentication (2,024)
- Authorization (289)
- Cryptography (368)
- Public-key infrastructure (PKI) (197)
- Encryption (987)
- Hashing (34)
- Digital signatures (6)
- Secure software development lifecycle (SSDLC) (38)
- Security operations center (SOC) (2,914)
- Security information and event management (SIEM) (713)
- Threat intelligence (569)
- Incident management (1,066)
- Privacy engineering (46)
- Data privacy (5,621)

### Quantitative finance, markets, and risk

- Quantitative finance (201)
- Financial mathematics (14)
- Financial engineering (162)
- Econometrics (154)
- Economics (4,139)
- Microeconomics (3)
- Macroeconomics (20)
- Corporate finance (514)
- Accounting (7,187)
- Financial statements (772)
- Capital markets (1,044)
- Equity markets (46)
- Fixed-income markets (94)
- Foreign-exchange markets (18)
- Commodities markets (77)
- Derivatives (985)
- Options (159)
- Futures (818)
- Swaps (222)
- Market microstructure (142)
- Order books (32)
- Market data (3,128)
- Electronic trading (318)
- High-frequency trading (187)
- Quantitative research (677)
- Portfolio theory (10)
- Asset pricing (12)
- Factor models (10)
- Arbitrage (47)
- Risk management (4,470)
- Market risk (133)
- Credit risk (501)
- Liquidity risk (56)
- Operational risk (746)
- Model risk (171)
- Counterparty risk (57)
- Volatility (304)
- Yield curves (9)
- Term structure (21)
- Discounted cash flow (18)
- Actuarial science (60)
- Financial technology (fintech) (8,480)
- Computational finance (4)
- Blockchain (1,148)
- Distributed ledger technology (5)

### Engineering processes and operations

- DevOps (3,697)
- Site reliability engineering (SRE) (1,934)
- Reliability engineering (685)
- Quality assurance (2,269)
- Technical debt (692)
- Open-source software (779)
- POSIX internals (148)

## Adjacent categories required for complete recall

These are applicable internship keywords, but assigning them to one of the six
requested categories would be inaccurate. They should become first-class
categories if the production taxonomy is intended to capture the full market.

### Framework candidates

- Angular (625)
- Vue.js (150)
- Next.js (1,106)
- Nuxt (17)
- Express.js (46)
- NestJS (164)
- Django (447)
- Flask (3)
- FastAPI (578)
- Ruby on Rails (231)
- Spring (97)
- Spring Boot (592)
- ASP.NET Core (37)
- Laravel (47)
- Symfony (28)
- React Native (555)
- Flutter (236)
- SwiftUI (207)
- UIKit (127)
- Qt (50)
- .NET MAUI (1)
- Apache Beam (40)
- Celery (0)
- Ray (5)
- LangChain (876)
- LlamaIndex (234)
- Hadoop MapReduce (1)
- Megatron-LM (47)
- DeepSpeed (147)
- Horovod (12)

### Datastore candidates

- PostgreSQL (3,591)
- MySQL (942)
- MariaDB (59)
- SQLite (46)
- Microsoft SQL Server (423)
- Oracle Database (76)
- IBM Db2 (2)
- MongoDB (1,295)
- Redis (1,228)
- Apache Cassandra (9)
- Amazon DynamoDB (469)
- Elasticsearch (758)
- OpenSearch (247)
- Neo4j (164)
- DuckDB (99)
- ClickHouse (896)
- CockroachDB (91)
- InfluxDB (89)
- TimescaleDB (55)
- RocksDB (20)
- LMDB (1)

### Protocol candidates

- HTTP / HTTPS (12,752)
- TCP/IP (1,233)
- UDP (323)
- WebSockets (375)
- gRPC (878)
- OAuth 2.0 (178)
- OpenID Connect (OIDC) (569)
- SAML (668)
- SSH (126)
- TLS / SSL (583)
- DNS (1,347)
- DHCP (315)
- SMTP (24)
- MQTT (142)
- AMQP (14)
- FIX Protocol (59)
- ITCH/OUCH Protocols (0)
- Simple Binary Encoding (SBE) (13)
- RDMA (321)

### Format and schema candidates

- JSON (592)
- XML (366)
- YAML (103)
- CSV (95)
- Apache Parquet (170)
- Apache Arrow (29)
- Apache ORC (0)
- Apache Avro (1)
- Protocol Buffers (260)
- FlatBuffers (6)
- ONNX (115)
- GGUF (5)
- HDF5 (10)
- NetCDF (6)

### Hardware candidates

- FPGA (821)
- ASIC (753)
- Microcontrollers (309)
- Embedded processors (45)
- ARM architecture (19)
- x86 architecture (48)
- Tensor Cores (34)
- High Bandwidth Memory (HBM) (182)
- PCI Express (PCIe) (1,050)
- NVLink (332)
- NVSwitch (24)
- InfiniBand (448)
- RoCE (304)
- Sensors (2,695)
- Robotics hardware (66)

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
