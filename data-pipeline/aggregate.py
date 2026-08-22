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


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    csv_glob = str(RAW_DIR / "*" / "*" / "*.csv")
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
    """)

    con.execute("""
        copy (
            select
                recipient_state_fips as state_fips,
                recipient_state_code as state_code,
                recipient_state_name as state_name,
                fiscal_year,
                coalesce(awarding_sub_agency_name, awarding_agency_name) as agency,
                sum(federal_action_obligation) as total_obligations,
                count(distinct award_id_fain) as award_count
            from transactions
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

    con.execute("""
        copy (
            select
                t.recipient_uei as uei,
                arg_max(t.recipient_name, t.fiscal_year) as name,
                arg_max(t.recipient_city_name, t.fiscal_year) as city,
                arg_max(t.recipient_state_code, t.fiscal_year) as state_code,
                t.fiscal_year,
                coalesce(t.awarding_sub_agency_name, t.awarding_agency_name) as agency,
                sum(t.federal_action_obligation) as total_obligations,
                count(distinct t.award_id_fain) as award_count
            from transactions t
            join top_institutions ti using (recipient_uei)
            group by t.recipient_uei, t.fiscal_year, agency
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
