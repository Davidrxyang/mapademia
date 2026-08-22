# mapademia

## Vision

A tool to visualize the US academic research economy: which states and institutions get federal research funding, broken down by field, subarea, agency, institution, and eventually researcher — correlated against actual research output and impact (via OpenAlex), and visualized in ways that aren't always geographic (funding vs. impact by field, not just by state).

Must run at effectively zero cost. That means:
- Pull data as **quarterly bulk archives**, not live/real-time API queries.
- Do all processing **offline**, on my own machine.
- Ship only the processed, aggregated output to a **free-hosted** static frontend.

## Data sources

- **USAspending.gov** — bulk award data archive; backbone dataset for funding by state/agency/institution (uniform across all agencies).
- **NIH ExPORTER** — NIH grants with field/disease categories (RCDC) and linked publication PMIDs.
- **NSF Award Search** — NSF's own directorate/division/program taxonomy.
- **DOE / OSTI.gov** — DOE-funded publications already linked to award numbers.
- **USDA NIFA (REEIS/CRIS)** — ag-science awards with discipline codes.
- **OpenAlex** — research output/impact side: works, citations, topics, institutions. Pulled via their REST API (filtered to US institutions, quarterly), not a full snapshot mirror.

Correlation strategy: join funding and output data at the **institution + agency + year** level rather than attempting award-level or researcher-level matching everywhere — that's the tractable version of this problem. Award-level linkage (which grant produced which papers) piggybacks on linkage NIH/DOE already publish, rather than being built from scratch. Researcher-level linkage (PI → OpenAlex author) is a late-stage stretch goal, not core scope, since it needs disambiguation that's unreliable without ORCID.

## Planning phases

0. **NSF + NIH only**, state-level choropleth, funding $ only. *(current)*
1. Institution-level view — curated/fuzzy crosswalk for the top ~300-500 institutions by funding volume (Pareto, not full long-tail matching).
2. Bring in OpenAlex — output/citation metrics joined at institution+agency+year; field breakdowns via OpenAlex's Topics hierarchy.
3. Award-level linkage via NIH/DOE's existing publication links; expand to more agencies (DOD, NASA, USDA, EPA STAR, ED).
4. Researcher-level view (stretch) — piloted on NIH first.

## Implementation notes (Phase 0)

- `data-pipeline/fetch/fetch_usaspending.py` — pulls grant-type prime awards (codes 02/03/04/05: block/formula/project grants, cooperative agreements) for NSF and NIH from USAspending's async bulk-download API. One request per fiscal year per agency — the API caps date ranges at one year.
- `data-pipeline/aggregate.py` — DuckDB aggregation of the raw transaction CSVs into `state x fiscal_year x agency` totals, joined on recipient state FIPS. Writes straight to `frontend/data/` — that aggregate *is* the published artifact, no separate copy step.
- `frontend/index.html` — static D3 choropleth (US states, `us-atlas` TopoJSON) with a fiscal-year slider and agency filter, reading the aggregate CSV directly. No backend; deployable as-is to GitHub Pages / Cloudflare Pages.
- Everything in `data-pipeline/` runs in its own `.venv` (duckdb, pandas, requests).
