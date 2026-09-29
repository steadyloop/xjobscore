"""Typst rendering for generated resumes.

Each resume picks one of the designs below by its ``template`` field: "main"
(serif, ruled sections) or "barlow" (sans, no rules). Both are single column
and ATS-parseable.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import typst

# Vendored OFL faces (Barlow, Barlow Condensed), searched before system fonts
# so a template that names them renders the same on every machine.
_FONT_DIR = str(Path(__file__).parent / "fonts")

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


def _dates(entry: dict[str, Any]) -> str:
    start, end = entry.get("start_date", ""), entry.get("end_date", "")
    if start and end:
        return f"{start} -- {end}"
    return start or end or ""


# The "main" design was ported from an HTML preview (since removed), so its
# sizes are written as that preview's CSS px and converted by `_pt`: the preview
# set 7.7px body text, this document sets 10pt, so one px there is 10/7.7 pt
# here and every other size, gap and rule keeps its ratio.
_PX_TO_PT = 10 / 7.7
_LEADING = "0.45em"          # line-height 1.35
_MARGIN = "0.55in"
_SERIF = '("Georgia", "Times New Roman", "Libertinus Serif", "New Computer Modern")'
_SANS = '("Arial", "Helvetica", "Libertinus Sans", "DejaVu Sans")'
_INK = 'rgb("#161a17")'
_AVAILABILITY_INK = 'rgb("#425047")'
_SECTION_RULE = 'rgb("#6e756f")'
_HEADER_RULE = 'rgb("#1a241e")'


def _pt(px: float) -> str:
    """A CSS px value as the point size that matches it."""
    return f"{round(px * _PX_TO_PT, 2)}pt"


def _gap(px: float) -> str:
    """A CSS margin between two blocks of text, as Typst vertical space.

    A browser puts half the line's leading above and below every line box, so a
    2px CSS margin between two lines of text is really 2px plus a full leading
    of air. Typst's `#v` sits between the glyph boxes and adds none of it, so
    converting the margin alone renders visibly tighter than the CSS did.
    """
    return f"{_pt(px)} + {_LEADING}"


# Header fields a resume may reword for itself (models.EntryPick).
ENTRY_OVERRIDES = ("org", "role", "location", "start_date", "end_date", "detail")


def _resolve_entries(
    resume: dict[str, Any], entries: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], list[str], bool]]:
    """The resume's entries in print order, as (entry, bullet texts, show_detail).

    Each entry already carries the resume's header overrides, and each bullet
    its ``text_override``, so a template only has to typeset what it is given.
    """
    chosen_entries = (resume.get("selection") or {}).get("entries", [])
    entry_by_id = {entry["id"]: entry for entry in entries}
    bullet_by_id = {
        bullet["id"]: bullet for entry in entries for bullet in entry.get("bullets", [])
    }
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
    resolved = []
    for picked in render_entries:
        entry = entry_by_id.get(picked.get("entry_id"))
        if entry is None:
            continue
        entry = {
            **entry,
            **{k: picked[k] for k in ENTRY_OVERRIDES if picked.get(k) is not None},
        }
        bullets = []
        for picked_bullet in picked.get("bullets", []):
            bullet = bullet_by_id.get(picked_bullet.get("bullet_id"))
            if bullet is None:
                continue
            bullets.append(picked_bullet.get("text_override") or bullet["text"])
        resolved.append((entry, bullets, picked.get("show_detail", True)))
    return resolved


def build_typst_source(
    profile: dict[str, Any],
    resume: dict[str, Any],
    entries: list[dict[str, Any]],
    skills: list[dict[str, Any]],
) -> str:
    """Assemble the .typ document from the resume's selection."""
    if resume.get("template") == "barlow":
        return _barlow_source(profile, resume, entries, skills)
    selection = resume.get("selection") or {}
    chosen_skill_ids = set(selection.get("skills", []))

    sections: dict[str, list[str]] = {kind: [] for kind in SECTION_ORDER}
    for entry, texts, show_detail in _resolve_entries(resume, entries):
        bullets = [f"- {typst_escape(text)}" for text in texts]
        block = [_entry_header(entry, show_detail=show_detail)]
        if bullets:
            block.append(f"#v({_gap(3)})")
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
        parts.append(f"\n#v({_gap(8)})\n".join(blocks))
    skills_block = _skills_block(skills, chosen_skill_ids)
    if skills_block:
        parts.append("== Skills")
        parts.append(skills_block)
    return "\n\n".join(parts) + "\n"


def _preamble() -> str:
    # Paragraph and block spacing are both zeroed so that every gap in the
    # document is one written here. Typst's default
    # 1.2em is additive, so an explicit `#v(2px worth)` under a role line came
    # out as 12pt + 2.6pt. Both settings are needed -- `block.spacing` does not
    # cover paragraphs, which is what the skills groups and the detail line
    # are. With them at zero the `#v` calls *are* the margins, and headings
    # keep their own spacing through `above`/`below` below.
    #
    # Section headings are an uppercase, letter-spaced sans rule -- `#stack`
    # rather than a negative `#v`, so the gap under the text is an exact 2px
    # and not a number tuned by eye.
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
    """Name, contacts, availability, rule.

    ``availability_line`` belongs here, under the contacts, and not inside the
    Education entry: it is the line recruiters scan for.
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

    # A URL rides on the org itself rather than a trailing "(link)": same ink,
    # same text, still clickable.
    org_body = f"*{org}*" if org else ""
    if org_body and entry.get("url"):
        org_body = f'#link("{typst_string(entry["url"])}")[{org_body}]'
    left_top = f"#text(size: {_pt(9)})[{org_body}]" if org_body else ""
    right_top = f"#align(right)[#text(size: {_pt(7.8)})[{dates}]]" if dates else ""
    # The role is semibold italic; the location beside it is not.
    left_bottom = f"#text(size: {_pt(7.8)}, weight: 600)[_{role}_]" if role else ""
    right_bottom = f"#align(right)[#text(size: {_pt(7.8)})[{location}]]" if location else ""

    # The rows are text boxes (cap-height to baseline, like paragraph lines),
    # so the gutter carries the leading as `_gap` does for every other gap.
    # A bare px gutter put the role's caps on top of the org's descenders.
    rows = [f"  [{left_top}], [{right_top}],"]
    # A project usually has neither role nor location. An empty Typst grid row
    # still costs a row gutter, so omit it.
    if left_bottom or right_bottom:
        rows.append(f"  [{left_bottom}], [{right_bottom}],")
    header = (
        f"#grid(columns: (1fr, auto), row-gutter: {_gap(1)},\n"
        + "\n".join(rows)
        + "\n)"
    )
    # The detail is its own full-width line under the role, never run into the
    # role with an em dash.
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
    # Each group is its own paragraph with a 2px margin between -- not a `\`
    # linebreak, which would only give them a line gap.
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


# -- Barlow ------------------------------------------------------------------
#
# All-sans design on the vendored Barlow faces: condensed name and section
# heads, no rules anywhere, secondary text in two greys. Every value is read off
# the reference PDF (a Firefox print of the HTML original, US letter): sizes and
# colours from its text spans, gaps as baseline-to-baseline distances. Text
# boxes are zero-height (top and bottom edge both on the baseline), so each
# `#v`, row gutter and leading below *is* the distance between two baselines.
# Italics are Firefox's synthetic oblique: Barlow Regular sheared by 14 degrees,
# not the Barlow Italic face. Unlike "main", skills and coursework print in the
# order they were picked.

_B_INK = 'rgb("#1d1f20")'
_B_SOFT = 'rgb("#424244")'   # contacts, role, location
_B_MUTED = 'rgb("#5d5d60")'  # availability, dates, stack line
_B_BODY = "9.375pt"          # 12.5px
_B_SMALL = "9pt"             # availability, dates, stack line


def _barlow_link_label(url: str) -> str:
    """How a profile link prints: the address itself, minus scheme and www."""
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url).rstrip("/")


def _oblique(body: str, size: str, fill: str) -> str:
    return f"#text(size: {size}, fill: {fill}, oblique[{body}])"


def _barlow_source(
    profile: dict[str, Any],
    resume: dict[str, Any],
    entries: list[dict[str, Any]],
    skills: list[dict[str, Any]],
) -> str:
    esc = typst_escape
    dot = " · "

    contact = [
        esc(value)
        for value in (profile.get("email"), profile.get("phone"), profile.get("location"))
        if value
    ]
    links = [
        f'#link("{typst_string(link["url"])}")[{esc(_barlow_link_label(link["url"]))}]'
        for link in profile.get("links", [])
        if link.get("url")
    ]
    header = [
        '#align(center, text(font: "Barlow", stretch: 75%, weight: "semibold", size: 21pt,'
        f' tracking: 0.02em)[{esc(profile.get("full_name") or "Your Name")}])'
    ]
    gaps = iter(("16.5pt", "16.4pt"))
    for line in (dot.join(contact), dot.join(links)):
        if line:
            header.append(f"#v({next(gaps)})\n#align(center, text(fill: {_B_SOFT})[{line}])")
    availability = resume.get("availability_line", "")
    if availability:
        header.append(
            "#v(15.8pt)\n#align(center, "
            + _oblique(esc(availability), _B_SMALL, _B_MUTED)[1:]
            + ")"
        )

    sections: dict[str, list[str]] = {kind: [] for kind in SECTION_ORDER}
    for entry, texts, show_detail in _resolve_entries(resume, entries):
        sections[entry["kind"]].append(_barlow_entry(entry, texts, show_detail))

    # Picked order, not the bank's alphabetical order.
    skill_by_id = {skill["id"]: skill for skill in skills}
    picked = [
        skill_by_id[skill_id]
        for skill_id in (resume.get("selection") or {}).get("skills", [])
        if skill_id in skill_by_id
    ]
    courses = [esc(skill["name"]) for skill in picked if skill["category"] == "coursework"]
    if courses:
        # Rides on the last education block, closer to the degree line than
        # one entry sits to the next, at a tighter leading than the bullets.
        line = f"#block[#set par(leading: 14.1pt)\n*Relevant Coursework:* {', '.join(courses)}]"
        if sections["education"]:
            sections["education"][-1] += f"\n#v(18.5pt)\n{line}"
        else:
            sections["education"].append(line)

    body = []
    for kind in SECTION_ORDER:
        if sections[kind]:
            body.append(f"== {SECTION_TITLES[kind]}")
            # The original sets a project's first line 3.1pt lower under its
            # heading than an education entry's.
            if kind != "education":
                body.append("#v(3.1pt)")
            body.append("\n#v(21.5pt)\n".join(sections[kind]))

    groups = []
    for category, title in SKILL_GROUP_TITLES.items():
        names = [esc(skill["name"]) for skill in picked if skill["category"] == category]
        if names:
            groups.append(f"[*{title}*], [{', '.join(names)}],")
    if groups:
        body.append("== Skills")
        body.append(
            "#grid(columns: (112.7pt, 1fr), row-gutter: 15.55pt,\n  "
            + "\n  ".join(groups)
            + "\n)"
        )

    preamble = f"""\
#set page(paper: "us-letter", margin: (x: 45pt, top: 60.5pt, bottom: 36pt))
#set text(font: "Barlow", size: {_B_BODY}, fill: {_B_INK},
  top-edge: "baseline", bottom-edge: "baseline")
#set par(leading: 14.33pt, spacing: 0pt, justify: false)
#set block(spacing: 0pt)
#set strong(delta: 300)
#set list(tight: true, spacing: 14.83pt, indent: 4.05pt, body-indent: 4.8pt,
  // A zero-height marker, so the disc cannot push its line down; its
  // centre sits 2.73pt above the baseline, as the original's does.
  marker: box(width: 3.15pt, height: 0pt,
    place(dy: -4.305pt, circle(radius: 1.575pt, fill: {_B_INK}))))
#let oblique(body) = skew(ax: -14deg, reflow: false, body)
#show link: it => it
#show heading.where(level: 2): it => block(above: 26.2pt, below: 16.75pt,
  text(font: "Barlow", stretch: 75%, weight: "semibold", size: 9.75pt, tracking: 0.1em,
    upper(it.body)))"""
    return "\n\n".join([preamble, "\n".join(header), *body]) + "\n"


def _barlow_entry(entry: dict[str, Any], texts: list[str], show_detail: bool) -> str:
    esc = typst_escape
    org = f"*{esc(entry.get('org', ''))}*"
    if entry.get("url"):
        org = f'#link("{typst_string(entry["url"])}")[{org}]'
    dates = esc(_dates(entry).replace(" -- ", " – "))
    role, location = esc(entry.get("role", "")), esc(entry.get("location", ""))
    detail = esc(entry.get("detail", "")) if show_detail else ""

    # The date sits 0.4pt above the org's baseline in the original.
    cells = [f"[{org}], [#text(size: {_B_SMALL}, fill: {_B_MUTED}, baseline: -0.4pt)[{dates}]],"]
    if role or location:
        cells.append(
            f"[{_oblique(role, _B_BODY, _B_SOFT)}], [{_oblique(location, _B_BODY, _B_SOFT)}],"
        )
    block = [
        "#grid(columns: (1fr, auto), row-gutter: 14.1pt, align: (left, right),\n  "
        + "\n  ".join(cells)
        + "\n)"
    ]
    if detail:
        block.append(f"#v(13.55pt)\n{_oblique(detail, _B_SMALL, _B_MUTED)}")
    if texts:
        block.append("#v(16.25pt)")
        block.extend(f"- {esc(text)}" for text in texts)
    return "\n".join(block)


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
        pdf = typst.compile(source.encode("utf-8"), font_paths=[_FONT_DIR])
    except typst.TypstError as error:
        return None, 0, str(error).strip()
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))
    return pdf, pages, ""
