# Software and finance opportunity filter resolution

## Scope

This filter was derived from the 2,505 postings the opportunities page actually
serves on 2026-08-27 (`status='active'`, `job_type IN (intern, coop, student,
new_grad, research)`, US or empty locations), and validated against a held-out
cohort of 40,000 active US postings with `job_type IS NULL` that took no part in
the derivation.

It answers one question: does the title name software work, or quantitative /
applied-math work? Labels are mutually exclusive and software wins, because a
quant-developer role is still a role a software student can take.

`finance` means the quantitative bucket -- quant research and trading,
actuarial, econometrics, derivatives, portfolio. The corporate finance function
(FP&A, accounting, tax, treasury, audit) is deliberately out of scope even at a
quant employer.

## Corpus findings

- Stripping program and scheduling vocabulary left only 18 of 2,505 titles
  uninformative, against 1,834 distinct role residuals. Titles carry the signal.
- Descriptions do not. Of 1,840 unlabeled postings only 74 carry three or more
  software cues, and that set is mostly hardware (FPGA/ASIC, EDA/CAD, flight
  test). The quant equivalent returned 10 rows, one being a legal internship at
  a trading firm.
- `attributes.degree` is not usable as a gate here: 356 unlabeled postings carry
  `cs` purely as degree-requirement boilerplate.
- `attributes.skills` is discriminative but not decisive -- two or more
  programming skills appear on 62.5% of software titles against 15.1% of
  unlabeled, yet the high-skill residual is dominated by hardware
  design-verification roles that list Python and C++ as tooling.
- A veto-first cascade mislabeled 27 postings because the veto words were
  program names: `recruiting` (11) from "Student Science Recruiting", `talent`
  (7) from "Emerging Talent", `sales` (2) from "Sales and Trading".

Every pattern was generalized to the broadest stem that preserves all 2,505
labels, then measured for breadth on the held-out cohort. Stems that swallow
lookalike words were rejected and kept specific: `quant[a-z]*` matches `quantum`
and `quantity`, `programm[a-z]*` matches the British spelling `programme`, and
`(?:systems) engineer[a-z]*` matches aerospace and fluid systems engineering.

Representative true controls include `Software Engineer Intern`, `Kernel
Engineer - New Grad`, `Research Intern - Reinforcement Learning`, `Quantitative
Trader`, `Sales and Trading` at Jane Street, and `Research Summer Analyst` at
AQR.

Representative false controls include `Analog Test Engineer`, `Environmental
Test Engineer`, `Human Resources Intern, Digital & AI`, `RTL Power Optimization
Engineer`, `Graduate Quantity Surveyor`, and `Legal Intern` at a trading firm.

## Resolution cascade

Apply the layers in order and retain the reason that admitted each posting.

### 0. Program-noise normalization

Strip scheduling and program vocabulary before any rule reads the title:
`emerging talent`, `early talent`, `student science recruiting`, `science
recruiting`, `university recruiting`, `campus recruiting`, `early career`,
`talent acquisition`, `leadership development program`, `rotational program`,
`development program`, `internship program`, the posting year, and the season.

Without this step the veto in layer 2 fires on posting-program names rather than
on role nouns.

### 1. Strong software evidence

A title naming software work is labelled `software` and is not subject to the
veto below. An adjacent-discipline guard (`analog`, `electrical`, `mechanic*`,
`environmental`, `civil`, `chemical`, `structural`, `materials`, `thermal`,
`hvac`, `acoustic`, `flight test`, `wafer`) blocks the label unless the title
also carries an unambiguous software term, which is what separates `Electronic
Design Automation` from `Analog Test Engineer`.

### 2. Nontechnical veto

Terminal. A title naming a nontechnical function is left unlabelled even when it
also names a domain.

### 3. Weak software evidence

Bare `ai`, `ml`, `android`, `ios`, `data analy*`, and qualifier-carrying
engineer roles (`cloud`, `platform`, `infrastructure`, `test`, `automation`,
`qa`, `devops`, `reliability`) are read only after the veto clears, because
`Human Resources Intern, Digital & AI` is an HR role.

### 3b. Student domain rotation

A software domain noun (`technolog(y|ies)`, `infrastructure`, `devops`,
`kubernetes`, `observability`) **immediately followed by** a student role noun
(`intern(ship)?s?`, `co-op`) is software, unless guarded. Read after the veto,
like stage 3.

Three constraints hold this stage in place, each of which failed once when
relaxed:

- **The pairing carries the label, not the domain noun.** Alone, `technology`
  matches `Foundry Technology Engineer` and `DRAM Device & Cell Technology
  Engineer`; `infrastructure` matches `Infrastructure Sales`, `Infrastructure
  Finance` and `Head of the EA Infrastructure Fund` (1,025 corpus hits, mostly
  wrong). This stage is the one exception to the widen-then-validate rule the
  rest of this file follows.
- **Adjacency is load-bearing.** Requiring only that both terms appear anywhere
  in the title admits `RiSM Internship ... Pharmaceutical Technology` x7,
  `Technology Development Engineer Intern (Silicon Photonics)`, `Yield
  Technology Equipment` and `Acoustic and Vibration Technologies Co-op`.
- **The role noun is closed to `intern(ship)?s?`.** `intern[a-z]*` matched
  `Technology Internal Audit`.

### 4. Quantitative title evidence

A title naming quant work is labelled `finance` unless it also names the
corporate finance function.

### 5. Quant employer prior

At an employer whose postings are at least 40% quant-titled over at least three
postings, a generic quant role noun is labelled `finance`. Infrastructure roles
(`network`, `linux`, `windows`, `sysadmin`, `identity management`, `security
governance`, `help desk`, `desktop`) are excluded, because a trading firm still
runs an IT department.

This layer admits 4 postings. It is the weakest leg and is safe to drop.
