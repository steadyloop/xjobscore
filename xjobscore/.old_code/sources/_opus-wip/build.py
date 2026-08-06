#!/usr/bin/env python3
"""Flatten the per-section catalog tables into the requested numbered-list format."""
import re, pathlib, textwrap

SRC = pathlib.Path("/Users/mr.po/Documents/GitHub/xjobscore/xjobscore/sources/_opus-wip")

GROUPS = [
    ("01-general-aggregators.md",      "General Aggregators / APIs"),
    ("02-ats-platforms.md",            "ATS"),
    ("03-student-intern-platforms.md", "Student & Intern Platforms / GitHub Trackers"),
    ("04-startup-vc-ecosystem.md",     "Startup & VC Ecosystem"),
    ("05-bigtech-structured-programs.md", "Big Tech & Named Structured Programs"),
    ("07-nontech-fortune-a.md",        "Non-Tech Employers A — Retail, CPG, Healthcare, Pharma, Hospitality, Agriculture"),
    ("09-government-labs.md",          "Government, National Labs & Public Sector"),
]

SEP = re.compile(r"^\|[\s:|-]+\|$")

def clean(s):
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def first_sentence(s, cap):
    s = clean(s)
    if not s or s == "—":
        return ""
    # cut at sentence end, but don't cut inside a code span or a decimal
    m = re.search(r"(?<![0-9])\.(?:\s|$)", s)
    if m and m.start() < cap * 1.6:
        s = s[: m.start() + 1]
    if len(s) > cap:
        s = s[:cap].rsplit(" ", 1)[0] + "…"
    return s

def parse(path):
    """Yield (subgroup, cells-dict) for every data row of every table."""
    cols, sub = None, None
    for line in path.read_text().splitlines():
        st = line.strip()
        if st.startswith("### "):
            sub = re.sub(r"^###\s+[\d.]*\s*", "", st).strip()
            cols = None
            continue
        if st.startswith("## "):
            sub, cols = None, None
            continue
        if not st.startswith("|"):
            continue
        if SEP.match(st):
            continue
        cells = [clean(c) for c in st.strip("|").split("|")]
        low = [c.lower() for c in cells]
        if "source" in low[:1] or ("url" in low and "tier" in low):
            cols = {c: i for i, c in enumerate(low)}
            continue
        if cols is None:
            continue
        get = lambda k: cells[cols[k]] if k in cols and cols[k] < len(cells) else ""
        name = get("source") or cells[0]
        if not name or name in {"—", "-"}:
            continue
        yield sub, {
            "name": name,
            "url": get("url"),
            "access": get("access"),
            "auth": get("auth"),
            "cov": get("coverage"),
            "tier": get("tier"),
            "notes": get("notes"),
        }

out, stats = [], []
for fname, title in GROUPS:
    p = SRC / fname
    rows = list(parse(p))
    out.append(f"\n{title}\n---\n")
    n, cur = 0, None
    for sub, r in rows:
        if sub != cur:
            cur = sub
            if sub:
                out.append(f"\n*{sub}*\n")
        n += 1
        url = r["url"].strip("`").strip()
        url = "" if url in {"—", "-", "(retired)", ""} else f" — `{url}`"
        meta = "/".join(x for x in (r["access"], r["auth"]) if x and x != "—")
        tier = r["tier"] or "—"
        bits = [b for b in (first_sentence(r["cov"], 150), first_sentence(r["notes"], 190)) if b]
        tail = (" — " + " · ".join(bits)) if bits else ""
        out.append(f"{n}. **{r['name']}**{url} — `{tier}` {meta}{tail}\n")
    stats.append((title, n))

pathlib.Path("/private/tmp/claude-501/-Users-mr-po-Documents-GitHub-xjobscore/31d0ee3f-24fc-422d-ba81-11552dbdaaed/scratchpad/body.md").write_text("".join(out))
total = sum(n for _, n in stats)
for t, n in stats:
    print(f"{n:5d}  {t}")
print(f"{total:5d}  TOTAL")
