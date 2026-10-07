# Bioengineering focus chips: MedTech, Regulatory, BioTech

The three focus chips answer one question per posting: should a bioengineering or
biomedical-engineering student (BS/MS) target this role, and as which kind of work?
`job_type` still decides whether a posting is student-oriented. The chips are
stored by the Gold `focus` step as `attributes.focus` (chip keys `med_device`,
`clinical_affairs`, `biotech`) with the evidence behind each in
`attributes.focus_meta.reasons`. Rules live in `xjobscore/bioengineering.py`;
the employer registry in `bioengineering-employers.yaml`.

## What each chip means

- **MedTech** — building, testing, making, servicing or clinically supporting
  medical technology: any engineering, quality, manufacturing, software or
  design role at a device, diagnostics, imaging, surgical-robotics or neurotech
  company; device-specific roles anywhere (biomedical/clinical engineering, HTM,
  medical physics, health sensing); field-clinical roles at device makers.
- **Regulatory** — regulatory affairs, life-science quality (QA/QC, validation,
  CAPA, GMP/GCP), clinical research and trial operations, pharmacovigilance,
  medical/scientific writing.
- **BioTech** — biotech, pharma and life-science R&D, process development,
  biomanufacturing, QC/analytical labs, computational biology, research tools,
  synthetic and ag biotech, biomedical research labs.

Out of scope everywhere: business and support functions, corporate IT, supply
chain, facilities and trades, patient care and licensed clinical roles,
teaching, chemistry-only roles, gig "AI training" work.

## Evidence cascade

1. **Vetoes** on the title (business/support/trade; patient care). Phrases such
   as "post-market", "drug delivery", "adverse event", "regulatory policy",
   "health economics" and "patient recruitment" are blanked first so their veto
   word does not fire.
2. **Strong title words** count at any employer ("Biomedical Engineer",
   "Regulatory Affairs", "Cell Culture", "Clinical Research").
3. **Weak title words** count only with life-science context: a registered
   employer or a role-level description term ("Quality", "Microbiology",
   "Ultrasound", "Formulation"). Therapeutic areas ("Oncology", "Vaccines")
   count for BioTech only beside a science or engineering noun.
4. **Employer segment × role function.** Device makers and pharma post most
   student roles under generic titles, so a registered segment plus the role's
   function decides the chip (see the YAML header for the mapping).
   Program-wide postings ("Undergrad Intern", "Co-op Opportunities, Waltham")
   take the employer's chip.
5. **Role-level description terms** stand in for an unregistered employer:
   ISO 13485, design controls, IEC 60601, 510(k) for MedTech; cell culture,
   transfection, flow cytometry for BioTech; cGMP, IRB, informed consent,
   21 CFR for Regulatory. Industry words ("medical devices", "biologics",
   "pharmaceutical") are never enough — they recur in boilerplate (LLNL's
   wireless-device footer, consultancies' industry lists). Food, beverage and
   water-treatment descriptions cannot supply BioTech evidence.

## Measurement (2026-10-06)

Gold set: 2,342 US student postings, stratified (title vocabulary, employer
signal, description signal, and a 400-posting random draw from everything
else), labeled independently against a written rubric; 70/30 dev/test split.

| Labels | MedTech P / R | Regulatory P / R | BioTech P / R |
|---|---|---|---|
| High-confidence (2,098) | 0.97 / 1.00 | 0.97 / 0.97 | 0.96 / 0.96 |
| All, incl. gray (2,342) | 0.95 / 0.90 | 0.96 / 0.59 | 0.95 / 0.88 |
| Held-out test split | 0.96 / 0.90 | 0.94 / 0.59 | 0.95 / 0.88 |

Most Regulatory misses are medium-confidence program-wide or business-adjacent
postings (e.g. "Co-op Opportunities" at Sanofi, "Clinical Trial Diversity").
The persistent high-confidence errors are J&J process/automation co-ops, which
sit on either the MedTech or the Innovative Medicine side and get both chips.

Without the employer registry, title and description evidence alone keep
precision near 1.0 but recall drops to 0.26 (MedTech), 0.78 (Regulatory) and
0.63 (BioTech): device makers in particular post generic engineering titles.
On 2026-10-06 the live US student feed had 35,252 postings: 422 MedTech,
170 Regulatory, 546 BioTech, 1,011 with any chip.

## Why stored, not computed in SQL

The rules use ~45 regular expressions. PostgreSQL evaluates these alternations
at 10-14 µs per pattern per row and recompiles once more than 32 distinct
patterns are live in one query (a 45-pattern scan of 63k rows ran for over
eight minutes). Python classifies all 63k typed postings in about 75 s, once.

## Collisions measured or reported

- `implant`: semiconductor ion implant. `ultrasound`: sonography students, NDT.
- `regulatory affairs`: utilities, banks, chemical makers (needs a life-science
  regulator in the description at an unregistered employer).
- `quality systems`, CAPA, IQ/OQ/PQ: steel, EMS and drone makers use them too.
- `cell engineering/manufacturing`: battery makers. `fermentation`, `assay`,
  `chromatography`: breweries, mining, petrochemicals.
- `CRM` is Cardiac Rhythm Management at Abbott/Medtronic, not only Salesforce.
- `GCP` is Google Cloud Platform unless qualified; `GLP` can be GLP-1.
- "Associate Clinical Specialist" / mapping roles are MedTech field-clinical
  work, not clinical research.
- Employer names: many are raw ATS keys ("jj", "bdx", "gehc", "vrtx") or
  anonymous Oracle tenants; `icon`, `Rho`, `Kestra`, `caribou`, `novoco`
  are unrelated companies sharing a life-science name.

## Maintaining the registry

1. List employers with student postings that are not registered but show
   life-science signals: strong chip words in their titles, or role-level
   description terms. Review each by its titles and description head.
2. Add confirmed employers under the right segment(s), keyed by
   `company.name` exactly as stored; note the real company for tenant keys.
3. Editing the YAML changes `focus_version()`, so the Gold catch-up loop
   restamps every typed posting on its next pass (or run
   `uv run python -m xjobscore.run --enrich focus`).

Bump `FOCUS_VERSION` in `bioengineering.py` whenever a rule changes, and add the
posting that motivated the change to `tests/test_bioengineering.py`.
