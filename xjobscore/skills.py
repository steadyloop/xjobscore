"""Resolve plain text against the skill canon in ``registry/skills.toml``.

One authority, two consumers: the pipeline labels a posting's description and the
resume workspace matches a job description, both through the same slugs, so a
resume skill and a posting requirement are finally the same vocabulary.

The vocabulary is closed. ``extract`` emits only slugs declared in the canon and
never infers: no stemming, no synonyms, no similarity. Matching is literal, which
drives false positives to zero and accepts false negatives in exchange — a term
whose spelling is not yet an alias is silently missed. That trade is deliberate.
A missed label is invisible and fixed by adding an alias; a wrong label quietly
corrupts every comparison downstream.

Skills whose display name has a non-technical English use are marked
``ambiguous`` in the canon; only their aliases resolve. ``Go`` is reachable
through ``golang`` but never through bare "go", and ``C`` — which no spelling
distinguishes from a letter or a grade — is never auto-labeled at all.

``extract`` expects plain text. Silver already flattens HTML with its own
``_plain_text``; keeping this module format-agnostic is what keeps it this small.
"""

from __future__ import annotations

import functools
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CATEGORIES", "Skill", "SkillsError", "SKILLS_PATH", "canon", "extract"]

SKILLS_PATH = Path(__file__).parent / "registry" / "skills.toml"

#: language = programming languages; stack = software you run; concept = things
#: you know. Category is a property of the skill, never of the posting, so it is
#: resolved here at read time rather than stored alongside a label.
CATEGORIES = ("language", "stack", "concept")

_FIELDS = {"name", "category", "aliases", "ambiguous"}
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*\Z")

# `\b` cannot express these boundaries: `+` is not a word character, so `\bc\+\+\b`
# fails to match "C++". Explicit lookaround keeps symbol-bearing names matchable
# while still rejecting "Rustacean" for `rust`.
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"


class SkillsError(ValueError):
    """The skill canon is malformed or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class Skill:
    slug: str
    name: str
    category: str
    aliases: tuple[str, ...]
    ambiguous: bool

    @property
    def patterns(self) -> tuple[str, ...]:
        """Lowercase spellings that resolve to this skill."""
        if self.ambiguous:
            return self.aliases
        return (self.name.lower(), *self.aliases)


def _entry(slug: object, row: object, path: Path) -> Skill:
    where = f"{path}: [{slug}]"
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise SkillsError(f"{path}: {slug!r} is not a lowercase hyphenated slug")
    if not isinstance(row, dict):
        raise SkillsError(f"{where} must be a TOML table")
    # Unknown keys fail loudly. A typo such as `alias =` would otherwise read as
    # an empty alias list and silently stop matching every spelling it named.
    unknown = sorted(set(row) - _FIELDS)
    if unknown:
        raise SkillsError(f"{where}: unknown key {unknown[0]!r}")

    name = row.get("name", slug)
    if not isinstance(name, str) or not name:
        raise SkillsError(f"{where}: name must be a non-empty string")

    category = row.get("category")
    if category not in CATEGORIES:
        raise SkillsError(f"{where}: category must be one of {CATEGORIES}, got {category!r}")

    aliases = row.get("aliases", [])
    if not isinstance(aliases, list):
        raise SkillsError(f"{where}: aliases must be an array")
    for alias in aliases:
        if not isinstance(alias, str) or not alias:
            raise SkillsError(f"{where}: aliases must be non-empty strings")
        if alias != alias.lower():
            raise SkillsError(f"{where}: alias {alias!r} must be lowercase")

    ambiguous = row.get("ambiguous", False)
    if not isinstance(ambiguous, bool):
        raise SkillsError(f"{where}: ambiguous must be a boolean")

    return Skill(
        slug=slug,
        name=name,
        category=category,
        aliases=tuple(aliases),
        ambiguous=ambiguous,
    )


def _load(path: Path) -> dict[str, Skill]:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SkillsError(f"{path}: {exc}") from exc

    skills = {}
    claimed: dict[str, str] = {}
    for slug, row in document.items():
        skill = _entry(slug, row, path)
        skills[skill.slug] = skill
        for pattern in skill.patterns:
            # Two skills answering to one spelling would make resolution depend
            # on alternation order rather than on the canon.
            owner = claimed.setdefault(pattern, skill.slug)
            if owner != skill.slug:
                raise SkillsError(f"{path}: {pattern!r} is claimed by both {owner} and {skill.slug}")
    if not skills:
        raise SkillsError(f"{path}: canon is empty")
    return skills


@functools.lru_cache(maxsize=1)
def canon() -> dict[str, Skill]:
    """The validated canon, keyed by slug. Read once, then cached."""
    return _load(SKILLS_PATH)


@functools.lru_cache(maxsize=1)
def _matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    """One alternation over every spelling, plus its spelling-to-slug map.

    A single pass costs the length of the text regardless of canon size, where a
    scan per skill would cost their product.
    """
    slugs = {pattern: skill.slug for skill in canon().values() for pattern in skill.patterns}
    # `re` alternation is ordered rather than longest-match, so a shorter
    # alternative listed first would claim the prefix of a longer name and strand
    # the rest: "numerical linear algebra" must not resolve through "algebra".
    alternatives = "|".join(re.escape(p) for p in sorted(slugs, key=len, reverse=True))
    return re.compile(f"{_LEFT}({alternatives}){_RIGHT}"), slugs


def extract(text: str) -> list[str]:
    """Canon slugs named in ``text``, deduplicated and sorted.

    Expects plain text. Every returned slug is in the canon; unrecognized terms
    are dropped rather than guessed at.
    """
    if not text:
        return []
    pattern, slugs = _matcher()
    return sorted({slugs[hit] for hit in pattern.findall(text.lower())})
