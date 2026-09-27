"""Typst rendering, page-count enforcement, and lint for generated resumes.

The template is fixed by design: single column, standard serif, ATS-parseable.
Template choice is not a per-resume option — that is how every export stays
inside the format rules the strategy doc treats as non-negotiable.
"""

from __future__ import annotations

import re
from typing import Any

import typst

SECTION_ORDER = ("education", "experience", "research", "project", "leadership")
SECTION_TITLES = {
    "education": "Education",
    "experience": "Experience",
    "research": "Research",
    "project": "Projects",
    "leadership": "Leadership & Activities",
}
SKILL_GROUP_TITLES = {
    "language": "Languages",
    "framework": "Frameworks & Libraries",
    "tool": "Tools",
    "math": "Math & Methods",
}

_FILLER_PHRASES = (
    "responsible for", "helped with", "worked on", "assisted with",
    "participated in", "various", "duties included",
)

_ESCAPES = str.maketrans({c: f"\\{c}" for c in "\\#$*_`@<>[]~"})

# A URL is interpolated into a Typst *string literal*, not into content, so the
# escapes above are both wrong and insufficient there: what ends a literal early
# is a quote or a backslash, and a newline. Without this a URL containing `")`
# closed the #link( call and compiled the rest of the value as Typst markup.
_STRING_ESCAPES = str.maketrans(
    {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}
)


def typst_escape(text: str) -> str:
    return text.translate(_ESCAPES)


def typst_string(value: str) -> str:
    """Escape a value for use inside a Typst string literal, e.g. #link("…")."""
    return value.translate(_STRING_ESCAPES)


def typst_available() -> bool:
    """Always true: the compiler is a wheel dependency, not a system binary.

    Kept as a function because it is on the wire — ``GET /resumes`` returns it
    and the studio hides its "install Typst" badge on it. The browser-local
    store still answers false, because a guest workspace is never sent to the
    server to be compiled.
    """
    return True


def _dates(entry: dict[str, Any]) -> str:
    start, end = entry.get("start_date", ""), entry.get("end_date", "")
    if start and end:
        return f"{start} -- {end}"
    return start or end or ""


# The live preview in frontend/resume/app.js is the design; this renderer
# follows it. Every measurement below is that preview's stylesheet value
# (frontend/resume/styles.css, the `.preview-*` rules) in CSS px, converted by
# `_pt`: the preview sets 7.7px body text, this document sets 10pt, so one px
# there is 10/7.7 pt here and every other size, gap and rule keeps its ratio.
# Change one side and change the other -- a preview that lies about the export
# is worse than no preview at all.
_PX_TO_PT = 10 / 7.7
_LEADING = "0.45em"          # .preview-entry li line-height: 1.35
_MARGIN = "0.55in"           # the one value not from the preview: the page is fixed, the content is not
_SERIF = '("Georgia", "Times New Roman", "Libertinus Serif", "New Computer Modern")'
_SANS = '("Arial", "Helvetica", "Libertinus Sans", "DejaVu Sans")'
_INK = 'rgb("#161a17")'            # .resume-preview
_AVAILABILITY_INK = 'rgb("#425047")'  # .preview-header > span
_SECTION_RULE = 'rgb("#6e756f")'   # .preview-section > h3 border-bottom
_HEADER_RULE = 'rgb("#1a241e")'    # .preview-header border-bottom


def _pt(px: float) -> str:
    """A preview stylesheet px value as the point size that matches it."""
    return f"{round(px * _PX_TO_PT, 2)}pt"


def _gap(px: float) -> str:
    """A CSS margin between two blocks of text, as Typst vertical space.

    A browser puts half the line's leading above and below every line box, so a
    2px CSS margin between two lines of text is really 2px plus a full leading
    of air. Typst's `#v` sits between the glyph boxes and adds none of it, so
    converting the margin alone renders visibly tighter than the preview.
    """
    return f"{_pt(px)} + {_LEADING}"


def build_typst_source(
    profile: dict[str, Any],
    resume: dict[str, Any],
    entries: list[dict[str, Any]],
    skills: list[dict[str, Any]],
) -> str:
    """Assemble the .typ document from the resume's selection."""
    selection = resume.get("selection") or {}
    chosen_entries = selection.get("entries", [])
    chosen_skill_ids = set(selection.get("skills", []))

    entry_by_id = {entry["id"]: entry for entry in entries}
    bullet_by_id = {
        bullet["id"]: bullet for entry in entries for bullet in entry.get("bullets", [])
    }

    sections: dict[str, list[str]] = {kind: [] for kind in SECTION_ORDER}
    # Contact and education are fixed parts of every resume in the UI. Only
    # reusable story entries are selectable per document.
    picked_entry_ids = {
        picked.get("entry_id") for picked in chosen_entries
    }
    render_entries = [
        {"entry_id": entry["id"], "bullets": []}
        for entry in entries
        if entry.get("kind") == "education" and entry["id"] not in picked_entry_ids
    ] + chosen_entries
    for picked in render_entries:
        entry = entry_by_id.get(picked.get("entry_id"))
        if entry is None:
            continue
        bullets = []
        for picked_bullet in picked.get("bullets", []):
            bullet = bullet_by_id.get(picked_bullet.get("bullet_id"))
            if bullet is None:
                continue
            text = picked_bullet.get("text_override") or bullet["text"]
            bullets.append(f"- {typst_escape(text)}")
        block = [_entry_header(entry, show_detail=picked.get("show_detail", True))]
        if bullets:
            block.append(f"#v({_gap(3)})")  # .preview-entry ul margin-top
            block.extend(bullets)
        sections[entry["kind"]].append("\n".join(block))

    parts = [_preamble(), _header_block(profile, resume)]
    coursework = _coursework_block(skills, chosen_skill_ids)
    for kind in SECTION_ORDER:
        blocks = list(sections[kind])
        if kind == "education" and coursework:
            blocks.append(coursework)
        if not blocks:
            continue
        parts.append(f"== {SECTION_TITLES[kind]}")
        parts.append(f"\n#v({_gap(8)})\n".join(blocks))  # .preview-entry + .preview-entry
    skills_block = _skills_block(skills, chosen_skill_ids)
    if skills_block:
        parts.append("== Skills")
        parts.append(skills_block)
    return "\n\n".join(parts) + "\n"


def _preamble() -> str:
    # Paragraph and block spacing are both zeroed so that every gap in the
    # document is one the preview's stylesheet asks for. Typst's default
    # 1.2em is additive, so an explicit `#v(2px worth)` under a role line came
    # out as 12pt + 2.6pt. Both settings are needed -- `block.spacing` does not
    # cover paragraphs, which is what the skills groups and the detail line
    # are. With them at zero the `#v` calls *are* the CSS margins, and headings
    # keep their own spacing through `above`/`below` below.
    #
    # Section headings are the preview's uppercase, letter-spaced sans rule --
    # `#stack` rather than a negative `#v`, so the gap under the text is the
    # stylesheet's 2px padding and not a number tuned by eye.
    return f"""\
#set page(paper: "us-letter", margin: {_MARGIN})
#set text(font: {_SERIF}, size: {_pt(7.7)}, fill: {_INK})
#set par(leading: {_LEADING}, spacing: 0pt, justify: false)
#set block(spacing: 0pt)
#set list(tight: true, spacing: {_gap(2)}, indent: {_pt(4)}, body-indent: {_pt(5)})
#show heading.where(level: 2): it => block(width: 100%, above: {_gap(13)}, below: {_gap(6)})[
  #stack(spacing: {_pt(2)},
    text(font: {_SANS}, size: {_pt(9)}, weight: "bold", tracking: 0.08em, upper(it.body)),
    line(length: 100%, stroke: 1pt + {_SECTION_RULE}),
  )
]"""


def _header_block(profile: dict[str, Any], resume: dict[str, Any]) -> str:
    """Name, contacts, availability, rule -- the preview's `.preview-header`.

    ``availability_line`` belongs here, under the contacts, and not inside the
    Education entry: the preview is the format of record and that is where it
    puts the line recruiters scan for.
    """
    name = typst_escape(profile.get("full_name") or "Your Name")
    contact_bits = [
        typst_escape(value)
        for value in (profile.get("email"), profile.get("phone"), profile.get("location"))
        if value
    ]
    for link in profile.get("links", []):
        url, label = link.get("url", ""), link.get("label") or link.get("url", "")
        if url:
            contact_bits.append(f'#link("{typst_string(url)}")[{typst_escape(label)}]')
    contact = f" #h({_pt(2)})·#h({_pt(2)}) ".join(contact_bits)

    rows = [
        f'align(center, text(font: {_SERIF}, size: {_pt(24)}, weight: "bold",'
        f" tracking: 0.015em, upper[{name}]))",
        f"v({_pt(5)})",
        f"align(center, text(font: {_SANS}, size: {_pt(7.7)})[{contact}])",
    ]
    availability = typst_escape(resume.get("availability_line", ""))
    if availability:
        rows.append(f"v({_pt(5)})")
        rows.append(
            f'align(center, text(font: {_SANS}, size: {_pt(7)}, style: "italic",'
            f" fill: {_AVAILABILITY_INK})[{availability}])"
        )
    rows.append(f"v({_pt(8)})")
    rows.append(f"line(length: 100%, stroke: {_pt(2)} + {_HEADER_RULE})")
    return "#stack(spacing: 0pt,\n  " + ",\n  ".join(rows) + ",\n)"


def _entry_header(entry: dict[str, Any], show_detail: bool = True) -> str:
    org = typst_escape(entry.get("org", ""))
    role = typst_escape(entry.get("role", ""))
    location = typst_escape(entry.get("location", ""))
    dates = typst_escape(_dates(entry))
    detail = typst_escape(entry.get("detail", "")) if show_detail else ""

    # The preview prints a plain org line, so a URL rides on the org itself
    # rather than a trailing "(link)": same ink, same text, still clickable.
    org_body = f"*{org}*" if org else ""
    if org_body and entry.get("url"):
        org_body = f'#link("{typst_string(entry["url"])}")[{org_body}]'
    left_top = f"#text(size: {_pt(9)})[{org_body}]" if org_body else ""
    right_top = f"#align(right)[#text(size: {_pt(7.8)})[{dates}]]" if dates else ""
    # `.preview-entry-sub em` is semibold italic; the location beside it is not.
    left_bottom = f"#text(size: {_pt(7.8)}, weight: 600)[_{role}_]" if role else ""
    right_bottom = f"#align(right)[#text(size: {_pt(7.8)})[{location}]]" if location else ""

    # The rows are text boxes (cap-height to baseline, like paragraph lines),
    # so the gutter carries the leading as `_gap` does for every other gap.
    # A bare px gutter put the role's caps on top of the org's descenders.
    rows = [f"  [{left_top}], [{right_top}],"]
    # A project usually has neither role nor location. The preview's sub-row is
    # an empty flex box in that case and collapses; an empty Typst grid row does
    # not, it still costs a row gutter -- so omit it.
    if left_bottom or right_bottom:
        rows.append(f"  [{left_bottom}], [{right_bottom}],")
    header = (
        f"#grid(columns: (1fr, auto), row-gutter: {_gap(1)},\n"
        + "\n".join(rows)
        + "\n)"
    )
    # `.preview-detail` is its own full-width line under the role, never run
    # into the role with an em dash.
    if detail:
        header += (
            f"\n#v({_gap(2)})\n"
            f'#text(size: {_pt(7.5)}, style: "italic")[{detail}]'
        )
    return header


def _skills_block(skills: list[dict[str, Any]], chosen_skill_ids: set[int]) -> str:
    # Coursework belongs under Education, not in the Skills section.
    lines = []
    for category, title in SKILL_GROUP_TITLES.items():
        names = [
            typst_escape(skill["name"])
            for skill in skills
            if skill["category"] == category and skill["id"] in chosen_skill_ids
        ]
        if names:
            lines.append(f"*{title}:* " + ", ".join(names))
    if not lines:
        return ""
    # Each group is its own `<p>` in the preview, with a 2px margin between --
    # not a `\` linebreak, which would only give them a line gap.
    body = f"\n#v({_gap(2)})\n".join(lines)
    return f"#text(size: {_pt(7.8)})[\n{body}\n]"


def _coursework_block(skills: list[dict[str, Any]], chosen_skill_ids: set[int]) -> str:
    names = [
        typst_escape(skill["name"])
        for skill in skills
        if skill["category"] == "coursework" and skill["id"] in chosen_skill_ids
    ]
    if not names:
        return ""
    return f'#text(size: {_pt(7.8)})[*Relevant Coursework:* {", ".join(names)}]'


def compile_pdf(source: str) -> tuple[bytes | None, int, str]:
    """Compile Typst source; returns (pdf_bytes, page_count, error).

    ``typst`` is the compiler itself, shipped as a binary wheel — no system
    install, no subprocess, no temp directory. Passing ``bytes`` compiles the
    source in memory; handing it a ``str`` would make it read a *path* instead.

    The subprocess this replaced carried a 30s timeout that a library call
    cannot: there is no way to interrupt the Rust compiler mid-run. The caller
    still runs it in a worker thread, so a pathological document ties up that
    thread rather than the event loop.
    """
    try:
        pdf = typst.compile(source.encode("utf-8"))
    except typst.TypstError as error:
        return None, 0, str(error).strip()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))
    return pdf, pages, ""


def lint_resume(
    profile: dict[str, Any],
    resume: dict[str, Any],
    entries: list[dict[str, Any]],
    page_count: int | None = None,
) -> list[dict[str, str]]:
    """Render-time checks for the rules a human reviewer would flag."""
    problems: list[dict[str, str]] = []

    def flag(level: str, message: str) -> None:
        problems.append({"level": level, "message": message})

    if page_count is not None and page_count > 1:
        flag("error", f"Resume is {page_count} pages — one page is non-negotiable for internships.")
    if not profile.get("email"):
        flag("error", "Profile has no email address.")
    if not resume.get("availability_line"):
        flag("warn", "No availability line (e.g. 'Available for Spring 2027 co-op') — "
                     "recruiters filter on legible availability.")

    selection = resume.get("selection") or {}
    bullet_by_id = {
        bullet["id"]: bullet for entry in entries for bullet in entry.get("bullets", [])
    }
    chosen: list[str] = []
    for picked in selection.get("entries", []):
        for picked_bullet in picked.get("bullets", []):
            bullet = bullet_by_id.get(picked_bullet.get("bullet_id"))
            if bullet is not None:
                chosen.append(picked_bullet.get("text_override") or bullet["text"])
    if not chosen:
        flag("error", "No bullets selected.")

    ends_with_period = 0
    for text in chosen:
        lowered = text.lower()
        for phrase in _FILLER_PHRASES:
            if phrase in lowered:
                flag("warn", f"Filler phrase '{phrase}' in: \"{text[:70]}…\"")
        if re.search(r"(?:^|\s)I(?:\s|'|,)", text):
            flag("warn", f"First person ('I') in: \"{text[:70]}…\"")
        if not re.search(r"\d", text):
            flag("info", f"No metric in: \"{text[:70]}…\" — quantify if honestly possible.")
        if len(text) > 240:
            flag("warn", f"Bullet likely wraps past 2 lines ({len(text)} chars): \"{text[:70]}…\"")
        if text.endswith("."):
            ends_with_period += 1
    if chosen and 0 < ends_with_period < len(chosen):
        flag("warn", "Inconsistent trailing periods across bullets — pick one style.")
    return problems
