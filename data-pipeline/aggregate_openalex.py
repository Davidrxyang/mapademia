"""
Extract per-year output/citation counts and field mix for our crosswalked
institutions from the OpenAlex institutions snapshot (see institution
records' own counts_by_year and topic_share fields - no API calls, no
per-work data needed).

Citation counts for recent years are inherently lower than older years
regardless of research quality - papers need time to accumulate citations,
so FY2025/2026 cited_by_count is not comparable to FY2016's. This is a
citation-lag artifact of the data, not a signal - callers should not
present raw recent-year citation counts as "impact" without that caveat.
"""

from pathlib import Path

import duckdb

RAW_DIR = Path(__file__).resolve().parent / "raw"
CROSSWALK_PATH = Path(__file__).resolve().parent / "institution_crosswalk.csv"
PROCESSED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "data"

MIN_YEAR = 2016
MAX_YEAR = 2026


def main():
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    con.execute(f"""
        create table crosswalk as
        select uei, openalex_id from read_csv_auto('{CROSSWALK_PATH}')
    """)
    con.execute(f"""
        create table insts as
        select * from read_ndjson_auto('{RAW_DIR / "openalex_institutions" / "*" / "*.gz"}', ignore_errors=true)
        where country_code in ('US', 'PR', 'VI', 'GU', 'AS', 'MP')
    """)

    con.execute(f"""
        copy (
            select
                c.uei,
                cy.year as fiscal_year,
                cy.works_count,
                cy.cited_by_count
            from crosswalk c
            join insts i on i.id = c.openalex_id
            cross join unnest(i.counts_by_year) as t(cy)
            where cy.year between {MIN_YEAR} and {MAX_YEAR}
            order by c.uei, cy.year
        ) to '{PROCESSED_DIR / "institution_output_by_year.csv"}' (header, delimiter ',')
    """)

    con.execute(f"""
        copy (
            select
                c.uei,
                ts.field.display_name as field_name,
                ts.domain.display_name as domain_name,
                sum(ts.value) as share
            from crosswalk c
            join insts i on i.id = c.openalex_id
            cross join unnest(i.topic_share) as t(ts)
            group by c.uei, field_name, domain_name
            order by c.uei, share desc
        ) to '{PROCESSED_DIR / "institution_field_mix.csv"}' (header, delimiter ',')
    """)

    con.execute(f"""
        copy (
            select c.uei, i.geo.latitude as lat, i.geo.longitude as lon
            from crosswalk c
            join insts i on i.id = c.openalex_id
            where i.geo.latitude is not null
        ) to '{PROCESSED_DIR / "institution_geo.csv"}' (header, delimiter ',')
    """)

    n_years = con.execute(f"select count(*) from read_csv_auto('{PROCESSED_DIR / 'institution_output_by_year.csv'}')").fetchone()[0]
    n_fields = con.execute(f"select count(*) from read_csv_auto('{PROCESSED_DIR / 'institution_field_mix.csv'}')").fetchone()[0]
    n_geo = con.execute(f"select count(*) from read_csv_auto('{PROCESSED_DIR / 'institution_geo.csv'}')").fetchone()[0]
    print(f"Wrote institution_output_by_year.csv ({n_years} rows)")
    print(f"Wrote institution_field_mix.csv ({n_fields} rows)")
    print(f"Wrote institution_geo.csv ({n_geo} of 500 institutions have coordinates)")


if __name__ == "__main__":
    main()
