"""Boundary-safe pattern matching for parse-time field derivation.

`job_type` and `remote` are derived per vendor at ``parse()`` time, from that
vendor's own measured evidence in ``doc/raw_postings/``. This module owns only
the matching primitive those rule tables share; the rules themselves are
vendor-specific and live next to the adapter that uses them.

THE PATTERN DIALECT
-------------------
A rule pattern is a **bare lowercase alternation** and nothing else::

    "intern(s|ship|ships)?"          yes
    "new grads?|graduate program"    yes
    "\\m(intern|interns)\\M"           no -- Postgres boundaries, see below
    "\\b(intern)\\b"                   no -- redundant and weaker
    "Intern"                         no -- can never match; see below
    "^intern$"                       no -- anchors, see below

Two conventions make that work, and both are enforced by `matcher` rather than
left to a reader's discipline -- a pattern that violates either one fails no
differently from a pattern that simply does not occur in the corpus, and a
silent zero-match rule is the worst possible failure here.

**Boundaries are added by this module, never written into the pattern.**
`matcher` wraps every pattern in the lookaround below. The vendor surveys in
``doc/raw_postings/`` are written in Postgres regex, so their patterns carry
``\\m``/``\\M`` -- those are the *doc's* notation for the boundary this module
already supplies. Transcribing one verbatim double-wraps it and it matches
nothing. Strip them; keep what is between.

**Patterns are lowercase, and the subject is lowercased before matching**,
rather than compiling with ``re.IGNORECASE``. That is what makes the
lowercase-only lookaround character class correct by construction -- with
`IGNORECASE` the class would silently also mean ``[A-Z]``, and the boundary
would stop rejecting "International". The cost is that any uppercase character
in a pattern is dead weight that can never match, so it is rejected outright.

See ``doc/raw_postings/transcribing.md`` for the full doc-to-rule translation.
"""

from __future__ import annotations

import functools
import re

__all__ = ["hit", "matcher"]

# `\b` fails on symbol-bearing and non-Latin patterns ("C++", "实习"); explicit
# lookaround keeps every alias matchable while still rejecting a bare substring
# inside a longer word. Both sides are load-bearing: the trailing boundary is
# what stops "intern" matching "International" and "co-op" matching
# "Cooperation".
_LEFT = r"(?<![a-z0-9])"
_RIGHT = r"(?![a-z0-9])"

# Escape sequences are exempt from the lowercase rule -- `\S` and `\W` are
# distinct classes, not shoutier spellings of `\s` and `\w`.
_ESCAPE = re.compile(r"\\.")


def _reject(pattern: str) -> str | None:
    """Why `pattern` can never work here, or None if it is well formed.

    Ordered so the more specific diagnosis wins: `\\M` is also an uppercase
    character, and "strip the Postgres boundary" is the useful thing to say
    about it, not "lowercase it".
    """
    for token, hint in (
        (r"\m", r"Postgres word boundaries (\m, \M) are the doc's notation for the "
                "boundary matcher() already adds -- strip them"),
        (r"\M", r"Postgres word boundaries (\m, \M) are the doc's notation for the "
                "boundary matcher() already adds -- strip them"),
        (r"\b", r"\b is both redundant here and wrong on symbol-bearing aliases "
                "(it would not match 'c++') -- drop it"),
        (r"\B", r"\B is redundant here -- drop it"),
        ("(?i)", "inline flags are unsupported: the subject is lowercased before "
                 "matching, so the pattern must be lowercase instead"),
    ):
        if token in pattern:
            return f"{pattern!r}: {hint}"
    if pattern.startswith("^") or (pattern.endswith("$") and not pattern.endswith(r"\$")):
        return (f"{pattern!r}: anchors cannot work -- matcher() searches within a "
                "larger field value and wraps the pattern in a group, so ^/$ would "
                "bind to the whole string, not the alias")
    bare = _ESCAPE.sub("", pattern)
    if bare != bare.lower():
        return (f"{pattern!r}: patterns must be lowercase -- the subject is "
                "lowercased before matching, so an uppercase character can never "
                "match anything")
    return None


@functools.lru_cache(maxsize=None)
def matcher(pattern: str) -> re.Pattern[str]:
    """Compile one lowercase alternation, wrapped in explicit word boundaries.

    Raises `ValueError` on a pattern written in the wrong dialect. That is
    deliberately louder than the alternative: every such pattern is a rule that
    matches nothing, which is indistinguishable at runtime from a signal the
    vendor does not carry.
    """
    problem = _reject(pattern)
    if problem is not None:
        raise ValueError(problem)
    return re.compile(f"{_LEFT}({pattern}){_RIGHT}")


def hit(text: str | None, pattern: str) -> bool:
    """Whether ``pattern`` matches ``text`` on whole-word boundaries."""
    if not text:
        return False
    return matcher(pattern).search(text.lower()) is not None
