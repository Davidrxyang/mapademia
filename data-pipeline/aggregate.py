"""
Aggregate raw USAspending assistance-transaction CSVs (one subdir per agency,
as produced by fetch/fetch_usaspending.py) into small state- and
institution-level funding summaries, small enough to ship to the static
frontend.

Sums federal_action_obligation (the incremental obligation per transaction,
not the cumulative award total) grouped by recipient state/institution - i.e.
funding is attributed to where the recipient institution is, not where the
work is physically performed.
"""

from pathlib import Path

import duckdb

RAW_DIR = Path(__file__).resolve().parent / "raw"
# Output goes straight into frontend/data - this aggregate *is* the published
# artifact the static site consumes, per the offline-processing architecture.
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "data"

# Institution-level output is capped to the top N recipients by all-time
# funding (Pareto: the top 500 already cover ~93% of total obligations) -
# the long tail of small/one-off recipients isn't useful for this view and
# would bloat the published artifact for no benefit.
TOP_N_INSTITUTIONS = 500

# DOE and EPA's grant-type awards (unlike NIH/NSF's) are dominated by non-
# research programs when pulled at full toptier scope - DOE's state energy/
# weatherization formula grants, EPA's multibillion-dollar "green bank"
# climate-finance grants (Greenhouse Gas Reduction Fund) and state
# environmental-agency formula grants. Neither agency exposes a clean
# subtier/office split this API can filter on (checked directly against
# USAspending's agency endpoints), so both are restricted post-hoc to
# recipients tagged as an institution of higher education - which cleanly
# isolates real university research grants (verified against USAspending's
# search API: this produces exactly the kind of university list you'd
# expect, e.g. Kansas State, University of Arizona, Fordham).
HIGHER_ED_BUSINESS_TYPE_CODES = ["H", "O", "S", "T", "U"]
UNIVERSITY_GRANTS_ONLY_AGENCIES = ["Department of Energy", "Environmental Protection Agency"]

# DOD's grant-type awards have the same problem, but its legitimate research
# footprint isn't limited to universities - it also funds a handful of real
# research-performing nonprofits directly (military medicine, applied
# research institutes). Curated by hand against DOD's actual top nonprofit
# recipients (cumulative FY2016-2024): keeps the ones that are themselves
# research performers, excludes manufacturing/industrial consortiums
# (National Center for Manufacturing Sciences, FlexTech Alliance, Vertical
# Lift Consortium...), non-research service orgs (USO, Red Cross, Institute
# of International Education's exchange programs, land-conservation
# trusts...), and other non-research grant programs (Air Force Academy
# Athletic Corporation, Young Marines...). Matched by exact recipient name
# as it appears in USAspending data, scoped to Department of Defense only.
DOD_RESEARCH_NONPROFIT_NAMES = [
    "THE HENRY M. JACKSON FOUNDATION FOR THE ADVANCEMENT OF MILITARY MEDICINE, INC.",
    "BATTELLE MEMORIAL INSTITUTE",
    "RESEARCH TRIANGLE INSTITUTE",
    "NATIONAL ACADEMY OF SCIENCES",
    "WOODS HOLE OCEANOGRAPHIC INSTITUTION",
    "THE GENERAL HOSPITAL CORPORATION",
    "THE MORGRIDGE INSTITUTE FOR RESEARCH, INC.",
    "THE CHARLES STARK DRAPER LABORATORY, INC.",
    "OAK RIDGE ASSOCIATED UNIVERSITIES, INCORPORATED",
    "GEORGIA TECH RESEARCH CORP",
    "VANDERBILT UNIVERSITY MEDICAL CENTER",
    "DENVER RESEARCH INSTITUTE",
]

# NASA's recipient base is otherwise clean (real universities and research
# nonprofits - Bay Area Environmental Research Institute, SETI Institute,
# Universities Space Research Association, small SBIR-style research
# companies like Eureka Scientific and Remote Sensing Systems), so it's left
# unrestricted rather than limited to an allow-list. But it does also fund
# large aerospace/defense contractors directly (Boeing, Lockheed Martin,
# Northrop Grumman, Raytheon, BAE Systems) under the same grant-type codes -
# these are "for-profit organization, other than small business" (business
# type Q), a distinct category from small research businesses (R, left
# alone - confirmed those are genuinely small science/research firms, not
# large contractors). Excluding Q only, not R, targets the actual problem
# without also cutting legitimate small-business research funding.
FOR_PROFIT_EXCLUDED_BUSINESS_TYPE_CODES = ["Q"]
FOR_PROFIT_EXCLUDED_AGENCIES = ["National Aeronautics and Space Administration"]


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    csv_glob = str(RAW_DIR / "*" / "*" / "*.csv")
    restricted_agencies_sql = ", ".join(
        "'" + a.replace("'", "''") + "'" for a in [*UNIVERSITY_GRANTS_ONLY_AGENCIES, "Department of Defense"]
    )
    # business_types_code is not always a single letter - USAspending
    # concatenates multiple category codes with no delimiter when a
    # recipient carries more than one tag (e.g. "HA" = higher-ed institution
    # that's also a state-government instrumentality, extremely common for
    # public universities; confirmed by inspecting real fetched data, not
    # assumed - an exact-match filter would have wrongly excluded every one
    # of these). Each letter maps to exactly one category in USAspending's
    # taxonomy regardless of what else it's concatenated with, so a
    # substring check per code is the correct match, not an exact one.
    higher_ed_clause_sql = " or ".join(f"business_types_code like '%{c}%'" for c in HIGHER_ED_BUSINESS_TYPE_CODES)
    dod_nonprofits_sql = ", ".join("'" + n.replace("'", "''") + "'" for n in DOD_RESEARCH_NONPROFIT_NAMES)
    for_profit_excluded_agencies_sql = ", ".join(
        "'" + a.replace("'", "''") + "'" for a in FOR_PROFIT_EXCLUDED_AGENCIES
    )
    for_profit_codes_clause_sql = " or ".join(
        f"business_types_code like '%{c}%'" for c in FOR_PROFIT_EXCLUDED_BUSINESS_TYPE_CODES
    )
    con.execute(f"""
        create table transactions as
        select
            recipient_uei,
            recipient_name,
            recipient_city_name,
            recipient_state_code,
            recipient_state_name,
            prime_award_transaction_recipient_state_fips_code as recipient_state_fips,
            action_date_fiscal_year::int as fiscal_year,
            awarding_agency_name,
            awarding_sub_agency_name,
            federal_action_obligation::double as federal_action_obligation,
            award_id_fain
        from read_csv_auto('{csv_glob}', union_by_name=true, ignore_errors=true)
        where recipient_country_code = 'USA'
          and recipient_state_code is not null
          and (
              awarding_agency_name not in ({restricted_agencies_sql})
              or ({higher_ed_clause_sql})
              or (awarding_agency_name = 'Department of Defense' and upper(recipient_name) in ({dod_nonprofits_sql}))
          )
          and not (
              awarding_agency_name in ({for_profit_excluded_agencies_sql})
              and ({for_profit_codes_clause_sql})
          )
    """)

    # award_count (below) counts each distinct award only in the fiscal year
    # it FIRST appears, not every year it has a transaction/modification.
    # Multi-year awards (the norm for e.g. NIH R01s, which run ~4-5 years
    # with an annual modification/transaction each year) would otherwise get
    # counted once per year they touch - and every dashboard view sums
    # award_count across whatever [start, end] range the Fiscal Year slider
    # selects, so that inflates "award count" by 3-4x for institutions with
    # many multi-year grants (confirmed: Johns Hopkins showed 20,735 "awards"
    # over FY2016-2026 before this fix: the true distinct-award count is
    # 5,861). Attributing each award to its first fiscal year only makes the
    # count exactly summable across any range with no double-counting.
    # Scoped per (state, award) rather than just per award: ~6.7% of NIH
    # award numbers show up under more than one recipient_uei over their
    # life (grants that transferred institutions when a PI moved, mostly -
    # confirmed by checking a sample: K23/K24/F30 career-development and
    # fellowship awards transfer often). A single global first-seen-year
    # per award would never count that award for whichever state/institution
    # it transferred TO, since its true first year belongs to whoever had it
    # first - undercounting the receiving side. Scoping "first seen" to each
    # state (and separately, each institution, below) means each side
    # correctly counts the award once, in the first year *it* reported that
    # award, regardless of who had it before.
    con.execute("""
        create table state_first_seen as
        select recipient_state_code, award_id_fain, min(fiscal_year) as first_fiscal_year
        from transactions
        where award_id_fain is not null
        group by recipient_state_code, award_id_fain
    """)

    con.execute("""
        copy (
            select
                t.recipient_state_fips as state_fips,
                t.recipient_state_code as state_code,
                t.recipient_state_name as state_name,
                t.fiscal_year,
                coalesce(awarding_sub_agency_name, awarding_agency_name) as agency,
                sum(federal_action_obligation) as total_obligations,
                count(distinct case when fs.first_fiscal_year = t.fiscal_year then t.award_id_fain end) as award_count
            from transactions t
            left join state_first_seen fs
                on fs.recipient_state_code = t.recipient_state_code and fs.award_id_fain = t.award_id_fain
            group by 1, 2, 3, 4, 5
            order by 1, 4, 5
        ) to '{}' (header, delimiter ',')
    """.format(PROCESSED_DIR / "funding_by_state_year_agency.csv"))

    con.execute(f"""
        create table top_institutions as
        select recipient_uei
        from transactions
        where recipient_uei is not null
        group by recipient_uei
        order by sum(federal_action_obligation) desc
        limit {TOP_N_INSTITUTIONS}
    """)

    # Canonical display name/city/state per institution, computed once across
    # its FULL history rather than scoped to one fiscal year - arg_max can't
    # meaningfully break ties on fiscal_year when fiscal_year is already a
    # group-by key (every row in the group shares the same value), so the
    # previous per-year version just picked an arbitrary same-year
    # transaction's name/address rather than the institution's most recent.
    con.execute("""
        create table institution_identity as
        select
            recipient_uei as uei,
            arg_max(recipient_name, fiscal_year) as name,
            arg_max(recipient_city_name, fiscal_year) as city,
            arg_max(recipient_state_code, fiscal_year) as state_code
        from transactions
        where recipient_uei is not null
        group by recipient_uei
    """)

    con.execute("""
        create table institution_first_seen as
        select recipient_uei, award_id_fain, min(fiscal_year) as first_fiscal_year
        from transactions
        where award_id_fain is not null and recipient_uei is not null
        group by recipient_uei, award_id_fain
    """)

    con.execute("""
        copy (
            select
                t.recipient_uei as uei,
                ii.name,
                ii.city,
                ii.state_code,
                t.fiscal_year,
                coalesce(t.awarding_sub_agency_name, t.awarding_agency_name) as agency,
                sum(t.federal_action_obligation) as total_obligations,
                count(distinct case when fs.first_fiscal_year = t.fiscal_year then t.award_id_fain end) as award_count
            from transactions t
            join top_institutions ti using (recipient_uei)
            join institution_identity ii on ii.uei = t.recipient_uei
            left join institution_first_seen fs
                on fs.recipient_uei = t.recipient_uei and fs.award_id_fain = t.award_id_fain
            group by t.recipient_uei, ii.name, ii.city, ii.state_code, t.fiscal_year, agency
            order by uei, fiscal_year, agency
        ) to '{}' (header, delimiter ',')
    """.format(PROCESSED_DIR / "funding_by_institution_year_agency.csv"))

    summary = con.execute("""
        select
            coalesce(awarding_sub_agency_name, awarding_agency_name) as agency,
            fiscal_year,
            sum(federal_action_obligation) as total
        from transactions
        group by 1, 2
        order by 1, 2
    """).fetchdf()
    print(summary.to_string(index=False))
    print(f"\nWrote {PROCESSED_DIR / 'funding_by_state_year_agency.csv'}")
    print(f"Wrote {PROCESSED_DIR / 'funding_by_institution_year_agency.csv'} (top {TOP_N_INSTITUTIONS} institutions)")


if __name__ == "__main__":
    main()
