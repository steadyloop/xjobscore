# Role summaries

## Goal

Show a faithful 4–6 line preview of each student role. The extractor is fully
deterministic and extractive: it only extracts complete source sentences. It
does not call an LLM, paraphrase, pad, or invent facts.

## Algorithm

1. Normalize whitespace while preserving source lines and order.
2. Recognize `role`, `qualification`, `context`, and `excluded` headings.
   Excluded sections include company background, benefits, compensation,
   applications, privacy, accommodations, and EEO text.
3. Split usable lines into sentences, remove bullet markers and metadata, and
   reject fragments shorter than 5 or longer than 50 words.
4. Mark a sentence as role evidence when it contains a concrete action and at
   least one of: a role heading, an action-led opening, role language, or a term
   from the job title. Gerunds and third-person verbs are supported. Mark direct
   education, experience, and skill requirements as qualification evidence.
5. Score each candidate:

   | Signal | Score |
   |---|---:|
   | Role / qualification / context / general section | +30 / +20 / +6 / +0 |
   | Credible role evidence | +14 |
   | Direct qualification | +9 |
   | Student or degree language | +7 |
   | Job-title overlap | +2 per term, up to +6 |
   | 8–28 words / over 38 words | +4 / −3 |
   | Marketing / generic / application copy | −18 / −16 / −12 |
   | Starts lowercase | −5 |

6. Keep the 14 highest candidates scoring at least 5. Test every combination
   of 1–5 candidates and accept only combinations that:

   - contain 40–50 words;
   - contain role evidence, including at least 12 role-evidence words; and
   - contain no sentence pair with 50% or more token overlap.

7. Rank valid combinations by average candidate score, then add +35 for role
   evidence, +6 for qualification evidence, up to +20 for role-word volume,
   and +6 for an explicit role section. Subtract distance from the 45-word
   target and 3 for each sentence beyond three. Ties favor earlier source text.
8. Restore source order and terminal punctuation. If nothing passes, return no
   summary. A credible source already at or below 50 words is returned intact,
   even when it is under 40; padding would invent text.

The exact heading, action, and rejection patterns live in
[`summaries.py`](summaries.py).

## What the live data taught us

| Finding | Change |
|---|---|
| Duties appear under many headings, including German headings | Added normalized heading families instead of depending on one template. |
| Pay, EEO, application questions, and metadata can resemble useful prose | Added hard rejection patterns and excluded-section state. |
| Marketing sometimes appears inside “The Role” | Required concrete actions there and added marketing penalties. |
| Several short qualification bullets could beat two useful duty sentences | Used average candidate quality, reduced the qualification bonus, and rewarded role-word volume. |
| Duties use gerunds or third person: “building…” or “the therapist works…” | Added gerund, inflected-verb, title-overlap, and role-subject signals. |
| Some postings repeat the same idea | Rejected candidate pairs with at least 50% token overlap. |
| Some postings contain no trustworthy duty text | Kept an explicit abstention instead of forcing a plausible-looking result. |

Final read-only audit of 1,631 live descriptions:

- 1,537 summarized (94.2%); 94 abstained;
- median 45 words and 92.2% shorter than the source;
- no detected pay, legal, privacy, or application boilerplate;
- three results were 33 words because that was the complete source text.

Random samples from every job type with described roles were reviewed after
each rule change. The tests preserve the failures found during that review,
including marketing-heavy role sections, qualification stacks, third-person
descriptions, unusual headings, legal-only text, and short sources.

## Storage and display

Gold stores `attributes.summary` and versioned `summary_meta` with status,
timestamp, and a title-plus-description input hash. A title or description
change invalidates both. The API promotes the summary and omits the full
description; Opportunities renders it at `55ch`, which makes 40–50 words about
4–6 lines. Abstentions link the user to the original posting.

## Known tradeoff

Extraction is cheap, repeatable, and cannot hallucinate, but source wording can
occasionally be awkward. New posting formats may abstain until their general
pattern is added and tested against the full corpus.
