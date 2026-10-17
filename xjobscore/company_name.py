"""Extract an employer from a job description, with inspectable evidence.

Generic rules need no company catalogue, model, network, or database. An optional
caller-provided catalogue also recognizes verified names in unfamiliar wording.
Recognition and employer selection are separate: several companies can be named
in one description. Keep structured vendor employer fields ahead of this fallback.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from html.parser import HTMLParser
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from xjobscore.records import Feed, Posting

VERSION = "2"
MAX_DESCRIPTION_LENGTH = 200_000


@dataclass(frozen=True, slots=True)
class NameEvidence:
    name: str
    rule: str
    excerpt: str
    start: int
    end: int
    strength: Literal["strong", "supporting"]
    relation: Literal["employer", "client", "placement_employer", "parent"] = "employer"


@dataclass(frozen=True, slots=True)
class CompanyMention:
    """A named organization candidate, not necessarily the hiring employer.

    ``canonical_name`` is populated only by the caller's verified-name lookup.
    Generic named phrases are broad candidates and can include other entities;
    website hints are lexical clues, not declarations of a legal company name.
    """
    name: str
    start: int
    end: int
    rule: str
    context: str
    canonical_name: str | None = None
    employer_context: bool = False


@dataclass(frozen=True, slots=True)
class CompanyNameResult:
    name: str | None
    status: Literal["matched", "ambiguous", "not_found"]
    confidence: Literal["strong", "supporting", "none"]
    evidence: tuple[NameEvidence, ...] = ()
    reason: str = ""
    truncated: bool = False
    mentions: tuple[CompanyMention, ...] = ()


class _Text(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(description: str | None) -> str:
    """Preserve headings/paragraphs and ignore executable/stylesheet content."""
    if not description:
        return ""
    parser = _Text()
    parser.feed(description[:MAX_DESCRIPTION_LENGTH])
    parser.close()
    return "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip())


def _identity(name: str) -> str:
    value = unicodedata.normalize("NFKC", name).casefold()
    value = value.replace("&", " and ").replace("+", " and ")
    value = re.sub(r",?\s+(?:incorporated|inc\.?|llc|llp|lp|ltd\.?|limited|corp\.?|corporation|plc)\s*$", "", value)
    return " ".join(re.findall(r"[^\W_]+", value))


# Bounded name capture deliberately does not consume a whole paragraph. Capture
# terminators belong to each rule (comma, hiring verb, heading end, etc.).
NAME = r"(?P<name>(?:\b(?:Inc|Co|Ltd|Corp|St|Ste|[A-Z])\.(?=\s)|\.(?=\S)|(?<=\w):(?=\w)|[^\n:;.!?]){2,110}?)"
START = r"(?:^|(?<=[\n.!?])\s*)"
FLAGS = re.IGNORECASE | re.MULTILINE
RULES = (
    ("employer_label", re.compile(r"^\s*(?:Employer|Company|Company Name|Hiring Organization|Hiring Company)\s*:\s*" + NAME + r"\s*$", FLAGS), "strong", "employer"),
    ("employed_by", re.compile(r"\b(?:you|students|interns|candidates|employees|participants|successful applicants)\s+(?:will be|are)\s+employed by\s+" + NAME + r"(?=[.\n;]|,\s*(?:and|with|where)|$)", FLAGS), "strong", "placement_employer"),
    ("named_client", re.compile(r"\b(?:our|the) client\s*[:,\-]?\s+" + NAME + r"(?:,\s*[^\n.]{0,90},)?\s+(?:is|are)\s+(?:currently\s+)?(?:seeking|hiring|looking for|recruiting)\b", FLAGS), "strong", "client"),
    ("client_identity", re.compile(r"\b(?:our|the) client is\s+" + NAME + r"(?=[.\n;]|,\s*(?:a|an|the|who|which)\b|$)", FLAGS), "strong", "client"),
    ("client_label", re.compile(r"^\s*(?:Client|Client Company)\s*:\s*" + NAME + r"\s*$", FLAGS), "strong", "client"),
    ("hiring_subject", re.compile(START + NAME + r"(?:,\s*[^\n.]{0,110},)?\s+(?:is|are)\s+(?:(?:currently|actively|now|always|proudly)\s+)*(?:seeking|hiring|looking for|recruiting|accepting applications|inviting applications)\b", FLAGS), "strong", "employer"),
    ("at_company", re.compile(START + r"(?:Here at|At)\s+" + NAME + r",\s*(?:we|you|our|your)\b", FLAGS), "strong", "employer"),
    ("about_heading", re.compile(r"^\s*(?:About|Who is|Why work (?:at|for))\s+" + NAME + r"\s*:?\s*$", FLAGS), "supporting", "employer"),
    ("company_definition", re.compile(START + NAME + r"\s+(?:is|are)\s+(?:a|an|the|one of)\s+(?:(?:[\w'’/-]+)\s+){0,7}(?:company|corporation|organization|organisation|firm|provider|manufacturer|leader|bank|nonprofit|non-profit|network|agency|retailer|consultancy|university|health system)\b", FLAGS), "strong", "employer"),
    ("equal_opportunity_employer", re.compile(START + NAME + r"\s+(?:is|are)\s+(?:proud to be\s+)?(?:an?|the)\s+(?:equal[ -]opportunity|affirmative[ -]action)\s+employer\b", FLAGS), "strong", "employer"),
    ("join_company", re.compile(START + r"Join\s+(?:the team at\s+)?" + NAME + r"(?=,|\.|!|\n|\s+(?:and|to|as|where)\b|$)", FLAGS), "supporting", "employer"),
    ("interest_in_company", re.compile(r"\b(?:Thank you for your interest in|Welcome to|a career (?:at|with)|careers at|working at|employees at)\s+" + NAME + r"(?=[.\n!]|\s+(?:enjoy|means|will|can|work|are|have|receive|get|benefit)\b|$)", FLAGS), "supporting", "employer"),
    ("company_possessive", re.compile(START + NAME + r"['’]s\s+(?:team|internship program|summer internship|mission|employees|commitment)\b", FLAGS), "supporting", "employer"),
    ("hiring_action", re.compile(START + NAME + r"\s+(?:seeks|welcomes|invites|needs)\s+(?:(?:[\w'’/-]+)\s+){0,5}(?:interns?|students?|graduates?|applicants?|candidates?|applications?|apprentices?)\b", FLAGS), "strong", "employer"),
    ("job_opening", re.compile(START + NAME + r"\s+(?:has|have|offers?|is offering)\s+(?:(?:[\w'’/-]+)\s+){0,5}(?:openings?|opportunit(?:y|ies)|internships?|co-ops?|apprenticeships?)\b", FLAGS), "strong", "employer"),
    ("self_at_company", re.compile(r"\b(?:For us at|When you work at|Life at|Why choose|Why you belong at|Come work (?:at|for))\s+" + NAME + r"(?=,|[!\n]|\s*\?|\s+(?:means|is|offers|you)\b|$)", FLAGS), "supporting", "employer"),
    ("employee_context", re.compile(START + NAME + r"(?:['’]s)?\s+(?:employees|colleagues|staff|internship program|internships|policies|operations department)\b", FLAGS), "supporting", "employer"),
    ("internal_applicants", re.compile(r"^\s*CURRENT\s+" + NAME + r"\s+(?:EMPLOYEE|STAFF|STUDENT)\b", FLAGS), "supporting", "employer"),
    ("employer_address_label", re.compile(r"(?:Name and Address of Employer|Employer Name)\s*:\s*" + NAME + r"(?=,\s*\d|\n|$)", FLAGS), "strong", "employer"),
    ("employment_policy", re.compile(START + NAME + r"\s+(?:is|are)\s+(?:(?:[\w'’/-]+)\s+){0,4}(?:equal employment opportunity|equal opportunity employer|affirmative action employer)\b", FLAGS), "strong", "employer"),
    ("employment_commitment", re.compile(START + NAME + r"\s+(?:is committed to providing|provides|will provide|does not discriminate|will not discriminate)\s+(?:(?:[\w'’/-]+)\s+){0,5}(?:equal employment|equal opportunity|employment opportunities)\b", FLAGS), "strong", "employer"),
    ("organization_action", re.compile(START + NAME + r"\s+(?:manufactures|develops|designs|operates|specializes|specialises|connects|surrounds|applies|responds|serves|builds|delivers|offers|provides)\b", FLAGS), "supporting", "employer"),
    ("organization_history", re.compile(START + r"(?:For (?:more than|over|nearly)?\s*\d+ years,?\s*|Since \d{4},?\s*|Founded in \d{4},?\s*)?" + NAME + r"\s+has (?:been|become|built|grown|served|provided|delivered|aimed)\b", FLAGS), "supporting", "employer"),
    ("company_possessive_context", re.compile(START + NAME + r"['’]s\s+(?:[\w/-]+\s+){0,4}(?:program|office|culture|business|mission|team|employees|commitment|internships|operations|policy|policies)\b", FLAGS), "supporting", "employer"),
    ("brand_employment_possessive", re.compile(r"(?<!\w)(?P<name>[^\W\d_][\w./-]{1,45})['’]s\s+(?:recruiting|recruitment|employees|staff|careers|internship|policy|policies)\b", FLAGS), "supporting", "employer"),
    ("parent_company", re.compile(r"\b(?:owned by|a (?:subsidiary|business|division|affiliate|member) of)\s+" + NAME + r"(?=,|\n|\s*\(|\s+(?:and|is|has|which|with)\b|[.!]|$)", FLAGS), "supporting", "parent"),
    ("parent_company_attribution", re.compile(r"\(\s*(?:an?\s+)?" + NAME + r"\s+[Cc]ompany\s*\)", FLAGS), "supporting", "parent"),
    ("french_hiring", re.compile(START + NAME + r"\s+(?:recrute|recherche)\b", FLAGS), "supporting", "employer"),
    ("spanish_hiring", re.compile(START + NAME + r"\s+(?:busca|está buscando|esta buscando|está contratando)\b", FLAGS), "supporting", "employer"),
)

GENERIC_PREFIXES = {
    "we", "you", "us", "me", "them", "our", "your", "my", "their", "they", "this", "that", "these", "those", "it", "who", "what", "why", "how", "which",
    "if", "as", "with", "from", "for", "on", "in", "of", "to", "by", "and", "or", "please", "not", "never", "the successful", "the ideal",
    "the candidate", "the intern", "the internship", "the position", "the role", "the opportunity", "the application", "the pay", "the college", "the student", "the program", "the programme", "the team", "the company",
    "job description", "position summary", "company overview", "job summary", "about us", "about the", "about this", "about you",
    "responsibilities", "qualifications", "requirements", "benefits", "department", "location", "salary", "employment", "company", "supports", "support", "provides", "the tga intern", "ability", "alignment", "contribute", "perform", "is attracted", "pay range",
    "internship", "internships", "graduate", "student", "students", "employees", "candidates", "applicants", "team", "join",
    "one of", "a global", "a leading", "an equal", "equal opportunity", "work with", "work for", "apply", "learn", "discover", "help",
}

ABBREVIATION = re.compile(r"(?P<full>[A-Z][\w&+.’'/-]*(?:[ \t]+(?:and|of|the|for|de|[A-Z][\w&+.’'/-]*)){1,10})\s*\((?P<alias>[A-Z][A-Z0-9&-]{1,9})\)")
NON_COMPANY_ABBREVIATIONS = {"US", "USA", "UK", "CA", "DC", "NY", "TX", "EU", "EEO", "EOE", "ADA", "STEM", "GPA", "BA", "BS", "MA", "MS", "HR", "IT", "CEO", "CFO", "CPA", "COVID", "DEI", "FLSA"}


def _explicit_aliases(body: str) -> dict[str, str]:
    """Only aliases stated in this same description; never a name catalogue."""
    groups: dict[str, set[str]] = {}
    for match in ABBREVIATION.finditer(body):
        full = _clean_name(match.group("full"))
        alias = match.group("alias")
        if full and alias not in NON_COMPANY_ABBREVIATIONS:
            groups.setdefault(_identity(alias), set()).add(full)
    return {key: next(iter(values)) for key, values in groups.items() if len(values) == 1}


def _clean_name(raw: str) -> str | None:
    name = raw.strip(" \t\r\n:;,*•–—-!®™")
    name = re.sub(r"^(?:Here at|At|About|Join|The team at)\s+", "", name, flags=re.I)
    name = re.sub(r"\s*\([^)]{1,35}\)\s*$", "", name).strip()
    name = re.split(r",\s*(?:a|an|the|based|headquartered|one|which|established|founded|located|recognized|with|known|interns|students|employees|participants|staff)\b", name, maxsplit=1, flags=re.I)[0]
    name = name.rstrip("., ")
    key = _identity(name)
    if not key or len(name) > 100 or len(name.split()) > 11:
        return None
    if any(key == prefix or key.startswith(prefix + " ") for prefix in GENERIC_PREFIXES):
        return None
    if re.search(r"https?://|www\.|@|[<>={}]|\b(?:seeking|hiring|looking|must|should|will|would|you|we|your|our|including|responsibilities)\b", name, re.I):
        return None
    if not re.search(r"[^\W\d_]", name) or re.search(r"\b\d{4}\b", name):
        return None
    # A sentence/clause is not a proper employer name. Lowercase brands remain
    # valid when short (e.g. fairlife, candi solar, moss.ag).
    words = name.split()
    if len(words) > 3 and words[0][0].islower() and not any(c.isupper() for c in words[0]) and words[0] != "the":
        return None
    connectors = {"and", "of", "the", "for", "at", "in", "to", "with", "de", "la", "le", "du", "des", "von", "der", "da", "do", "del", "di", "d'", "inc", "llc", "llp", "lp", "ltd", "plc", "co"}
    if words[0][0].isupper() and any(w[0].islower() and not any(c.isupper() for c in w) and w.strip(".,").casefold() not in connectors for w in words[1:] if w):
        return None
    if re.search(r"\b(?:and|of|the|for|at|in|to|with|by|a|an)\s*$", name, re.I):
        return None
    return name


def _extract_explicit_name(description: str | None) -> CompanyNameResult:
    """Infer employer-bearing names. Unnamed clients/conflicting names abstain.

    Confidence describes rule strength, not a calibrated probability. Offsets
    refer to ``plain_text(description)``. Evidence never comes from a domain,
    a candidate company catalogue, or merely the first capitalized phrase.
    """
    body = plain_text(description)
    truncated = bool(description and len(description) > MAX_DESCRIPTION_LENGTH)
    if not body:
        return CompanyNameResult(None, "not_found", "none", reason="empty_description", truncated=truncated)
    evidence: list[NameEvidence] = []
    seen = set()
    for rule, pattern, strength, relation in RULES:
        for match in pattern.finditer(body):
            name = _clean_name(match.group("name"))
            if name is None:
                continue
            if rule == "company_definition" and re.search(r"\b(?:integral part of|part of (?:the|a)|department of|division of|team within)\b", body[match.end("name"):match.end()], re.I):
                continue
            # Reject negated/hypothetical mentions, and disallow a customer
            # description being promoted through a generic company definition.
            before = body[max(0, match.start() - 70):match.start()]
            if re.search(r"\b(?:not|never|example|e\.g\.|such as|including|experience (?:at|with))\s*$", before, re.I):
                continue
            marker = (_identity(name), rule, match.start("name"))
            if marker in seen:
                continue
            seen.add(marker)
            evidence.append(NameEvidence(name, rule, body[max(0, match.start() - 35):min(len(body), match.end() + 100)], match.start("name"), match.end("name"), strength, relation))
    if not evidence:
        return CompanyNameResult(None, "not_found", "none", reason="no_employer_bearing_statement", truncated=truncated)
    # A stated placement employer/client outranks the agency advertising it.
    specific = [e for e in evidence if e.relation in {"client", "placement_employer"}]
    employer_evidence = [e for e in evidence if e.relation != "parent"]
    pool = specific or employer_evidence or evidence
    if not specific and re.search(r"\b(?:our client|on behalf of (?:our|a) client|client of ours)\b", body, re.I):
        return CompanyNameResult(None, "ambiguous", "none", tuple(evidence), "unnamed_or_unconfirmed_client", truncated)
    strong = [e for e in pool if e.strength == "strong"]
    selected = strong or pool
    if any(re.search(r",.+\band\b", e.name, re.I) for e in selected):
        return CompanyNameResult(None, "ambiguous", "none", tuple(evidence), "multiple_named_employers_in_statement", truncated)
    groups: dict[str, list[NameEvidence]] = {}
    aliases = _explicit_aliases(body)
    for e in selected:
        full = aliases.get(_identity(e.name), e.name)
        groups.setdefault(_identity(full).removeprefix("the "), []).append(e)
    # Full/legal and shortened brands can coexist, but a mere substring cannot
    # collapse different employers (e.g. Foo vs Foo Foundation).
    if len(groups) != 1:
        return CompanyNameResult(None, "ambiguous", "none", tuple(evidence), "conflicting_employer_names", truncated)
    matches = next(iter(groups.values()))
    best = max(matches, key=lambda e: (e.strength == "strong", e.relation == "placement_employer", len(e.name)))
    full = aliases.get(_identity(best.name), best.name)
    if full != best.name:
        alias_match = next(m for m in ABBREVIATION.finditer(body) if _identity(m.group("alias")) == _identity(best.name) and _clean_name(m.group("full")) == full)
        evidence.append(NameEvidence(full, "explicit_abbreviation", body[max(0, alias_match.start() - 35):alias_match.end() + 100], alias_match.start("full"), alias_match.end("full"), "supporting", best.relation))
    return CompanyNameResult(full, "matched", best.strength, tuple(evidence), "employer_bearing_statement", truncated)


def _token_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return "".join(c for c in value if not unicodedata.combining(c))


def _tokens(body: str):
    for match in re.finditer(r"\w+|[&+]", body):
        raw = match.group()
        yield ("and" if raw in {"&", "+"} else _token_key(raw), match.start(), match.end())


class CompanyNameCatalog:
    """Reusable token trie of caller-provided verified names.

    No built-in company list or automatic DB lookup. Matching respects word
    boundaries, punctuation, accents, ampersands, and optional leading articles.
    It recognizes mentions; it cannot resolve parent/client/employer semantics.
    """
    def __init__(self, names: Iterable[str]):
        if isinstance(names, (str, bytes)):
            raise TypeError('known_names must be an iterable of company names, not one string')
        self._trie: dict = {}
        for name in names:
            if not isinstance(name, str) or not name.strip():
                continue
            tokens = tuple(t[0] for t in _tokens(name))
            variants = {tokens}
            if tokens and tokens[0] == "the" and len(tokens) > 1:
                variants.add(tokens[1:])
            for variant in tuple(variants):
                if variant and variant[-1] in {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "plc"} and len(variant) > 1:
                    variants.add(variant[:-1])
            for variant in variants:
                node = self._trie
                for token in variant:
                    node = node.setdefault(token, {})
                node.setdefault(None, set()).add((name.strip(), variant))

    def find(self, body: str) -> tuple[CompanyMention, ...]:
        tokens = list(_tokens(body))
        matches = []
        seen = set()
        for start, token in enumerate(tokens):
            node = self._trie
            for end in range(start, len(tokens)):
                node = node.get(tokens[end][0])
                if node is None:
                    break
                for canonical, variant in node.get(None, ()):
                    canonical_tokens = list(_tokens(canonical))
                    offset = 1 if canonical_tokens[0][0] == 'the' and variant[0] != 'the' else 0
                    gaps_valid = True
                    for n in range(end-start):
                        actual = body[tokens[start+n][2]:tokens[start+n+1][1]]
                        expected = canonical[canonical_tokens[offset+n][2]:canonical_tokens[offset+n+1][1]]
                        if any(c in actual and c not in expected for c in ';!?') or re.search(r'\n\s*\n', actual):
                            gaps_valid = False
                            break
                    if not gaps_valid:
                        continue
                    lo, hi = token[1], tokens[end][2]
                    marker = (canonical, lo, hi)
                    if marker not in seen:
                        seen.add(marker)
                        context = body[max(0, lo-80):hi+100]
                        matches.append(CompanyMention(body[lo:hi], lo, hi, "verified_name_lookup", context, canonical, _employer_context(body, lo, hi)))
        return tuple(sorted(matches, key=lambda m: (m.start, m.end, m.canonical_name or "")))


PHRASE_TOKEN = re.compile(r"[^\W_][\w.’'&+/:·!|-]*|[&+]", re.UNICODE)
CONNECTORS = {"and", "of", "the", "for", "at", "in", "on", "de", "la", "le", "du", "des", "von", "der", "da", "do", "del", "di"}
SECTION_WORDS = {"application", "instructions", "current", "employee", "employees", "student", "students", "intern", "interns", "internship", "internships", "position", "description", "responsibilities", "qualifications", "requirements", "minimum", "salary", "compensation", "benefits", "job", "summary", "overview", "role", "duties", "skills", "experience", "apply", "join", "thank", "why", "about", "learn", "please", "workday", "work", "manager", "supervisor", "required", "remote", "hybrid", "time", "part", "schedule", "closing", "date"}
ORG_ENDINGS = {"university", "college", "institute", "foundation", "association", "corporation", "company", "inc", "llc", "ltd", "bank", "hospital", "center", "centre", "agency", "group", "commission", "council", "authority", "department", "district", "schools", "services", "system", "systems", "technologies", "industries", "labs", "laboratories", "studios", "architects", "engineers", "health", "engineering", "energy", "insurance", "construction", "consulting", "manufacturing", "materials", "supply", "farms", "properties", "motors", "media", "outreach", "partners", "solutions", "trust", "capital", "finance", "design", "transport", "logistics", "foods", "medical", "solar"}


def _employer_context(body: str, start: int, end: int) -> bool:
    before = body[max(0, start-100):start]
    after = body[end:end+120]
    if re.search(r"\b(?:experience with|proficient in|knowledge of|familiar with|such as|customers include|clients include|products include|including)\s*$", before, re.I):
        return False
    return bool(re.search(r"\b(?:at|for|with|join|employer|company|about|work|working|team|employees?|internship|career|policy|employment)\b", before[-65:] + " " + after, re.I))


def _generic_mentions(body: str) -> tuple[CompanyMention, ...]:
    """Broad organization candidates, never auto-used as high-confidence names."""
    tokens = list(PHRASE_TOKEN.finditer(body))
    found = []
    i = 0
    while i < len(tokens):
        first = tokens[i]
        value = first.group().rstrip(".,")
        number_brand = bool(re.fullmatch(r'\d+[A-Za-z][\w-]*', value)) and not re.fullmatch(r'\d+(?:st|nd|rd|th)',value,re.I)
        if not (any(c.isupper() for c in value) or number_brand) or value.casefold() in SECTION_WORDS:
            i += 1
            continue
        group = [first]
        j = i + 1
        while j < len(tokens) and len(group) < 12:
            prev, nxt = tokens[j-1], tokens[j]
            gap = body[prev.end():nxt.start()]
            value = nxt.group().rstrip(".,")
            prev_value = prev.group().rstrip('.,').casefold()
            initial = len(prev_value) == 1
            abbreviation = prev_value in {'st','ste','inc','co','corp','ltd','jr','sr','dr','bros','const','u.s.a'} or initial or bool(re.fullmatch(r'(?:[a-z]\.){1,5}',prev.group().casefold()))
            internal_role = value.casefold() in {'intern','internship'} and (any(t.group().casefold() in ORG_ENDINGS for t in group) or (j+1<len(tokens) and tokens[j+1].group().casefold()=='group'))
            if gap.strip(' \t,®™|') or "\n" in gap or (prev.group().endswith('.') and not abbreviation) or (value.casefold() in SECTION_WORDS and not internal_role):
                break
            if not (any(c.isupper() for c in value) or value.casefold() in CONNECTORS or value.casefold() in ORG_ENDINGS or value in {"&", "+"}):
                break
            group.append(nxt)
            j += 1
        while len(group) > 1 and group[-1].group().casefold() in CONNECTORS:
            group.pop()
        lo, hi = group[0].start(), group[-1].end()
        raw = body[lo:hi].rstrip(".,")
        hi = lo + len(raw)
        name = _clean_mention(raw)
        if name:
            found.append(CompanyMention(name, lo, hi, "organization_phrase", body[max(0, lo-60):hi+80], employer_context=_employer_context(body, lo, hi)))
            # Preserve the full phrase and also expose the organization after
            # narrative cues glued to headings, e.g. "Who We Are Acme Labs".
            narrative = {'at','here','join','meet','welcome','support','assist','represent','develop','build','create','maintain','manage','collaborate','who','we',"we're",'we’re','are','us','founded'}
            offset = 0
            while offset<len(group)-1 and group[offset].group().rstrip('.,').casefold() in narrative:
                offset += 1
            if offset:
                begin = group[offset].start()
                clean = _clean_mention(body[begin:hi])
                if clean:
                    found.append(CompanyMention(clean, begin, hi, 'organization_after_narrative', body[max(0, begin-60):hi+80], employer_context=_employer_context(body, begin, hi)))
            # Organization designators reveal a named institution inside a
            # larger unit, e.g. "Ithaca College Wellness Clinic".
        for boundary in group[:-1]:
            if boundary.group().rstrip(".,").casefold() in ORG_ENDINGS:
                short = _clean_mention(body[lo:boundary.end()])
                if short:
                    found.append(CompanyMention(short, lo, boundary.end(), "organization_designator", body[max(0, lo-60):boundary.end()+80], employer_context=_employer_context(body, lo, boundary.end())))
        i = max(i+1, j)
    return tuple(found)


def _clean_mention(raw: str) -> str | None:
    """Candidate recognition permits legitimate names beginning Our/As/For.

    Broader than employer selection, and never automatically trusted as strong.
    """
    raw = raw.strip(' \t.,®™')
    if len(raw)>150 or len(raw.split())>14 or not re.search(r'[^\W\d_]',raw):
        return None
    if re.search(r'https?://|www\.|@|[<>=]',raw,re.I):
        return None
    if raw.casefold() in GENERIC_PREFIXES or raw.casefold() in SECTION_WORDS:
        return None
    return raw


def _website_hints(body: str) -> tuple[CompanyMention, ...]:
    """Expose name-like hosts/slugs without guessing a canonical employer."""
    pattern = re.compile(r"(?:https?://|www\.)[^\s<>\"']+|(?<![\w-])[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}(?:/[^\s<>\"']*)?", re.I)
    hints = []
    for match in pattern.finditer(body):
        url = match.group().rstrip('.,;)')
        try:
            parsed = urlsplit(url if '://' in url else 'https://' + url)
            host = parsed.hostname or ''
        except ValueError:
            continue
        if not host:
            continue
        host_start = body.find(host, match.start(), match.end())
        if host_start < 0:
            # urlsplit lowercases hosts; retain the original source spelling.
            host_start = body.casefold().find(host.casefold(), match.start(), match.end())
        if host_start < 0:
            continue
        labels = host.split('.')
        skip = {'www','www2','www3','jobs','careers','recruiting','apply','login','cdn'}
        brand = next((p for p in labels[:-1] if p not in skip), '')
        if brand and len(brand)>2:
            lo = host_start + host.find(brand)
            hints.append(CompanyMention(body[lo:lo+len(brand)], lo, lo+len(brand), 'website_name_hint', body[max(0, match.start()-45):match.end()+60]))
        # A branded top-level domain may also form part of the displayed name.
        lo = host_start + (4 if host.startswith('www.') else 0)
        hi = host_start + len(host)
        hints.append(CompanyMention(body[lo:hi], lo, hi, 'website_name_hint', body[max(0, match.start()-45):match.end()+60]))
        for segment in re.finditer(r'/([A-Za-z][A-Za-z-]{3,95})(?=/|$)', parsed.path):
            slug = segment.group(1)
            if '-' not in slug:
                continue
            lo = body.find(slug, match.start(), match.end())
            if lo>=0:
                hints.append(CompanyMention(slug, lo, lo+len(slug), 'website_name_hint', body[max(0, match.start()-45):match.end()+60]))
    return tuple(hints)


def extract_company_name(description: str | None, *, known_names: Iterable[str] | CompanyNameCatalog | None = None) -> CompanyNameResult:
    """Extract an employer and expose all name candidates for reconciliation.

    Generic extraction receives the description alone. Optional ``known_names``
    recognizes previously verified companies; this is lookup coverage, not
    proof of generalization to new names. Unknown/customer/parent mentions stay
    visible without being silently promoted to the hiring employer.
    """
    result = _extract_explicit_name(description)
    body = plain_text(description)
    mentions = [CompanyMention(e.name, e.start, e.end, e.rule, e.excerpt, employer_context=e.relation != "parent") for e in result.evidence]
    mentions.extend(_generic_mentions(body))
    mentions.extend(_website_hints(body))
    catalogue = known_names if isinstance(known_names, CompanyNameCatalog) else CompanyNameCatalog(known_names) if known_names is not None else None
    verified = catalogue.find(body) if catalogue is not None else ()
    mentions.extend(verified)
    unique = {(m.name, m.start, m.end, m.rule, m.canonical_name): m for m in mentions}
    result = replace(result, mentions=tuple(sorted(unique.values(), key=lambda m: (m.start, m.end, m.rule, m.name))))
    # Sentence punctuation inside a brand can otherwise leave the generic
    # noun as the subject ("Bang Zoom! Studios" -> "Studios"). Recover the
    # containing organization phrase; a bare institution type is insufficient.
    if result.status=='matched' and _identity(result.name or '').removeprefix('the ') in ORG_ENDINGS:
        expansions = {}
        for evidence in result.evidence:
            if _identity(evidence.name).removeprefix('the ')!=_identity(result.name or '').removeprefix('the '):
                continue
            for mention in mentions:
                if mention.rule in {'organization_phrase','organization_after_narrative'} and mention.start<evidence.start and mention.end==evidence.end:
                    expansions[mention.name]=mention
        if len(expansions)==1:
            mention=next(iter(expansions.values()))
            extra=NameEvidence(mention.name,'organization_phrase_extension',mention.context,mention.start,mention.end,'supporting')
            result=replace(result,name=mention.name,evidence=(*result.evidence,extra))
        elif not any(e.rule in {'employer_label','client_label'} for e in result.evidence):
            result=replace(result,name=None,status='not_found',confidence='none',reason='unqualified_organization_type')
    if result.status == "matched" and verified:
        same = {m.canonical_name for m in verified if _identity(m.name).removeprefix("the ") == _identity(result.name or "").removeprefix("the ")}
        if len(same) == 1:
            result = replace(result, name=next(iter(same)))
    elif result.status == "not_found" and re.search(r'\b(?:our client|on behalf of (?:our|a) client|client of ours)\b',body,re.I):
        result=replace(result,status='ambiguous',reason='unnamed_or_unconfirmed_client')
    elif result.status == "not_found" and verified:
        eligible = {m.canonical_name for m in verified if m.employer_context}
        if len(eligible) == 1:
            result = replace(result, name=next(iter(eligible)), status="matched", confidence="supporting", reason="verified_name_in_employer_context")
        elif len(eligible) > 1:
            result = replace(result, status="ambiguous", reason="multiple_verified_names_in_employer_context")
    return result


def extract_company_names(description: str | None, *, known_names: Iterable[str] | CompanyNameCatalog | None = None) -> tuple[CompanyMention, ...]:
    """All observed organization candidates, including ambiguous descriptions."""
    return extract_company_name(description, known_names=known_names).mentions


@lru_cache(maxsize=8)
def _configured_catalogue(names: tuple[str, ...]) -> CompanyNameCatalog:
    return CompanyNameCatalog(names)


def enrich_posting(feed: Feed, posting: Posting) -> Posting:
    """Feed opt-in: record suggestions, optionally fill missing/slug-only names.

    ``company_from_description='suggest'`` only records evidence;
    ``company_from_description='fallback'`` also uses a strong unambiguous name
    when no native employer is present. Existing verified feeds and explicit
    company_raw_spec routing remain authoritative. Default is disabled.
    """
    mode = feed.config.get("company_from_description")
    if not isinstance(mode,str) or mode not in {"suggest", "fallback"} or not posting.description_html:
        return posting
    names = feed.config.get('company_description_known_names')
    if names is not None and (not isinstance(names, list) or not all(isinstance(n,str) for n in names)):
        raise ValueError('company_description_known_names must be a list of verified names')
    catalogue = _configured_catalogue(tuple(names)) if names is not None else None
    result = extract_company_name(posting.description_html, known_names=catalogue)
    extra = {**posting.source_extra, "company_name_from_description": {"version": VERSION, **asdict(result)}}
    raw = posting.company_raw
    may_fill = (
        mode == "fallback" and result.status == "matched" and result.confidence == "strong" and not result.truncated
        and "company_raw_spec" not in feed.config
        and (feed.company_id is None or feed.company_needs_fill)
        and (not raw or raw == feed.key)
    )
    return replace(posting, source_extra=extra, company_raw=result.name if may_fill else raw)


def cli() -> None:
    """Read one plain-text/HTML description on stdin and return evidence JSON."""
    parser = argparse.ArgumentParser(description='Extract employer names and reviewable organization candidates.')
    parser.add_argument('--known-names', type=Path, help='Optional JSON list of previously verified company names; lookup mode is reported explicitly.')
    args = parser.parse_args()
    names = json.loads(args.known_names.read_text()) if args.known_names else None
    print(json.dumps(asdict(extract_company_name(sys.stdin.read(MAX_DESCRIPTION_LENGTH + 1), known_names=names)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli()
