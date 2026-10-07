"""Normalize free-text job locations into country, state, and city records.

The public API is :func:`normalize_location`.  The module also retains the
original JSONL command-line contract so corpus analysis can use the same code
as the application.
"""

import fileinput
import json
import os
import pickle
import re
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path

from geonamescache import GeonamesCache

__all__ = ["normalize_location"]


def clean(value):
    value = "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", value).strip()


FIELDS = "country state city".split()
NOISE = set("remote hybrid virtual multiple locations office hq headquarters supercenter supercentre neighborhood market store or and area central opportunity sud trion i".split())


def response(rows, remote):
    return {"locations": [dict(zip(FIELDS, row)) for row in rows], "remote": remote}


def add(mapping, key, value):
    mapping.setdefault(clean(key), []).append(value)


geo = GeonamesCache(min_city_population=500)


def _cache_path() -> Path:
    """Return a writable runtime cache path, never a path inside the package."""
    if configured := os.environ.get("XJOBSCORE_LOCATION_CACHE"):
        return Path(configured).expanduser()
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "xjobscore" / "location-normalizer.v4.pkl"


cache = _cache_path()
try:
    cities, countries, _ = pickle.loads(cache.read_bytes())
except (FileNotFoundError, OSError, EOFError, pickle.UnpicklingError, ValueError):
    cities, countries = {}, {}
    for city in geo.get_cities().values():
        canonical = clean(city["name"])
        place = city["countrycode"], city["admin1code"], city["name"], city["population"]
        for alias in (city["name"], *city["alternatenames"]):
            alias = clean(alias)
            if set(alias.split()) < set(canonical.split()) and canonical.split()[:len(alias.split())] != alias.split():
                continue
            cities.setdefault(alias, []).append((*place, alias == canonical))
    for code, country in geo.get_countries().items():
        for alias in (country["name"], country["iso"], country["iso3"]):
            countries[clean(alias)] = code
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(pickle.dumps((cities, countries, {}), pickle.HIGHEST_PROTOCOL))
    except OSError:
        # A read-only home/cache directory must not make normalization unusable.
        pass

country_data = geo.get_countries()
countries.update({name[4:]: code for code, country in country_data.items()
                  if (name := clean(country["name"])).startswith("the ")})
countries.update({
    "prc": "CN", "korea": "KR", "republic of korea": "KR", "korea republic of": "KR", "south korea": "KR",
    "turkiye": "TR", "uae": "AE", "u s": "US", "united states of america": "US",
})
country_capitals = {code: clean(country["capital"]) for code, country in country_data.items()}
country_abbreviations = {clean(country[key]) for country in country_data.values() for key in ("iso", "iso3")}
country_abbreviations |= {"prc", "u s"}

for alias, encoded in {
    "apodaca": "MX|19|Apodaca|1", "bangpa in": "TH|36|Bang Pa-in|20484",
    "carole park": "AU|04|Carole Park|1", "colonsay": "CA|11|Colonsay|1",
    "gift city": "IN|09|Gandhinagar|1", "gonfreville l orcher": "FR|28|Gonfreville-l'Orcher|1",
    "hinjewadi": "IN|16|Pune|1", "hsinchu city": "TW|04|Hsinchu|1", "hume": "AU|01|Hume|1",
    "kanata": "CA|08|Kanata|1", "lousado": "PT|04|Lousado|1", "pangyo": "KR|13|Seongnam|1",
    "pluakdaeng": "TH|47|Pluak Daeng|1", "rusayl": "OM|06|Rusayl|1",
    "rwc": "US|CA|Redwood City|1", "sara maria": "SR|17|Sara Maria|1",
    "sergeicikai i": "LT|57|Sergeičikai I|1", "sf": "US|CA|San Francisco|1",
    "starbase": "US|TX|Starbase|1", "tomago": "AU|02|Tomago|1", "viera": "US|FL|Viera|1",
}.items():
    country, state, city, population = encoded.split("|")
    add(cities, alias, (country, state, city, int(population), True))

states = {}
for code, state in GeonamesCache().get_us_states().items():
    for alias in (code, state["name"]):
        add(states, alias, ("US", code, code))
regions = {
    "CA": dict(x.split(":", 1) for x in "AB:Alberta|BC:British Columbia|MB:Manitoba|NB:New Brunswick|NL:Newfoundland and Labrador|NS:Nova Scotia|NT:Northwest Territories|NU:Nunavut|ON:Ontario|PE:Prince Edward Island|QC:Quebec|SK:Saskatchewan|YT:Yukon".split("|")),
    "AU": dict(x.split(":", 1) for x in "ACT:Australian Capital Territory|NSW:New South Wales|NT:Northern Territory|QLD:Queensland|SA:South Australia|TAS:Tasmania|VIC:Victoria|WA:Western Australia".split("|")),
}
for country, names in regions.items():
    for code, name in names.items():
        for alias in (code, name):
            add(states, alias, (country, code, None))
for alias, admin in {
    "BY": "02", "Bavaria": "02", "Bayern": "02", "BW": "01", "Baden-Württemberg": "01",
    "BE": "16", "Berlin": "16", "BB": "11", "Brandenburg": "11", "HE": "05", "Hesse": "05",
    "Hessen": "05", "NDS": "06", "Lower Saxony": "06", "RP": "08", "Rhineland-Palatinate": "08",
    "SL": "09", "Saarland": "09", "SA": "14", "Saxony-Anhalt": "14", "TH": "15", "Thuringia": "15",
}.items():
    add(states, alias, ("DE", admin, admin))
add(states, "nh", ("NL", "07", "07"))

# Full Dutch province names are useful context, but names such as Limburg and
# Utrecht are not globally unambiguous. Use them as corroboration per location
# clause, without changing the existing global country/state vocabulary.
NL_PROVINCES = {
    "drenthe": "01", "flevoland": "16", "friesland": "02",
    "gelderland": "03", "groningen": "04", "limburg": "05",
    "noord brabant": "06", "noord holland": "07", "overijssel": "15",
    "utrecht": "09", "zeeland": "10", "zuid holland": "11",
}
NL_COMPOUND_PROVINCES = frozenset({"noord brabant", "noord holland", "zuid holland"})


def _dutch_context(words, bounds, city_spans, country_hits, full_country_hits, state_hits):
    """Recognize Dutch administrative text only with corroborating context.

    Nederland is also a real US city. Keep bare/ambiguous occurrences and any
    clause asserting a US state or a different country on the existing path.
    Semicolon/pipe/slash-separated offices are independent, so recognizing a
    Dutch address cannot reclassify an adjoining US address.
    """
    if not {"nederland", "noord", "zuid"}.intersection(words):
        return
    for number in {part[2] for part in bounds}:
        parts = [part for part in bounds if part[2] == number]
        start, end = parts[0][0], parts[-1][1]
        local_countries = [hit for hit in country_hits if start <= hit[0] < end]
        local_states = [hit for hit in state_hits if start <= hit[0] < end]
        if (any(hit[2] != "NL" for hit in local_countries)
                or any(hit[2] == "US" for hit in local_states)):
            continue
        components = [(a, b, " ".join(words[a:b])) for a, b, *_ in parts]
        component_spans = {(a, b) for a, b, _ in components}
        provinces = [(a, b, NL_PROVINCES[key]) for a, b, key in components
                     if key in NL_PROVINCES]
        # A country suffix can make the existing clause reducer discard other
        # cities. Treat a US-only city outside the administrative components as
        # conflicting evidence too, even without a state: "Seattle, Amsterdam,
        # Noord-Holland, Nederland" may be a comma-separated office list.
        if any(
            start <= a < end and alias != "nederland"
            and ((a, b) in component_spans or (a > start and words[a - 1] == "or")
                 or (b < end and words[b] == "or"))
            and not any(p <= a and b <= q for p, q, _ in provinces)
            and any(place[0] == "US" for place in cities.get(alias, ()))
            and not any(place[0] == "NL" for place in cities.get(alias, ()))
            for a, b, alias in city_spans
        ):
            continue
        native_country = [(a, b, "NL") for a, b, key in components if key == "nederland"]
        has_dutch_city = native_country and any(
            (a, b) in component_spans and alias != "nederland"
            and any(place[0] == "NL" for place in cities.get(alias, ()))
            for a, b, alias in city_spans
        )
        country_context = any(hit[2] == "NL" for hit in local_countries)
        if native_country and (provinces or has_dutch_city or country_context):
            country_hits.extend(native_country)
            full_country_hits.extend(native_country)
        # Only compound names need city-span suppression. Registering Groningen
        # or Utrecht as states also changes existing city-vs-region decisions
        # (e.g. "NLD - Groningen"), which is outside this correction.
        state_hits.extend((a, b, "NL", NL_PROVINCES[key], NL_PROVINCES[key])
                          for a, b, key in components if key in NL_COMPOUND_PROVINCES)


def decode(encoded):
    return [tuple(value or None for value in row.split("|")) for row in encoded.split(";")]


overrides = {clean(key): decode(value) for key, value in {
    "Batavia": "US|IL|Batavia", "CA - Belle Plaine, SK": "CA|SK|Belle Plaine",
    "Bay Area Office": "US|CA|San Francisco", "Bay Area / Remote": "US|CA|San Francisco",
    "Bay Area, CA, United States": "US|CA|San Francisco",
    "Cheektowaga, Gyeonggi-do, Korea, Republic of": "KR|13|", "Florida (Remote)": "US|FL|",
    "Farmville, VA": "US|VA|Farmville", "Guangdong, China": "CN|30|", "Jalisco, Mexico": "MX|14|",
    "Johor, , Malaysia": "MY|01|", "LA": "US|CA|Los Angeles", "Menlo Park": "US|CA|Menlo Park",
    "Mexico": "MX||", "MOUNT-ROYAL (Montreal)": "CA|10|Mont-Royal", "New Jersey": "US|NJ|",
    "New Delhi": "IN|07|New Delhi", "Orange County": "US|CA|", "Oceanus HQ": "US|CA|El Segundo",
    "Penang": "MY|09|", "Penang, Malaysia": "MY|09|", "Penang, Malaysia - Grande": "MY|09|",
    "Penang, Malaysia - Mini-Grande": "MY|09|", "Malaysia, Penang": "MY|09|", "Puerto Rico": "PR||",
    "Region Metropolitana de Santiago, Chile": "CL|12|", "RM- Region Metropolitana de Santiago": "CL|12|",
    "Remote - Bay Area": "US|CA|San Francisco", "RTP, North Carolina, US": "US|NC|",
    "Silicon Valley": "US|CA|",
    "San Mateo": "US|CA|San Mateo", "San Carlos, CA or Seattle, WA": "US|CA|San Carlos;US|WA|Seattle",
    "San Francisco Bay Area": "US|CA|San Francisco", "SF Bay Area": "US|CA|San Francisco",
    "San Francisco Office (Second St)": "US|CA|San Francisco", "San Jose Office (First St)": "US|CA|San Jose",
    "Stuttgart Schockenriedstr. 17": "DE|01|Stuttgart",
    "Sergeičikai I, Kauno apskr., Lithuania": "LT|57|Sergeičikai I",
    "Singapore, Marina Bay Financial Tower 2": "SG||Singapore",
    "Undisclosed test site 35km from Munich": "DE|02|Munich", "RWC HQ": "US|CA|Redwood City",
    "Houston 1400 Smith Street": "US|TX|Houston", "Leland HQ - Lehi, UT": "US|UT|Lehi",
    "Leland HQ - Lehi, UT (Hybrid)": "US|UT|Lehi", "Redpine HQ, central Stockholm": "SE|26|Stockholm",
    "Remote Opportunity - United States; Salt Lake City, Utah": "US||;US|UT|Salt Lake City",
    "Israel, Tel Aviv, Herzliya": "IL|05|Tel Aviv;IL|05|Herzliya",
    "Dallas-Fort Worth, Texas, USA": "US|TX|Dallas;US|TX|Fort Worth", "Oman, Masqat, Rusayl": "OM|06|Rusayl",
    "USA - New York - Malta": "US|NY|Malta", "Yang Pu Qu, Shang Hai Shi, China": "CN|23|Shanghai",
    "Kuwait, Al 'Asimah, Kuwait": "KW|02|Kuwait City",
    "Austin, Texas, USA; Dallas-Fort Worth, Texas, USA; Los Angeles, California, USA; Phoenix, Arizona, USA": "US|TX|Austin;US|TX|Dallas;US|TX|Fort Worth;US|CA|Los Angeles;US|AZ|Phoenix",
    "sf": "US|CA|San Francisco", "SF Office": "US|CA|San Francisco", "HINJEWADI PUNE": "IN|16|Pune",
    "IN KA BANGALORE Home Office PTPP1": "IN|19|Bengaluru", "IN-Bangalore (3811)": "IN|19|Bengaluru",
    "CAN Kanata (2), ON - WR": "CA|ON|Kanata", "CA-Colonsay, SK": "CA|SK|Colonsay",
    "Fins Only-DE-Munich-MSO": "DE|02|Munich", "MEX Aptiv Juarez MTC": "MX|06|Ciudad Juárez",
    "MY-LMM KM [2700] (3833)": "MY|02|Kulim", "MY: GBS Kuala Lumpur": "MY|14|Kuala Lumpur",
    "PL-Warsaw-Lixa C": "PL|78|Warsaw", "SG - Singapore": "SG||Singapore",
    "SGP - Science Park": "SG||Singapore", "MSB, Singapore": "SG||Singapore",
    "Fab 10A, Singapore": "SG||Singapore", "Fab 10N/X, Singapore": "SG||Singapore",
    "Fab 10W, Singapore": "SG||Singapore", "Malaysia > Selangor : Imazium, No. 8, Jalan SS 21/37": "MY|12|",
    "FXE_APAC/CHN/CNCANRC/Guangzhou call center": "CN|30|Guangzhou",
    "FXE_APAC/CHN/CNWUHRC/FDX/CN Customer Service Ctr": "CN|12|Wuhan",
    "FXE_APAC/IDN/IDPPJA/JAKARTA HEAD OFFI": "ID|04|Jakarta",
    "FXE_APAC/KOR/KRKORHQ/FEDEX KOREA HQ - KB HAPJEONG": "KR|11|Seoul",
    "FXE_APAC/MYS/MYKULHQ/KL Headquarters": "MY|14|Kuala Lumpur",
    "FXE_APAC/MYS/MYKULRC/ECCC": "MY|14|Kuala Lumpur",
    "FXE_APAC/PHL/PHCRKRC/PH ANGELES CITY": "PH|03|Angeles City",
    "FXE_APAC/PHL/PHMNLHQ/Philippines Headquarters": "PH|NCR|Manila",
    "FXE_APAC/SGP/SGSINHQ/SG HUB 90 Alps Avenue": "SG||Singapore",
    "FXE_APAC/THA/THHHQA/TNT head office": "TH|40|Bangkok",
    "FXE_APAC/VNM/VNSGNHQ/Headquarter": "VN|79|Ho Chi Minh City",
    "FXE-EU/PRT/OPOSSC/OPOSSC/R.LIONESA": "PT|17|Porto",
    "FXE-MEISA/SAU/RUHZ/RUHZ/Riyadh": "SA|10|Riyadh",
}.items()}

# These two whole-value overrides are also safe when they appear as exact
# delimiter-separated components inside richer structured location text. Keep
# this allowlist deliberately small: short overrides such as ``LA`` are
# ambiguous (Louisiana versus Los Angeles) once removed from whole-value
# context.
SAFE_COMPONENT_OVERRIDES = frozenset({"san mateo", "new jersey"})
COMPONENT_SEPARATOR = re.compile(r"[;|,]+|\s+/\s+|\s+[\-–—]\s+")
EXPLICIT_US_NAME = re.compile(
    r"\bUnited\s+States(?:\s+of\s+America)?\b",
    re.IGNORECASE,
)
EXPLICIT_US_ABBREVIATION = re.compile(
    r"(?<![A-Za-z0-9])(?:US|USA|U\.S(?:\.A)?\.?)(?![A-Za-z0-9])"
)


def _append_strong_hints(rows, value):
    """Append narrow source assertions without changing existing results."""
    hints = []
    for component in COMPONENT_SEPARATOR.split(value):
        key = clean(component)
        if key in SAFE_COMPONENT_OVERRIDES:
            hints.extend(overrides[key])

    if EXPLICIT_US_NAME.search(value) or EXPLICIT_US_ABBREVIATION.search(value):
        hints.append(("US", None, None))

    def refines(row, hint):
        return all(expected is None or actual == expected
                   for actual, expected in zip(row, hint))

    for hint in hints:
        if not any(refines(row, hint) for row in rows):
            rows.append(hint)


@lru_cache(maxsize=8192)
def _normalize_location(value: str) -> dict:
    original_value = value
    remote = bool(re.search(r"\b(remote|hybrid)\b", value, re.IGNORECASE))
    # The country alias table recognizes this formal name, but the city matcher
    # can otherwise select América, Argentina from the nested final word.
    # Canonicalizing the exact country phrase strengthens the intended signal
    # without changing any surrounding location structure.
    value = re.sub(
        r"\bUnited\s+States\s+of\s+America\b",
        "United States",
        value,
        flags=re.IGNORECASE,
    )
    if key := overrides.get(clean(value)):
        return response(key, remote)
    retail = re.match(r"^\((USA|CAN)\)\s+([A-Z]{2})\s+(.+?)\s+\d{5}\s+", value)
    if retail:
        country = {"USA": "US", "CAN": "CA"}[retail[1]]
        state, city_text = retail[2], clean(retail[3])
        matches = [x for x in cities.get(city_text, ())
                   if x[0] == country and (country != "US" or x[1] == state)]
        city = max(matches, key=lambda x: (x[4], x[3]))[2] if matches else retail[3].title()
        return response([(country, state, city)], remote)

    value = re.sub(r"\bwal[\s-]*mart\b", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"Korea\s*,\s*Republic of", "South Korea", value, flags=re.IGNORECASE)
    words, word_clauses, bounds, uppercase = [], [], [], set()
    for clause_number, clause in enumerate(re.split(r"[;|]+|\s+/\s+", value)):
        for component_number, raw in enumerate(re.split(r"[,]+|\s+[\-–—]\s+", clause)):
            segment = clean(raw).split()
            if not segment:
                continue
            start = len(words)
            raw_words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", raw))
            words += segment
            word_clauses += [clause_number] * len(segment)
            if len(raw_words) == len(segment):
                uppercase.update(start + i for i, word in enumerate(raw_words)
                                 if len(clean(word)) <= 3 and word.isupper())
            bounds.append((start, len(words), clause_number, component_number))

    # "Bay Area" is a metro-level hint, not a city token. Feeding it into the
    # normal city ranking would let San Francisco displace stronger localities
    # such as Oakland, Burlingame, or Campbell. Record only high-confidence
    # forms here and apply the hint after the existing parse has finished.
    bay_area_clauses = set()
    for start, end, clause_number, _ in bounds:
        component = words[start:end]
        for index in range(len(component) - 1):
            if component[index:index + 2] != ["bay", "area"]:
                continue
            prefix = component[:index]
            explicit_sf = prefix[-1:] == ["sf"] or prefix[-2:] == ["san", "francisco"]
            unqualified = not prefix or all(word in {"remote", "hybrid"} for word in prefix)
            if explicit_sf or unqualified:
                bay_area_clauses.add(clause_number)
            break

    spans = [(i, j, " ".join(words[i:j])) for start, end, _, _ in bounds
             for i in range(start, end) for j in range(i + 1, end + 1)]
    short = {i for i, word in enumerate(words) if len(word) == 2}
    city_spans = [(i, j, x) for i, j, x in spans
                  if j - i > 1 or not short.intersection(range(i, j))]
    country_hits = [(i, j, countries[x]) for i, j, x in city_spans
                    if x in countries and x not in {"and", "or"} and
                    (x not in country_abbreviations or all(k in uppercase for k in range(i, j)))]
    full_country_hits = [(i, j, countries[x]) for i, j, x in city_spans
                         if x in countries and (x not in country_abbreviations or x in {"us", "u s", "usa"})]

    components = {(start, end) for start, end, *_ in bounds}
    state_hits = [(i, j, *state) for i, j, x in spans
                  if (x not in {"and", "or"} or (i, j) in components) and
                  (len(x) != 2 or i in uppercase)
                  for state in states.get(x, ())]
    country_hits += [(i, i + 1, countries[word]) for i, word in enumerate(words)
                     if i in short and i in uppercase and word in countries]
    full_country_hits += [(i, i + 1, countries[word]) for i, word in enumerate(words)
                          if i in uppercase and word in {"us"}]

    _dutch_context(words, bounds, city_spans, country_hits, full_country_hits, state_hits)

    def nearest(hits, start, end):
        hits = [x for x in hits if word_clauses[x[0]] == word_clauses[start]]
        if not hits:
            return None
        if len({x[2] for x in hits}) == 1:
            return hits[0][2]
        return min(hits, key=lambda x: (max(start - x[1], x[0] - end, 0), x[0] < end))[2]

    def nearest_states(start, end):
        hits = [x for x in state_hits if word_clauses[x[0]] == word_clauses[start]]
        if not hits:
            return []
        distances = [(max(start - x[1], x[0] - end, 0), x) for x in hits]
        distance = min(x[0] for x in distances)
        return [x for d, x in distances if d == distance]

    ignored = short | {i for i, word in enumerate(words) if word.isdigit() or word in NOISE}
    for start, end, _, _ in bounds:
        if end - start > 2 and words[start] == "greater" and words[end - 1] == "area":
            ignored.add(start)
    if "area" in words:
        ignored.update(i for i, word in enumerate(words) if word == "bay")
    for start, end, _, _ in bounds:
        if (end - start > 1 and words[end - 1] in {"center", "centre", "campus", "building"} and
                " ".join(words[start:end]) not in cities):
            ignored.add(end - 1)

    found = {}
    suppressed_states = set()
    ambiguous_country_spans = {(i, j) for i, j, _ in country_hits
                               if any(a == i and b == j for a, b, *_ in state_hits)}
    explicit_country_hits = [x for x in country_hits if x[:2] not in ambiguous_country_spans]
    ambiguous_country_hits = [x for x in country_hits if x[:2] in ambiguous_country_spans]
    forbidden = ignored - short

    def fits(place, state):
        return place[0] == state[2] and (state[4] is None or place[1] == state[4])

    for start, end, alias in city_spans:
        places = cities.get(alias, ())
        if not places or any(i in forbidden for i in range(start, end)):
            continue
        if (any(x[:2] == (start, end) for x in state_hits) and
                not any(fits(place, state) for place in places for state in state_hits
                        if word_clauses[state[0]] == word_clauses[start])):
            continue
        # A multi-word state name loses to city matches on its own halves:
        # "North Carolina" is a state span (0, 2) while `north` (0, 1) and
        # `carolina` (1, 2) are cities, and the exact-span guard above never
        # sees them. Strict containment only -- an equal span is still decided
        # by `fits` above, so "New York, NY" keeps resolving to the city.
        #
        # Suppressing the city is only half the repair. The `if not rows`
        # fallback below promotes state hits only when NOTHING was selected, so
        # in "Atlanta; Florida; North Carolina" the surviving Atlanta would keep
        # North Carolina out entirely. Record the covering state and assert it
        # afterwards through the same monotonic path `_append_strong_hints`
        # uses -- append, never replace.
        covering = [state for state in state_hits
                    if state[0] <= start and end <= state[1]
                    and (state[1] - state[0]) > (end - start)
                    and word_clauses[state[0]] == word_clauses[start]]
        if covering:
            suppressed_states.update((x[2], x[3] or None, None) for x in covering)
            continue
        state_options = nearest_states(start, end)
        country = nearest(explicit_country_hits, start, end)
        if country and not any(place[0] == country for place in places):
            country = None
        if country:
            state_options = [x for x in state_options if x[2] == country]
        if state_options and not any(fits(place, state) for place in places for state in state_options):
            state_options = []
        if not country and not state_options:
            possible_country = nearest(ambiguous_country_hits, start, end)
            country = possible_country if possible_country and any(x[0] == possible_country for x in places) else None
        overlapping_country = any(x[0] == start and x[1] == end for x in country_hits)
        for place in places:
            compatible_states = [state for state in state_options if fits(place, state)]
            if ((overlapping_country and alias != country_capitals.get(place[0])) or
                    (state_options and not compatible_states) or (country and place[0] != country)):
                continue
            state = compatible_states[0][3] if compatible_states else place[1]
            candidate = start, end, place[4], place[0], state, place[2], place[3]
            key = start, end, place[0], place[1], place[2]
            found[key] = max(found.get(key, candidate), candidate, key=lambda x: (x[6], x[2]))

    by_start = {}
    for candidate in found.values():
        by_start.setdefault(candidate[0], []).append(candidate)

    rank = lambda matches: (sum(x[1] - x[0] for x in matches), -len(matches),
                            sum(x[6] for x in matches), sum((x[1] - x[0]) * x[2] for x in matches))

    @lru_cache
    def best(position):
        if position >= len(words):
            return ()
        return max([best(position + 1)] + [(x,) + best(x[1]) for x in by_start.get(position, ())], key=rank)

    selected = list(best(0))
    admin = ({i for start, end, _ in country_hits for i in range(start, end)} |
             {i for start, end, *_ in state_hits for i in range(start, end)})
    for start, end, _, _ in bounds:
        local = [x for x in selected if start <= x[0] < end]
        covered = admin | ignored | {i for x in local for i in range(x[0], x[1])}
        if local and any(i not in covered for i in range(start, end)):
            selected = [x for x in selected if x not in local]

    candidate_rank = lambda x: (x[1] - x[0], x[6], x[2], -x[0])

    def inside(hit, group):
        return any(start <= hit[0] and hit[1] <= end for start, end, *_ in group)

    def pick(start, end, predicate):
        return max((x for x in found.values() if start <= x[0] < end and predicate(x)),
                   key=candidate_rank, default=None)

    def replace(group, choice):
        selected[:] = [x for x in selected if not inside(x, group)] + ([choice] if choice else [])

    clauses = [[x for x in bounds if x[2] == number] for number in {x[2] for x in bounds}]
    for clause in clauses:
        if len(clause) < 2:
            continue

        full = [x for x in full_country_hits if x[:2] not in ambiguous_country_spans and inside(x, clause)]
        clause_countries = [x for x in (full or explicit_country_hits) if inside(x, clause)]
        codes = {x[2] for x in clause_countries}
        if len(codes) == 1:
            code = codes.pop()
            country_parts = {part for a, b, _, part in clause
                             if any((a, b) == x[:2] for x in clause_countries)}
            state_parts = {part for a, b, _, part in clause
                           if any((a, b) == x[:2] for x in state_hits)}
            choice = None
            if clause[-1][3] in country_parts:
                for a, b, *_ in clause[:-1]:
                    if choice := pick(a, b, lambda x: x[3] == code):
                        break
                replace(clause, choice)
            elif clause[0][3] in country_parts:
                for a, b, _, part in reversed(clause[1:]):
                    if part not in country_parts | state_parts and (choice := pick(a, b, lambda x: x[3] == code)):
                        break
                if not choice:
                    a, b, *_ = clause[0]
                    choice = pick(a, b, lambda x: x[3] == code)
                replace(clause, choice)

        a, b, *_ = clause[-1]
        trailing_states = [x for x in state_hits if a <= x[0] and x[1] <= b]
        clause_states = [x for x in state_hits if inside(x, clause)]
        if trailing_states and len({(x[2], x[3]) for x in clause_states}) == 1:
            for a, b, *_ in reversed(clause[:-1]):
                choice = pick(a, b, lambda x: any(x[3] == state[2] and
                              (state[4] is None or x[4] == state[3]) for state in trailing_states))
                if choice:
                    replace(clause, choice)
                    break

    rows = list(dict.fromkeys((x[3], x[4] or None, x[5]) for x in sorted(selected, key=lambda x: x[0])))
    if not rows:
        full_countries = {x[2] for x in full_country_hits}
        regions = [(i, country, state) for i, _, country, state, _ in state_hits
                   if not full_countries or country in full_countries]
        if not regions:
            hits = full_country_hits if full_countries else country_hits
            regions = [(i, code, None) for i, _, code in hits]
        rows = list(dict.fromkeys((country, state, None) for _, country, state in sorted(regions)))

    # Preserve the normalizer's result and add San Francisco only where a Bay
    # Area clause did not already resolve to a more specific California city.
    # "or" makes peer locations alternatives (Bay Area or Los Angeles), so it
    # does not suppress the Bay Area fallback.
    if bay_area_clauses:
        specific_ca_city_clauses = {
            word_clauses[x[0]] for x in selected
            if x[3] == "US" and x[4] == "CA" and x[5] != "San Francisco"
        }
        alternative_clauses = {
            word_clauses[index] for index, word in enumerate(words) if word == "or"
        }
        unresolved = bay_area_clauses - (specific_ca_city_clauses - alternative_clauses)
        bay_area = ("US", "CA", "San Francisco")
        if unresolved and bay_area not in rows:
            # A single country/state ancestor contains no information that the
            # metro anchor does not preserve. With multiple rows, retain every
            # existing result so another office or region can never disappear.
            if len(rows) == 1 and rows[0][2] is None and all(
                broad is None or broad == specific
                for broad, specific in zip(rows[0], bay_area)
            ):
                rows[0] = bay_area
            else:
                rows.append(bay_area)
    # The city optimizer intentionally remains unchanged. Reconcile only
    # strong signals that it can currently suppress when a richer input adds
    # another city. This operation is monotonic: every pre-existing tuple
    # stays in its original order and only missing refinements are appended.
    _append_strong_hints(rows, original_value)
    # Same contract as the hints above: a state whose own words were consumed by
    # a spurious city match is appended only where no existing row already
    # refines it, so a resolved "Charlotte, North Carolina" is never widened.
    _append_strong_hints(rows, "")
    for hint in sorted(suppressed_states):
        if not any(all(want is None or got == want for got, want in zip(row, hint))
                   for row in rows):
            rows.append(hint)
    return response(rows, remote)


def normalize_location(value: str) -> dict:
    """Return a fresh copy of the bounded, memoized normalization result."""
    result = _normalize_location(value)
    return {
        "locations": [location.copy() for location in result["locations"]],
        "remote": result["remote"],
    }


def main(argv: list[str] | None = None) -> None:
    """Normalize JSONL from files or stdin and write JSONL to stdout."""
    paths = sys.argv[1:] if argv is None else argv
    for line in fileinput.input(paths or ("-",)):
        if line.strip():
            item = json.loads(line)
            value = item.get("input", item.get("location_text")) if isinstance(item, dict) else item
            result = normalize_location(value) if isinstance(value, str) else response([], False)
            print(json.dumps({"input": value, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
