"""
Match our top-500 USAspending recipients (by recipient_uei) to OpenAlex
institution records, entirely offline against the OpenAlex institutions
snapshot (see fetch_openalex_snapshot.py) - no API calls, no rate limits.

There's no shared identifier between USAspending (UEI/DUNS) and OpenAlex
(ROR-based) institution records, so this is name matching, blocked by US
state to keep it fast and to disambiguate similarly-named institutions in
different states. It's a curated crosswalk, not an exact join - matches
below CONFIDENCE_THRESHOLD are flagged for manual review rather than
silently trusted.
"""

import csv
import difflib
import math
import re
from pathlib import Path

import duckdb

RAW_DIR = Path(__file__).resolve().parent.parent / "raw"
FRONTEND_DATA = Path(__file__).resolve().parent.parent.parent / "frontend" / "data"
OUT_PATH = Path(__file__).resolve().parent.parent / "institution_crosswalk.csv"

CONFIDENCE_THRESHOLD = 0.55
STATE_MATCH_BONUS = 0.15

# USPS postal code -> full state name, to compare against OpenAlex's geo.region
POSTAL_TO_STATE = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "PR": "Puerto Rico",
}

# Generic legal/institutional boilerplate that shows up in official award
# recipient names but carries no identifying signal (and actively causes
# mismatches by diluting distinctive words like "Harvard" or "Tulane").
STOPWORDS = {
    "the", "of", "at", "in", "for", "and", "board", "regents", "trustees",
    "president", "fellows", "fund", "foundation", "corporation", "corp",
    "inc", "administrators", "system", "higher", "educational", "education",
    "commonwealth",
}


def normalize(name: str) -> str:
    words = re.sub(r"[,\-]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in STOPWORDS)


def jaccard(a: str, b: str) -> float:
    sa, sb = set(normalize(a).split()), set(normalize(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similarity(a: str, b: str) -> float:
    # Blend word-overlap (robust to boilerplate-driven length differences)
    # with sequence ratio (rewards close full-string matches).
    ratio = difflib.SequenceMatcher(None, normalize(a), normalize(b)).ratio()
    return 0.6 * jaccard(a, b) + 0.4 * ratio


def load_target_institutions() -> dict:
    seen = {}
    with open(FRONTEND_DATA / "funding_by_institution_year_agency.csv") as f:
        for row in csv.DictReader(f):
            seen[row["uei"]] = (row["name"], row["city"], row["state_code"])
    return seen


def depth_penalty(lineage_len: int) -> float:
    # lineage length 1 = top-level institution. 2 is often still a genuine
    # standalone university nominally filed under a state "system" parent
    # (e.g. Jackson State, Mississippi State both come back as length 2) -
    # only a small penalty. 3+ is reliably a true sub-unit (a department,
    # observatory, hospital-within-a-system) and should rarely win against
    # a same-named top-level institution, hence the much steeper penalty.
    if lineage_len <= 1:
        return 0.0
    if lineage_len == 2:
        return -0.05
    return -0.35


def load_openalex_candidates() -> list:
    """Returns [(id, ror, display_name, [alt_names], region, lineage_len, works_count), ...]"""
    con = duckdb.connect()
    con.execute(f"""
        create table insts as
        select * from read_ndjson_auto('{RAW_DIR / "openalex_institutions" / "*" / "*.gz"}', ignore_errors=true)
        where country_code = 'US'
    """)
    rows = con.execute("""
        select id, ror, display_name, display_name_alternatives, geo.region as region,
               len(lineage) as lineage_len, works_count
        from insts
    """).fetchall()
    return [(id_, ror, display_name, alts or [], region, lineage_len, works_count)
            for id_, ror, display_name, alts, region, lineage_len, works_count in rows]


def build_word_index(candidates: list) -> dict:
    """word -> set of candidate indices whose name/alternatives contain it.
    geo.region is null even for major institutions (Creighton, the main BYU
    campus, ...), so region can't be a hard filter - blocking by shared
    words is the only way to keep this fast without silently excluding
    correct matches the way region-blocking did."""
    index = {}
    for i, (_, _, display_name, alts, *_rest) in enumerate(candidates):
        for n in [display_name] + alts:
            for w in set(normalize(n).split()):
                index.setdefault(w, set()).add(i)
    return index


def best_match(name: str, state_code: str, candidates: list, word_index: dict) -> tuple:
    region = POSTAL_TO_STATE.get(state_code)
    query_words = set(normalize(name).split())

    candidate_ids = set()
    for w in query_words:
        candidate_ids |= word_index.get(w, set())
    if not candidate_ids:
        candidate_ids = range(len(candidates))  # last-resort: full scan

    best = None
    best_score = -1.0
    for i in candidate_ids:
        id_, ror, display_name, alts, cand_region, lineage_len, works_count = candidates[i]
        name_sim = max(similarity(name, n) for n in [display_name] + alts)
        magnitude = 0.05 * math.log10((works_count or 0) + 1) / 6
        score = (
            name_sim
            + depth_penalty(lineage_len)
            + magnitude
            + (STATE_MATCH_BONUS if region and cand_region == region else 0)
        )
        if score > best_score:
            best_score = score
            best = (id_, ror, display_name, name_sim)
    return best, best_score


# Hand-verified corrections for cases where automated name matching loses to
# a same-named sub-unit or coincidentally-worded unrelated institution -
# checked individually against OpenAlex by hand, not derived from a rule.
# Keyed by recipient_uei. Each entry: (openalex_id, ror, openalex_name).
MANUAL_OVERRIDES = {
    "JDLVAVGYJQ21": ("https://openalex.org/I145311948", "https://ror.org/00za53h95", "Harvard University"),
    "LN53LCFJFL45": ("https://openalex.org/I145311948", "https://ror.org/00za53h95", "Harvard University"),
    "UNVDZNFA8R29": ("https://openalex.org/I145311948", "https://ror.org/00za53h95", "Harvard University"),
    "QN6MS4VN7BD1": ("https://openalex.org/I1283280774", "", "Brigham and Women's Hospital"),
    "W8LKB16HV1K5": ("https://openalex.org/I8248082", "", "Florida Agricultural and Mechanical University"),
    "C1F5LNUF7W86": ("https://openalex.org/I121934306", "", "Tufts University"),
    "WL9FLBRVPJJ7": ("https://openalex.org/I121934306", "", "Tufts University"),
    "DZ4YCZ3QSPR5": ("https://openalex.org/I63135867", "", "University of Cincinnati"),
    "GKPBCFV1QMM3": ("https://openalex.org/I4210146710", "", "Mayo Clinic in Florida"),
    "ULMJJBL7ZXX3": ("https://openalex.org/I4210125099", "", "Mayo Clinic in Arizona"),
    # These three matched at high (even 1.0) similarity but were still wrong:
    # generic legal suffixes ("Corporation" vs "Foundation") are both stripped
    # as boilerplate, so an unrelated org sharing just the core name can
    # collide at a perfect score - a real blind spot in the stopword approach,
    # caught by cross-checking funding against OpenAlex output (these three
    # had $2-5B in funding but only tens-to-hundreds of matched papers).
    "FLJ7DQKLL226": ("https://openalex.org/I4210087915", "", "Massachusetts General Hospital"),
    "Z1L9F1MM1RY3": ("https://openalex.org/I1288882113", "", "Boston Children's Hospital"),
    "KUKXRCZ6NZC2": ("https://openalex.org/I1334819555", "", "Memorial Sloan Kettering Cancer Center"),
    # Same failure shape as above, but via a different route: matched a
    # "System"/sub-unit variant at a perfect 1.0 text score that turned out
    # to have ~zero OpenAlex output itself - the real research output sits
    # under the actual campus entity, not the administrative shell.
    "YRXVL4JYCEF5": ("https://openalex.org/I219193219", "", "Purdue University West Lafayette"),
    "SN7KD2UK7GC5": ("https://openalex.org/I47251452", "", "Wake Forest University"),
}


def main():
    targets = load_target_institutions()
    print(f"Loading OpenAlex US institutions for matching {len(targets)} targets...")
    candidates = load_openalex_candidates()
    word_index = build_word_index(candidates)
    print(f"Loaded {len(candidates)} top-level US OpenAlex institutions ({len(word_index)} index terms).")

    rows = []
    low_confidence = []
    for uei, (name, city, state) in targets.items():
        if uei in MANUAL_OVERRIDES:
            oa_id, ror, oa_name = MANUAL_OVERRIDES[uei]
            rows.append([uei, name, city, state, oa_id, ror, oa_name, "manual"])
            continue

        match, score = best_match(name, state, candidates, word_index)
        if match is None:
            rows.append([uei, name, city, state, "", "", "", 0])
            low_confidence.append((name, city, state, "NO MATCH"))
            continue
        oa_id, ror, oa_name, name_sim = match
        rows.append([uei, name, city, state, oa_id, ror, oa_name, round(name_sim, 3)])
        if name_sim < CONFIDENCE_THRESHOLD:
            low_confidence.append((name, city, state, f"{oa_name} ({name_sim:.2f})"))

    with open(OUT_PATH, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["uei", "recipient_name", "city", "state_code", "openalex_id", "ror", "openalex_name", "similarity"])
        writer.writerows(rows)

    print(f"\nWrote {OUT_PATH} ({len(rows)} rows)")
    print(f"\n{len(low_confidence)} low-confidence matches (similarity < {CONFIDENCE_THRESHOLD}) for review:")
    for name, city, state, resolved in low_confidence:
        print(f"  {name} ({city}, {state}) -> {resolved}")


if __name__ == "__main__":
    main()
