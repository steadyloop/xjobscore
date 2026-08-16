# Resume intake worksheet

Fill this in offline, then type it into the resume studio at
<http://127.0.0.1:8766/resume/>. Every field below maps 1:1 to a field the
studio actually stores — nothing here is decorative, and nothing the studio
needs is missing.

Field limits are the ones enforced by `resume_models.py`; exceeding them is a
422 from the API. Enum values must be typed exactly as shown.

---

## 1. Profile

One profile for all resumes. Studio tab: **Content bank → Profile**.

| Field | Limit | Your value |
|---|---|---|
| Full name | 200 chars | |
| Email | 320 chars — **required**, lint errors without it | |
| Phone | 50 chars | |
| Location | 200 chars | |

### Links

One per line as `label url`. The label is what prints in the header; the URL is
the target. Leave the label off and the URL prints raw.

```
GitHub  https://github.com/
LinkedIn  https://www.linkedin.com/in/
Portfolio  https://
```

---

## 2. Entries

An *entry* is one org/school/project. Repeat this block per entry — there is no
cap. Studio tab: **Content bank → Add entry**.

`kind` must be exactly one of: `education` · `experience` · `research` ·
`project` · `leadership`. It decides the printed section:

| kind | Prints as |
|---|---|
| `education` | Education |
| `experience` | Experience |
| `research` | Research |
| `project` | Projects |
| `leadership` | Leadership & Activities |

### Entry template — copy per entry

```
Kind:            (education | experience | research | project | leadership)
Organization:    (required, ≤300)          e.g. Northeastern University
Role / degree:   (≤300)                    e.g. BS Computer Science
Location:        (≤200)                    e.g. Boston, MA
Start:           (≤40)                      e.g. Sep 2024
End:             (≤40)                      e.g. Present
URL:             (≤2000, optional)          prints as a small "(link)"
Detail:          (≤500)                     GPA line / tech stack; prints beside the role
```

Dates print as `Start -- End`. Give only one and only that one prints.

### Bullets for this entry

Bullets are the atomic unit — a resume is a *selection* of bullets, so write
every bullet you might ever use, here, once. Repeat per bullet.

```
Text:            (required, ≤600)
Audience:        (all | swe | quant | research)   default: all
Tags:            comma-separated, e.g. python, optimization
Defense notes:   (≤4000) what you'd say to "tell me more about this"
```

**Audience** filters what the builder offers you: a bullet tagged `quant` is
dimmed when you're building a `swe` resume. `all` always shows. Defense notes
never print — they exist so you can't put a bullet on a resume you can't defend
in an interview.

**Write bullets to pass the linter.** It flags, at render time:

- **error** — resume renders to more than 1 page
- **error** — no bullets selected
- **error** — profile has no email
- **warn** — filler phrases: `responsible for`, `helped with`, `worked on`,
  `assisted with`, `participated in`, `various`, `duties included`
- **warn** — first person (`I`)
- **warn** — bullet over 240 chars (wraps past two lines)
- **warn** — some bullets end in a period and some don't
- **info** — no digit anywhere in the bullet (quantify if honestly possible)

Shape that survives all of it: **verb + what + how + number**, no `I`, under
240 chars, consistent punctuation across every bullet you might select together.

---

## 3. Skills

Flat bank, deduplicated by (category, name) — a duplicate is a 409. Category
must be exactly one of `language` · `framework` · `tool` · `coursework` · `math`.

`coursework` is special: it does **not** print in the Skills section. Selected
coursework prints under your education entry as `Relevant coursework: …`.

```
language:    (prints as "Languages")
framework:   (prints as "Frameworks & Libraries")
tool:        (prints as "Tools")
math:        (prints as "Math & Methods")
coursework:  (prints under Education, not in Skills)
```

| Category | Name (≤120) | Audience (all/swe/quant/research) |
|---|---|---|
| | | |
| | | |
| | | |

---

## 4. Target postings

One per job you're tailoring to. Paste the **full** JD text — coverage analysis
reads it literally and reports matched / in-bank-but-not-shown / missing.
Studio tab: **Targets**.

```
Company:      (required, ≤300)
Role title:   (required, ≤500)
Posting URL:  (≤2000)
Date posted:  (≤40, YYYY-MM-DD)
Job description: (≤100,000 — paste the whole thing, verbatim)
Note:         (≤4000) referral contact, recruiter, deadline, …
```

Coverage never invents a skill for you. `missing` terms are surfaced so
skipping them is a conscious decision, not an oversight.

---

## 5. The resume itself

A resume is a name + template parameters + which bullets and skills to show.
Studio tab: **Builder**.

```
Name:               (required, ≤300)   e.g. "Jane Kim — SWE — Stripe"
Target posting:     (pick one from §4, or none)
Variant:            (swe | quant | research)   drives audience filtering
Availability line:  (≤300)   e.g. "Available for Spring 2027 co-op"
Education note:     (≤300)   e.g. "GPA: 3.8/4.0"
```

Leave the availability line blank and the linter warns — recruiters filter on
legible availability.

### Selection

Tick, in order, which entries appear and which of their bullets:

```
Entry: ______________________
  [ ] bullet …                       (optional per-resume text override, ≤600)
  [ ] bullet …
Entry: ______________________
  [ ] bullet …

Skills to show: ______________________
Coursework to show: ______________________   (prints under Education)
```

A text override changes the wording on *this resume only*; the bank keeps the
original.

---

## 6. What is not yours to choose

The template is fixed by design — single column, New Computer Modern, US Letter,
0.55in × 0.5in margins, ATS-parseable. There is no template picker, and section
order is always Education → Experience → Research → Projects → Leadership →
Skills. That is how every export stays inside the format rules.

**One page is a hard error**, not a warning. If `/check` reports 2 pages, cut
bullets — the builder will not export around it.

PDF export needs Typst installed (`brew install typst`); without it the studio
shows a badge, `/pdf` returns 503, and the linter still runs minus the page
count. `GET /api/resume/resumes/{id}/source` returns the raw `.typ` if you want
to hand-tune outside the studio.
