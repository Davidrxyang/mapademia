# mapademia

Visualizes the US academic research economy: which states, institutions, and researchers get federal research funding, and how that correlates with actual research output and impact.

## Features

- Map of federal research funding by state and institution, filterable by agency and field.
- Funding vs. research output/impact by field and subfield, not just geography.
- Institution-level and researcher-level breakdowns.

## Data

- Funding: USAspending.gov, NIH ExPORTER, NSF Award Search, DOE/OSTI, USDA NIFA.
- Research output/impact: OpenAlex.

## Architecture

- Data is pulled in bulk on a quarterly cadence, not via live API queries.
- All processing runs offline, on local machines.
- Only the processed output is published, to a free-hosted static frontend.

## Phases

0. NSF + NIH, state-level funding map.
1. Institution-level funding.
2. OpenAlex output/impact correlation, by field.
3. Award-level funding-to-output linkage; more agencies.
4. Researcher-level view.
