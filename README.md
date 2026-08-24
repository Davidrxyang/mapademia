# mapademia

Visualizes the US academic research economy: which states and institutions get federal research funding, and how that correlates with actual research output and impact.

## Features

- Map of federal research funding by state and institution, filterable by agency and field.
- Funding vs. research output/impact by field and subfield, not just geography.
- Institution-level breakdowns.

## Data

- Funding: USAspending.gov (NSF, NIH, NASA, DOD, DOE, EPA), NIH ExPORTER.
- Research output/impact: OpenAlex.
- DOE and EPA are restricted to university-grant recipients - their full
  grant-type data is dominated by non-research programs (DOE state energy/
  weatherization formula grants, EPA's multibillion-dollar climate-finance
  and state environmental grants). DOD is restricted to universities plus a
  curated list of legitimate research-performing nonprofits, for the same
  reason.

## Architecture

- Data is pulled in bulk on a quarterly cadence, not via live API queries.
- All processing runs offline, on local machines.
- Only the processed output is published, to a free-hosted static frontend.

## Phases

0. NSF + NIH, state-level funding map.
1. Institution-level funding.
2. OpenAlex output/impact correlation, by field.
3. Award-level funding-to-output linkage (NIH); NASA, DOD, DOE, and EPA added
   as additional funding agencies.
