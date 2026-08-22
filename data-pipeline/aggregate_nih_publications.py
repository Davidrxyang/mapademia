"""
Build genuine award-level linkage for NIH: which specific papers resulted
from which specific grants, using NIH's own project/publication/link data
(see fetch/fetch_nih_exporter.py) - no OpenAlex, no per-paper API calls.

Join path: USAspending transaction (award_id_fain, recipient_uei)
         -> NIH link table (PROJECT_NUMBER == award_id_fain, PMID)
         -> NIH publications file (PMID -> PUB_YEAR)
         -> aggregate to institution + publication-year.

award_id_fain and NIH's CORE_PROJECT_NUM/PROJECT_NUMBER share the same
format (e.g. "R01CA123456") and match directly ~85-90% of the time across
our fetched year range - the gap is mostly older/newer grant-years NIH
didn't snapshot a project record for in our fetched range, not a format
mismatch. That's an accepted, documented coverage gap, not a bug.
"""

from pathlib import Path

import duckdb

RAW_DIR = Path(__file__).resolve().parent / "raw"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "data"

MIN_YEAR = 2016
MAX_YEAR = 2026


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    con.execute(f"""
        create table nih_awards as
        select distinct award_id_fain, recipient_uei
        from read_csv_auto('{RAW_DIR / "national_institutes_of_health" / "*" / "*.csv"}', union_by_name=true, ignore_errors=true)
        where recipient_uei is not null
    """)

    con.execute(f"""
        create table links as
        select distinct PMID as pmid, PROJECT_NUMBER as project_number
        from read_csv_auto('{RAW_DIR / "nih_exporter" / "linktables" / "*" / "*.csv"}', union_by_name=true, ignore_errors=true)
    """)

    con.execute(f"""
        create table pubs as
        select distinct PMID as pmid, PUB_YEAR::int as pub_year
        from read_csv_auto('{RAW_DIR / "nih_exporter" / "publications" / "*" / "*.csv"}', union_by_name=true, ignore_errors=true)
    """)

    total_awards = con.execute("select count(distinct award_id_fain) from nih_awards").fetchone()[0]
    matched_awards = con.execute("""
        select count(distinct a.award_id_fain) from nih_awards a
        join links l on l.project_number = a.award_id_fain
    """).fetchone()[0]
    print(f"NIH awards in USAspending data: {total_awards}")
    print(f"Awards with at least one linked publication: {matched_awards} ({100*matched_awards/total_awards:.1f}%)")

    con.execute(f"""
        copy (
            select
                a.recipient_uei as uei,
                p.pub_year as fiscal_year,
                count(distinct l.pmid) as paper_count,
                count(distinct a.award_id_fain) as award_count
            from nih_awards a
            join links l on l.project_number = a.award_id_fain
            join pubs p on p.pmid = l.pmid
            where p.pub_year between {MIN_YEAR} and {MAX_YEAR}
            group by a.recipient_uei, p.pub_year
            order by uei, fiscal_year
        ) to '{PROCESSED_DIR / "nih_linked_publications_by_institution_year.csv"}' (header, delimiter ',')
    """)

    n = con.execute(f"select count(*) from read_csv_auto('{PROCESSED_DIR / 'nih_linked_publications_by_institution_year.csv'}')").fetchone()[0]
    print(f"Wrote nih_linked_publications_by_institution_year.csv ({n} rows)")


if __name__ == "__main__":
    main()
