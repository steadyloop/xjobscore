# Bioengineering opportunity filter resolution

## Scope

This filter was derived from all 242,954 rows in `job_posting` on 2026-08-21.
Because student recruiting was out of season, full-time titles without senior or
manager markers were used as the main proxy for future internship and co-op
titles. Existing student rows were retained as a validation slice.

The filter answers two separate questions:

1. Is the posting student-oriented? This remains `job_type`.
2. Does the title resemble work a bioengineering student could perform? This is
   the title cascade below.

Description-only focus-area matches are discovery evidence, not sufficient
recommendation evidence.

## Corpus findings

- The current bio focus fields selected 8,397 unique postings.
- Of 85 student-oriented postings in that set, only 26 had a bio signal in the
  title; 59 inherited the signal from description text.
- At least 860 of the 8,397 had explicitly nontechnical titles such as sales,
  marketing, finance, accounting, legal, or recruiting.
- A direct title cohort contained 2,461 postings. A separate cohort of 7,305
  postings at `company.labels.biotech` employers showed that company membership
  alone is not sufficient: sales and translation titles were among its most
  frequent terms.
- Mining technical, non-senior title families produced 1,983 proxy candidates
  and 56 current student postings for manual inspection.

Representative true controls include `Biomedical Engineer Intern`,
`Neuroengineer Intern`, `Computational Neuroscience Intern`, `Lab Automation
Internship`, `Intern, Protein Design`, `Engineering Intern` at Medtronic, and
Roche internships naming pharmaceutical sciences.

Representative false controls include AbbVie finance and pharmaceutical-sales
internships, a Tacto videography internship, and LLNL national-security
internships that inherit `medical devices` from a wireless-device compliance
footer.

## Resolution cascade

Apply the layers in order and retain the reason that admitted each posting.

### 1. Nontechnical veto

Do not recommend a title containing one of these functions solely because it
also names a bio domain:

`sales`, `marketing`, `brand`, `commercial`, `finance`, `accounting`,
`recruiting`, `human resources`, `legal`, `counsel`, `communications`,
`business development`, `customer success`, `account executive`,
`administrative`, `executive assistant`, `videography`, `partnerships`,
`product specialist`, or `business technology`.

The posting may remain discoverable as a low-confidence industry match.

### 2. Direct domain-title evidence

Accept a title when it contains a domain phrase plus a technical occupation or
an explicit student marker. High-value domain phrases are:

- bioengineering, biomedical engineering, biological engineering, biochemical
  engineering, biosystems engineering, clinical engineering;
- neuroengineering, neural engineering, biomechanics, biomaterials, tissue
  engineering, regenerative medicine, biosensors;
- bioinformatics, computational biology, biostatistics;
- immunology, neuroscience, neurobiology, pharmacology, toxicology,
  microbiology, biochemistry, biophysics;
- pharmaceutical sciences, drug discovery, drug development, drug delivery,
  assay development, cell biology, cell culture, cell engineering, protein
  design, protein engineering;
- bioprocessing, biomanufacturing, biorefinery, upstream/downstream processing,
  fermentation, laboratory automation, microfluidics;
- medical-device engineering, medical imaging, biomedical equipment, surgical
  robotics, neural interfaces, and brain-computer interfaces.

Technical/student markers include `engineer`, `scientist`, `research`,
`associate`, `technician`, `technologist`, `analyst`, `developer`, `programmer`,
`clinical`, `regulatory`, `laboratory`, `process`, `manufacturing`, `quality`,
`validation`, `specialist`, `intern`, `internship`, `co-op`, `student`,
`trainee`, and `apprentice`.

### 3. Bio-employer prior

Generic technical titles require employer evidence. An employer is a candidate
bio employer when either:

- `company.labels` contains `biotech`; or
- at least five current titles contain direct domain evidence and those titles
  are at least 2% of the employer's current postings; or
- at least three current titles contain direct domain evidence and those titles
  are at least 4% of the employer's current postings.

This corpus-derived prior recovered large pharmaceutical and med-device
employers plus focused companies such as Adaptyv, Freenome, Alamar Biosciences,
and Neuralink. The 2% floor prevents large mixed employers such as Oracle from
qualifying on a handful of unrelated titles. It also did not promote LLNL from
a repeated description footer.

### 4. Technical title families at a bio employer

At a bio employer, accept non-senior titles in these families:

- research scientist, research associate/assistant/engineer/technician, R&D
  engineer or scientist;
- process, process-development, manufacturing, or product-development engineer,
  scientist, associate, or technician;
- quality, QA/QC, or validation engineer, scientist, associate, specialist, or
  technician;
- laboratory scientist, associate, engineer, technician, technologist, or
  automation engineer;
- clinical engineer/scientist/specialist/technologist, clinical research
  associate/coordinator, or regulatory associate/specialist/scientist/engineer;
- systems, mechanical, electrical, mechatronics, materials, software, data, or
  machine-learning engineer.

For the full-time proxy set, seniority markers (`senior`, `principal`, `staff`,
`lead`, `manager`, `director`, `head`, `chief`, `VP`, `fellow`) are excluded.
They are not needed when the posting already has a student `job_type`.

### 5. Generic student technical titles

At a bio employer, also accept titles such as `Engineering Intern`, `R&D
Intern`, `Quality Intern`, `Validation Engineer Trainee`, and comparable co-op
or apprentice forms. Keep the technical function in the evidence so facilities,
support, and unrelated software roles can be ranked below direct domain titles.

### 6. Description-only evidence

A description-only bio match must not admit a recommendation. Keep it as a
low-confidence discovery candidate until one of these supplies role evidence:

- a title rule above;
- a parsed qualification explicitly naming the field or degree; or
- a parsed responsibility describing relevant technical work.

Company introductions, product portfolios, legal footers, EEO language, and
wireless-device notices are boilerplate for this purpose.

## Ambiguous terms

- Never match bare `GCP`; use `ICH-GCP`, `good clinical practice`, or a qualified
  clinical form. Bare `GCP` overwhelmingly means Google Cloud Platform here.
- Never match bare `genetics` in descriptions. It occurs in 16,479 otherwise
  unlabeled descriptions, primarily through EEO text containing `genetic
  information`.
- `immunology` and `neuroscience` are useful title signals but noisy description
  signals because pharmaceutical companies repeat therapeutic-area language
  across unrelated roles.
- `life sciences`, `medical devices`, and `biotechnology` remain useful company
  or focus-area evidence, but are not sufficient role evidence.

## Refresh rule

Recompute title counts and employer priors on each materially new corpus. New
terms enter the cascade only after actual matching titles and false controls are
reviewed. Preserve unmatched postings as an explicit residual rather than
weakening a rule to force coverage.
