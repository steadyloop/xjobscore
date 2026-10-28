"""Applicant eligibility checks for student and recent-graduate opportunities.

Discovery must fetch descriptions independently of this classifier. These are
measured heuristics, not proof that every eligible job is recognizable from
employer prose. Explicit enrollment alternatives and preferred qualifications
must not be mistaken for graduate-only requirements.
"""
from __future__ import annotations

import re

from xjobscore.company_name import plain_text


def _pattern(value: str) -> re.Pattern[str]:
    return re.compile(value, re.IGNORECASE)


_UNDERGRAD = _pattern(
    r"\b(?:bachelor\w*|undergrad\w*|baccalaureate|bsc|bs|associate['’]?s?|"
    r"freshm[ae]n|sophomore|junior\s*,?\s*(?:or\s+)?senior|senior\s*,?\s*or\s+graduate)\b"
)
_ADVANCED = _pattern(
    r"\b(?:master[’']?s?|msc|ms(?! office)|mba|phd|doctoral|"
    r"graduate (?:student|program|degree))\b"
)
_PREFERRED = _pattern(
    r"preferred (?:qualifications|requirements|skills|experience|education)|nice.to.have|desirable|"
    r"what we prefer|what you.ll ideally bring"
)
_REQUIRED = _pattern(
    r"(?:minimum|required|basic) (?:qualifications|education|skills|experience)|"
    r"internship qualifications|requirements|qualifications|what you.ll (?:bring|need)|"
    r"(?:^|\n)\s*required\s*:"
)
_OPTIONAL = _pattern(r"preferred|desired|a plus|may also be considered|also (?:welcome|eligible)")
_STAFF = _pattern(
    r"\b(?:internship coordinator|student (?:support counselor|"
    r"services? (?:manager|coordinator|advisor)|advisor)|"
    r"parent.{0,15}student programs? coordinator|assistant director|director of)\b"
    r"|\bco[ -]?op (?:transition )?advis(?:er|or)\b"
)
_TRANSLATED_EXCLUSION = _pattern(r"pharmazeut|pharmazie|apotheker|krankenpfleg|pflegefach")
_INTERN = _pattern(
    r"(?<![a-z0-9])(?:praktikant(?:in|en)?|praktikum|praktika|praktikantenprogramm|"
    r"stagiaire|estagi[aá]ri[oa]|est[aá]gio|becari[oa]|pasant[ií]a)(?![a-z0-9])|实习"
)
_STUDENT = _pattern(
    r"(?<![a-z0-9])(?:werkstudent(?:in|en)?|alternance|alternant[e]?|aprendiz|"
    r"ausbildung|auszubildende[rn]?)(?![a-z0-9])"
)
_STUDENT_EVIDENCE = _pattern(
    r"(?:requires?|recipient of|qualifies for).{0,35}(?:federal )?work.study(?: award)?"
    r"|currently enrolled.{0,55}(?:student|university|school|program)"
    r"|must be.{0,45}(?:current|continuing|enrolled|matriculated).{0,30}student"
    r"|current (?:graduate or )?undergraduate student"
    r"|(?:the|this) student assistant will"
    r"|seeking a student.{0,35}co.op"
    r"|\bbe (?:a )?(?:first|second|third|fourth)[ -]year student\b"
)
_GRAD_EVIDENCE = _pattern(
    r"\b(?:fresh|recent|new) graduates?\b.{0,55}\b(?:welcome|encouraged|invited)\b"
    r"|\b(?:welcome|encourage|seeking|looking for)\b.{0,35}\b(?:fresh|recent|new) graduates?\b"
    r"|\bgraduate (?:program|programme)\b.{0,150}\b(?:career|start|begin)\b"
    r"|\bfor (?:fresh|recent|new) graduates?,? you\b"
    r"|\(graduates?\).{0,120}(?:kick.start your career|begin your professional journey)"
)


def _normalize(text: str) -> str:
    text = plain_text(text) if isinstance(text, str) else ""
    # Preserve clause boundaries while protecting degree abbreviations from
    # sentence splitting. MS Office must retain its ordinary software meaning.
    for old, new in [(r"ph\.?d\.?", "PhD"), (r"m\.s\.", "MS"),
                     (r"b\.s\.", "BS"), (r"m\.sc\.", "MSc"), (r"b\.sc\.", "BSc")]:
        text = re.sub(r"\b" + old, new, text, flags=re.IGNORECASE)
    return text


def advanced_required(description: str) -> bool:
    """Reject positive advanced-study requirements, not optional alternatives."""
    return _advanced_required(_normalize(description))


def _optional(text: str, start: int, clause: str) -> bool:
    before = text[:start]
    preferred = list(_PREFERRED.finditer(before))
    required = [m for m in _REQUIRED.finditer(before)
                if not any(p.start() <= m.start() < p.end() for p in preferred)]
    return bool(_OPTIONAL.search(clause) or (
        preferred and (not required or preferred[-1].start() > required[-1].start())
    ))


def _advanced_required(text: str) -> bool:
    for match in re.finditer(
        r"\b(?:pursuing|enrolled|registered|pursuance|attained)\b[^.!?;\n]{0,300}",
        text, re.IGNORECASE,
    ):
        start = max(text.rfind(c, 0, match.start()) for c in "\n.;") + 1
        clause = text[start:match.end()]
        if not _ADVANCED.search(clause):
            continue
        # An already completed bachelor's can be a prerequisite for the
        # required master's enrollment; it is not an enrollment alternative.
        prerequisite = re.search(
            r"\b(?:completed|earned|hold|holds|attained)\b.{0,60}"
            r"\b(?:bachelor\w*|undergrad\w*|bsc|bs)\b.{0,60}\band\b",
            text[start:match.start()], re.I,
        )
        if _UNDERGRAD.search(clause) and not prerequisite:
            continue
        if re.search(r"\bdiploma\b|four.year accredited (?:college|university)|150 (?:academic )?credits", clause, re.I):
            continue
        if _optional(text, match.start(), clause):
            continue
        return True
    # Some jobs require an already completed degree or a thesis placement,
    # without using an enrollment verb. Read the degree requirement in its
    # qualification section, not an institution's degree offerings or benefits.
    for match in re.finditer(
        r"(?:^|\n)\s*(?:[•■*-]\s*)?(?:master[’']?s?|msc|mba|phd|doctoral)"
        r"\s+(?:degree|thesis)\b[^.!?;\n]{0,220}", text, re.I,
    ):
        clause = match.group()
        if _UNDERGRAD.search(clause) or _optional(text, match.start(), clause):
            continue
        if _REQUIRED.search(text[max(0, match.start() - 240):match.start()]):
            return True
    for match in re.finditer(
        r"\b(?:intended|available|open) only for\b[^.!?;\n]{0,80}"
        r"\b(?:msc|master[’']?s?|mba|phd)\s+(?:thesis|students?)\b", text, re.I,
    ):
        if not _UNDERGRAD.search(match.group()):
            return True
    for match in re.finditer(
        r"\b(?:current graduate student|graduate student position|graduate students? only)\b[^.!?;\n]{0,80}",
        text, re.IGNORECASE,
    ):
        if not _UNDERGRAD.search(match.group()):
            return True
    return False


def student_evidence(text: str) -> bool:
    for match in _STUDENT_EVIDENCE.finditer(text):
        start = max(text.rfind(c, 0, match.start()) for c in "\n.;!?") + 1
        prefix = text[start:match.start()]
        # Serving enrolled students is not evidence that the applicant is one.
        if re.search(
            r"\b(?:(?:advis|supervis|manag|serv|coordinat)(?:e[sd]?|ing)|"
            r"overse(?:e|es|eing)|"
            r"(?:support|mentor|teach|assist|recruit)(?:s|ed|ing)?)\b", prefix, re.I,
        ):
            continue
        return True
    return False


def job_type(title: str | None, description: str | None, *, title_type: str | None,
             excluded: bool = False) -> str | None:
    """Refine title classification and recover explicit description evidence."""
    title = title if isinstance(title, str) else ""
    if excluded or _TRANSLATED_EXCLUSION.search(title):
        return None
    text = _normalize(description or "")
    if _advanced_required(text):
        return None
    student = student_evidence(text)
    if _STAFF.search(title) and not student:
        return None
    if (title_type == "coop" and not student
            and not re.search(r"\b(?:intern|student|apprentice)\b", title, re.I)
            and re.search(r"\bcontinuity of operations\s*\(coop\)", text, re.I)):
        return None
    if title_type:
        return title_type
    if _INTERN.search(title):
        return "intern"
    if _STUDENT.search(title):
        return "student"
    if student:
        return "coop" if re.search(r"\bco[ ‐‑–—-]?op\b", title, re.I) else "student"
    if _GRAD_EVIDENCE.search(" ".join(text.split())):
        return "new_grad"
    return None
