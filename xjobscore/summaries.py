"""Deterministic, extractive summaries of student-role descriptions.

The extractor never invents prose.  It identifies role and qualification
sections, removes administrative boilerplate, and chooses a complementary set
of source sentences whose combined length fits the Opportunities detail pane.
When the source does not contain credible role evidence it returns ``None``
instead of manufacturing a plausible-looking summary.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re

__all__ = ["MAX_WORDS", "MIN_WORDS", "extract", "word_count"]


MIN_WORDS = 40
TARGET_WORDS = 45
MAX_WORDS = 50
_MAX_CANDIDATES = 14
_MAX_SELECTION = 5

_WORD = re.compile(r"[a-z0-9+#.]+", re.IGNORECASE)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9])")
_LEADING_BULLET = re.compile(r"^[•●▪‣*\-–—\xad]+\s*")
_METADATA_LINE = re.compile(
    r"^(?:where|when|duration|location|link\[\d+\])\s*:",
    re.IGNORECASE,
)

_ROLE_HEADING = re.compile(
    r"^(?:(?:the|about the|your) )?role(?: overview)?$|"
    r"^(?:(?:essential duties and |position |core |key |primary |essential )?responsibilities|"
    r"deine aufgaben)$|"
    r"^(?:what|how) you(?:’|'| wi)?ll (?:do|contribute|make an impact|achieve)$|"
    r"^what you will (?:do|be doing)$|^the impact you will have$|"
    r"^the day.to.day$|^duties$|^your impact$|^you will$|^at .+ you will$|"
    r"^as (?:a|an|the) .+ you will$|^the residency$|^.+ program outline$"
)
_QUALIFICATION_HEADING = re.compile(
    r"^(?:basic|minimum|required|preferred|desired|additional)? ?qualifications"
    r"(?: . desired skills)?$|"
    r"^(?:minimum|additional)? ?requirements$|^required$|^dein profil$|"
    r"^(?:required|preferred|desired) skills(?: . experience| and experience)?$|^skills$|"
    r"^what (?:we(?:’|'| a)?re looking for|we look for|we require|"
    r"you(?:’|'| wi)?ll (?:bring|need)|you bring|we value|"
    r"(?:would )?make you (?:a )?great fit)$|"
    r"^(?:who|about) you(?: are)?$|^who we are looking for$|^you have$|"
    r"^skills you(?:’|')ll need to bring$|^what will help you on the job$|^experience$"
)
_CONTEXT_HEADING = re.compile(
    r"^(?:overview|about (?:the position|our .+ program)|job description|the team|"
    r"position summary|program descriptions?|team descriptions?|"
    r"internship opportunities|.+ (?:program|internship|internship/co.op)|"
    r"summer internship|summer/fall co.op)$"
)
_EXCLUDED_HEADING = re.compile(
    r"^(?:who we are|our (?:values|mission)|don't work here|"
    r"benefits(?: and perks)?|compensation(?: and benefits)?|what we offer|"
    r"what.s in it for you|logistics|"
    r"pay range(?: transparency)?|annual salary range|"
    r"equal (?:employment )?opportunity(?: employer)?|equal opportunity .+|"
    r"voluntary self.identification|reasonable accommodation|"
    r"california privacy notice|applicant privacy notice|"
    r"how to (?:apply|identify fake job advertisements)|"
    r"a note on ai|what else you should know|eeo.statement)$"
)

_ACTION = re.compile(
    r"\b(?:build|develop|design|implement|research|analy[sz]e|test|support|"
    r"collaborate|create|write|manage|lead|prototype|evaluate|deliver|maintain|"
    r"improve|integrate|deploy|debug|perform|assist|contribute|own|operate|"
    r"investigate|produce|coordinate|provide|ship|help|learn|explore|cover|"
    r"gain|participate|focus|prepare|review|handle|resolve|run|organize|document)\b",
    re.IGNORECASE,
)
_ACTION_START = re.compile(
    r"^(?:build|develop|design|implement|research|analy[sz]e|test|support|"
    r"collaborate|create|write|manage|lead|prototype|evaluate|deliver|maintain|"
    r"improve|integrate|deploy|debug|perform|assist|contribute|own|operate|"
    r"investigate|produce|coordinate|provide|ship|help|learn|explore|cover|"
    r"gain|participate|focus|prepare|review|handle|resolve|run|organize|document)\b",
    re.IGNORECASE,
)
_ROLE_ACTION = re.compile(
    r"\b(?:build|develop|design|implement|research|analy[sz]e|test|support|"
    r"collaborate|create|write|manage|lead|prototype|evaluate|deliver|maintain|"
    r"improve|integrate|deploy|debug|perform|assist|contribute|own|operate|"
    r"investigate|produce|coordinate|provide|ship|help|prepare|work|handle|"
    r"resolve|run|organize|document)\b",
    re.IGNORECASE,
)
_GERUND_ACTION = re.compile(
    r"\b(?:building|developing|designing|implementing|researching|analyzing|"
    r"testing|supporting|collaborating|creating|writing|managing|leading|"
    r"evaluating|delivering|maintaining|improving|integrating|deploying|"
    r"debugging|assisting|contributing|operating|investigating|producing|"
    r"coordinating|providing|shipping|helping|preparing|handling|resolving|"
    r"running|organizing|documenting)\b",
    re.IGNORECASE,
)
_ROLE_SUBJECT = re.compile(
    r"\b(?:you|intern|co.op|fellow|apprentice|resident|the role|this role|"
    r"the position|this position|participants?|the program|this program|"
    r"duties|responsibilities)\b",
    re.IGNORECASE,
)
_ACTION_WORDS = frozenset(
    "build develop design implement research analyze analyse test support collaborate "
    "create write manage lead prototype evaluate deliver maintain improve integrate "
    "deploy debug perform assist contribute own operate investigate produce coordinate "
    "provide ship help learn explore cover gain participate focus prepare review handle "
    "resolve run organize document work serve".split()
)
_CORE_ACTION_WORDS = _ACTION_WORDS - {
    "learn", "explore", "cover", "gain", "participate", "focus", "review"
}
_STUDENT = re.compile(
    r"\b(?:student|undergraduate|graduate|graduat(?:e|ing|ion)|enrolled|degree|"
    r"intern|co.op|bachelor|master|phd|university|college|gpa|sophomore)\b",
    re.IGNORECASE,
)
_REQUIREMENT = re.compile(
    r"\b(?:must|required|requirement|experience|proficien|knowledge|ability|"
    r"eligible|currently pursuing|preferred|qualification|clearance|authorization|"
    r"familiarity|understanding)\b",
    re.IGNORECASE,
)
_DIRECT_QUALIFICATION = re.compile(
    r"^(?:you |candidates? |applicants? |currently |pursuing |enrolled |"
    r"bachelor|master|phd|gpa|experience |familiarity |knowledge |ability |"
    r"proficien|must |required )",
    re.IGNORECASE,
)
_HARD_REJECT = re.compile(
    r"equal opportunity|without regard to|reasonable accommodation|protected veteran|"
    r"self.identification|privacy notice|medical, dental|401\(k\)|paid parental|"
    r"salary range|pay range|compensation range|actual compensation packages|"
    r"disability status|race,? color|click here|submit your application|"
    r"application (?:deadline|process)|this application is|complete your application|"
    r"candidates apply once|ready to apply|conversion process|equal pay act|"
    r"fake job|questionnaire|after you apply|^benefits?:|physical requirements|"
    r"background check|drug test|"
    r"anniversary|thriving in.office culture",
    re.IGNORECASE,
)
_MARKETING = re.compile(
    r"\b(?:our mission|we believe|we are passionate|world.s (?:best|leading)|"
    r"fastest.growing|award.winning|join us|come and join|our values|founded in|headquartered|"
    r"generational company|culture of high velocity|elite team|bold thinkers|"
    r"wired to make an impact|place for complacency|expectations are high|"
    r"office experience is intentional|high.performing, fast.moving team)\b",
    re.IGNORECASE,
)
_GENERIC = re.compile(
    r"^(?:(?:excellent|strong) (?:written and verbal )?communication skills|"
    r"other duties as assigned|perform other duties as assigned|"
    r"we are recruiting for multiple positions|we want all groups to feel empowered)",
    re.IGNORECASE,
)
_APPLICATION_COPY = re.compile(
    r"\b(?:apply|application|recruiter|interview process|offer process|stay in touch)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "and the for with you your this that from into will are our their have has job "
    "role work position intern internship co op to of in on a an as is be by or".split()
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    text: str
    index: int
    section: str
    kind: str
    score: int
    credible_role: bool

    @property
    def words(self) -> int:
        return word_count(self.text)


def word_count(value: str) -> int:
    """Count display words using the same whitespace rule as the length budget."""
    return len(value.split())


def _normalized_heading(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip(" \t:.-–—")


def _heading_kind(value: str) -> str | None:
    normalized = _normalized_heading(value)
    if not normalized or len(normalized.split()) > 10 or len(normalized) > 100:
        return None
    if _ROLE_HEADING.fullmatch(normalized):
        return "role"
    if _QUALIFICATION_HEADING.fullmatch(normalized):
        return "qualification"
    if _CONTEXT_HEADING.fullmatch(normalized):
        return "context"
    if _EXCLUDED_HEADING.fullmatch(normalized):
        return "excluded"
    if normalized.startswith("about ") and "role" in normalized:
        return "role"
    if (
        normalized.startswith("about ")
        and not any(word in normalized for word in ("role", "position", "you", "program"))
    ):
        return "excluded"
    return None


def _looks_like_heading(value: str) -> bool:
    stripped = value.strip(" :.-–—")
    words = stripped.split()
    if not words or len(words) > 8 or len(stripped) > 90:
        return False
    return (
        value.rstrip().endswith(":")
        or stripped.isupper()
        or (
            len(words) <= 5
            and not value.rstrip().endswith((".", "!", "?"))
            and sum(word[:1].isupper() for word in words) >= len(words) - 1
        )
    )


def _tokens(value: str) -> set[str]:
    terms: set[str] = set()
    for raw_word in _WORD.findall(value):
        word = raw_word.casefold()
        if len(word) < 3 or word in _STOPWORDS:
            continue
        terms.add(word)
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            terms.add(word[:-1])
    return terms


def _title_terms(title: str) -> set[str]:
    return _tokens(title)


def _similar(left: _Candidate, right: _Candidate) -> bool:
    first, second = _tokens(left.text), _tokens(right.text)
    if not first or not second:
        return False
    overlap = len(first & second) / min(len(first), len(second))
    return overlap >= 0.5


def _candidate(
    text: str,
    *,
    index: int,
    section: str,
    title_terms: set[str],
) -> _Candidate | None:
    text = " ".join(_LEADING_BULLET.sub("", text).split()).strip(" •")
    count = word_count(text)
    if count < 5 or count > MAX_WORDS or text.endswith(":") or _HARD_REJECT.search(text):
        return None

    tokens = _tokens(text)
    overlap = len(tokens & title_terms)
    gerund_action = bool(_GERUND_ACTION.search(text))
    action = bool(_ACTION.search(text)) or gerund_action or bool(tokens & _ACTION_WORDS)
    core_action = (
        bool(_ROLE_ACTION.search(text))
        or gerund_action
        or bool(tokens & _CORE_ACTION_WORDS)
    )
    first_word = _WORD.match(text)
    action_start = (
        bool(_ACTION_START.search(text) or _GERUND_ACTION.match(text))
        or bool(first_word and _tokens(first_word.group()) & _CORE_ACTION_WORDS)
    )
    credible_role = (section == "role" and core_action) or (
        section != "qualification"
        and core_action
        and (
            action_start
            or bool(_ROLE_SUBJECT.search(text))
            or overlap >= 1
        )
    ) or (
        section == "context"
        and action
        and (
            bool(_ROLE_SUBJECT.search(text))
            or action_start
        )
    )
    direct_qualification = section == "qualification" or (
        bool(_REQUIREMENT.search(text))
        and (bool(_DIRECT_QUALIFICATION.search(text)) or bool(_STUDENT.search(text)))
    )

    if credible_role:
        kind = "role"
    elif direct_qualification:
        kind = "qualification"
    else:
        kind = "context"

    score = {"role": 30, "qualification": 20, "context": 6}.get(section, 0)
    if credible_role:
        score += 14
    if direct_qualification:
        score += 9
    if _STUDENT.search(text):
        score += 7
    score += min(6, overlap * 2)
    if 8 <= count <= 28:
        score += 4
    elif count > 38:
        score -= 3
    if _MARKETING.search(text):
        score -= 18
    if _GENERIC.search(text):
        score -= 16
    if _APPLICATION_COPY.search(text):
        score -= 12
    if text[:1].islower():
        score -= 5
    return _Candidate(text, index, section, kind, score, credible_role)


def _candidates(title: str, description: str) -> list[_Candidate]:
    section = "general"
    title_words = _title_terms(title)
    candidates: list[_Candidate] = []
    index = 0

    for raw_line in description.splitlines():
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        if _METADATA_LINE.match(line):
            continue

        heading = _heading_kind(line)
        remainder = ""
        if heading is None and ":" in line:
            prefix, possible_remainder = line.split(":", 1)
            heading = _heading_kind(prefix)
            if heading is not None:
                remainder = possible_remainder.strip()
        if heading is not None:
            section = heading
            if not remainder or section == "excluded":
                continue
            line = remainder
        elif _looks_like_heading(line):
            section = "general"
            continue

        if section == "excluded":
            continue
        for sentence in _SENTENCE_BOUNDARY.split(line):
            candidate = _candidate(
                sentence,
                index=index,
                section=section,
                title_terms=title_words,
            )
            index += 1
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _selection_score(selected: tuple[_Candidate, ...], words: int) -> int:
    kinds = {candidate.kind for candidate in selected}
    role_words = sum(candidate.words for candidate in selected if candidate.credible_role)
    # Average rather than sum: once the word target is met, a stack of short
    # generic qualification bullets must not beat two strong role sentences.
    score = round(sum(candidate.score for candidate in selected) / len(selected))
    score += 35 if "role" in kinds else 0
    score += 6 if "qualification" in kinds else 0
    score += min(20, role_words // 2)
    score += 6 if any(candidate.section == "role" for candidate in selected) else 0
    score -= abs(words - TARGET_WORDS)
    score -= max(0, len(selected) - 3) * 3
    return score


def _best_selection(candidates: list[_Candidate]) -> tuple[_Candidate, ...] | None:
    ranked = [
        item
        for item in sorted(candidates, key=lambda item: (-item.score, item.index))
        if item.score >= 5
    ][:_MAX_CANDIDATES]
    best: tuple[int, tuple[int, ...], tuple[_Candidate, ...]] | None = None
    for size in range(1, min(_MAX_SELECTION, len(ranked)) + 1):
        for selected in combinations(ranked, size):
            words = sum(candidate.words for candidate in selected)
            if not MIN_WORDS <= words <= MAX_WORDS:
                continue
            if not any(candidate.credible_role for candidate in selected):
                continue
            if sum(candidate.words for candidate in selected if candidate.credible_role) < 12:
                continue
            if any(_similar(left, right) for left, right in combinations(selected, 2)):
                continue
            ordered = tuple(sorted(selected, key=lambda item: item.index))
            key = (
                _selection_score(selected, words),
                tuple(-candidate.index for candidate in ordered),
                ordered,
            )
            if best is None or key[:2] > best[:2]:
                best = key
    return best[2] if best else None


def _render(selected: tuple[_Candidate, ...]) -> str:
    parts: list[str] = []
    for candidate in selected:
        text = candidate.text.strip()
        if text[-1:] not in ".!?":
            text += "."
        parts.append(text)
    return " ".join(parts)


def extract(title: str, description: str | None) -> str | None:
    """Return a faithful 40–50 word role summary, or ``None`` when unsafe.

    A genuinely short source is returned unchanged: padding it to forty words
    would be generation rather than extraction.  Longer sources must yield a
    40–50 word selection containing credible evidence about the work itself.
    """
    normalized = "\n".join(
        " ".join(line.split()) for line in (description or "").splitlines()
        if line.strip()
    ).strip()
    if not normalized:
        return None
    source_words = word_count(normalized)
    candidates = _candidates(title, normalized)
    if source_words <= MAX_WORDS:
        if _HARD_REJECT.search(normalized) or not any(item.credible_role for item in candidates):
            return None
        return normalized
    selected = _best_selection(candidates)
    return _render(selected) if selected else None
