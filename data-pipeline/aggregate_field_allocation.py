"""
Allocate each institution's funding (by year, agency) across its OpenAlex
research fields, proportional to that institution's field_mix shares, then
aggregate to year + agency + domain + field. Feeds the treemap view.

This is a proxy, not a ground-truth award-level field tag: it distributes
an institution's total funding using its overall research-output profile,
not what any specific grant was actually for. Institutions with no matched
field_mix data (e.g. very low OpenAlex output) contribute nothing here -
their funding isn't lost from other views, just absent from this one.

Each institution's shares are renormalized to sum to 1 before allocating,
so a given institution's funding always fully distributes across its own
fields (field_mix shares don't sum to 1 on their own - see
aggregate_openalex.py - so allocating with raw shares would silently drop
money rather than spreading it across the institution's known fields).
"""

from pathlib import Path

import duckdb

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "frontend" / "data"


def main():
    con = duckdb.connect()
    con.execute(f"""
        create table funding as
        select uei, fiscal_year, agency, sum(total_obligations) as total_obligations
        from read_csv_auto('{PROCESSED_DIR / "funding_by_institution_year_agency.csv"}')
        group by uei, fiscal_year, agency
    """)
    con.execute(f"""
        create table field_mix as
        select * from read_csv_auto('{PROCESSED_DIR / "institution_field_mix.csv"}')
    """)

    con.execute(f"""
        copy (
            with normalized as (
                select uei, field_name, domain_name, share / sum(share) over (partition by uei) as norm_share
                from field_mix
            )
            select
                f.fiscal_year,
                f.agency,
                n.domain_name,
                n.field_name,
                sum(f.total_obligations * n.norm_share) as allocated_funding
            from funding f
            join normalized n using (uei)
            group by f.fiscal_year, f.agency, n.domain_name, n.field_name
            order by f.fiscal_year, f.agency, allocated_funding desc
        ) to '{PROCESSED_DIR / "funding_allocated_by_field_year_agency.csv"}' (header, delimiter ',')
    """)

    n = con.execute(f"select count(*) from read_csv_auto('{PROCESSED_DIR / 'funding_allocated_by_field_year_agency.csv'}')").fetchone()[0]
    covered = con.execute("""
        select sum(total_obligations) from funding where uei in (select distinct uei from field_mix)
    """).fetchone()[0]
    total = con.execute("select sum(total_obligations) from funding").fetchone()[0]
    print(f"Wrote funding_allocated_by_field_year_agency.csv ({n} rows)")
    print(f"Coverage: ${covered:,.0f} of ${total:,.0f} total funding ({100*covered/total:.1f}%) has field-mix data to allocate")


if __name__ == "__main__":
    main()
