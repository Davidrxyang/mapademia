"""
Aggregate raw USAspending assistance-transaction CSVs (one subdir per agency,
as produced by fetch/fetch_usaspending.py) into a small state x year x agency
funding summary, small enough to ship to the static frontend.

Sums federal_action_obligation (the incremental obligation per transaction,
not the cumulative award total) grouped by recipient state - i.e. funding is
attributed to where the recipient institution is, not where the work is
physically performed.
"""

from pathlib import Path

import duckdb

RAW_DIR = Path(__file__).resolve().parent / "raw"
# Output goes straight into frontend/data - this aggregate *is* the published
# artifact the static site consumes, per the offline-processing architecture.
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "data"


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    csv_glob = str(RAW_DIR / "*" / "*" / "*.csv")
    con.execute(f"""
        create table transactions as
        select
            recipient_state_code,
            recipient_state_name,
            prime_award_transaction_recipient_state_fips_code as recipient_state_fips,
            action_date_fiscal_year::int as fiscal_year,
            funding_agency_name,
            funding_sub_agency_name,
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
                coalesce(funding_sub_agency_name, funding_agency_name) as agency,
                sum(federal_action_obligation) as total_obligations,
                count(distinct award_id_fain) as award_count
            from transactions
            group by 1, 2, 3, 4, 5
            order by 1, 4, 5
        ) to '{}' (header, delimiter ',')
    """.format(PROCESSED_DIR / "funding_by_state_year_agency.csv"))

    summary = con.execute("""
        select
            coalesce(funding_sub_agency_name, funding_agency_name) as agency,
            fiscal_year,
            sum(federal_action_obligation) as total
        from transactions
        group by 1, 2
        order by 1, 2
    """).fetchdf()
    print(summary.to_string(index=False))
    print(f"\nWrote {PROCESSED_DIR / 'funding_by_state_year_agency.csv'}")


if __name__ == "__main__":
    main()
