"""
Pull bulk award (grant) data from USAspending.gov for a set of agencies.

Uses the async bulk_download API (POST /api/v2/bulk_download/awards/), which
generates a CSV zip server-side rather than requiring paginated live queries -
this is the "quarterly batch pull" pattern, run by hand or on a cron, not a
live API dependency for the product itself.

API contract: https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/bulk_download/awards.md
"""

import time
import zipfile
from pathlib import Path

import requests

API_BASE = "https://api.usaspending.gov"
RAW_DIR = Path(__file__).resolve().parent.parent / "raw"

# Grant-type prime award codes only (excludes loans/insurance/direct payments):
# 02 Block Grant, 03 Formula Grant, 04 Project Grant, 05 Cooperative Agreement
GRANT_TYPE_CODES = ["02", "03", "04", "05"]

# "type": "awarding" filters on the agency that administered the award, as
# opposed to "funding" (the agency that actually supplied the money). For
# NIH/NSF these coincide almost always (they fund and administer their own
# grants directly) - but the funding_agency field is unreliable: it's
# entirely unpopulated pre-2017, and undercounts NSF awards by ~30% even in
# recent years (confirmed against USAspending's search API). awarding_agency
# has full, reliable coverage across the whole range, so use that instead.
AGENCIES = [
    {"name": "National Science Foundation", "tier": "toptier", "type": "awarding"},
    {
        "name": "National Institutes of Health",
        "tier": "subtier",
        "type": "awarding",
        "toptier_name": "Department of Health and Human Services",
    },
]

FISCAL_YEARS = range(2016, 2027)  # FY2016 through FY2026 (partial, to date)


def fiscal_year_date_range(fy: int, today: str = "2026-08-22") -> dict:
    """USAspending bulk_download caps date_range at 1 year, so fetch per FY.
    FY{n} runs {n-1}-10-01 through {n}-09-30 (or today, if that FY isn't over)."""
    end = f"{fy}-09-30"
    if end > today:
        end = today
    return {"start_date": f"{fy - 1}-10-01", "end_date": end}


def submit_bulk_download(agency: dict, date_range: dict) -> dict:
    payload = {
        "filters": {
            "agencies": [agency],
            "prime_award_types": GRANT_TYPE_CODES,
            "date_range": date_range,
            "date_type": "action_date",
            "recipient_scope": "domestic",
        },
        "file_format": "csv",
    }
    resp = requests.post(f"{API_BASE}/api/v2/bulk_download/awards/", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


class EmptyResultError(RuntimeError):
    """USAspending occasionally reports 'finished' with a truncated/empty file
    (a known flakiness in its bulk-download generation, not a real zero-row
    result) - treat it as a failure worth retrying."""


def poll_until_ready(status_url: str, poll_seconds: int = 15, timeout_seconds: int = 3600) -> dict:
    elapsed = 0
    while elapsed < timeout_seconds:
        resp = requests.get(status_url, timeout=30)
        if resp.status_code == 404:
            # The job can be briefly unregistered right after submission -
            # treat as not-ready-yet rather than a hard failure.
            print("  status: 404 (not registered yet)")
            time.sleep(poll_seconds)
            elapsed += poll_seconds
            continue
        resp.raise_for_status()
        body = resp.json()
        status = body.get("status")
        print(f"  status: {status}")
        if status == "finished":
            if not body.get("total_rows"):
                raise EmptyResultError(f"Bulk download finished with 0 rows: {body}")
            return body
        if status == "failed":
            raise RuntimeError(f"Bulk download failed: {body}")
        time.sleep(poll_seconds)
        elapsed += poll_seconds
    raise TimeoutError(f"Bulk download did not finish within {timeout_seconds}s")


def download_and_extract(file_url: str, dest_subdir: str) -> Path:
    dest_dir = RAW_DIR / dest_subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / "download.zip"

    with requests.get(file_url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)
    zip_path.unlink()
    return dest_dir


def already_fetched(dest_subdir: str) -> bool:
    dest_dir = RAW_DIR / dest_subdir
    for csv_path in dest_dir.glob("*.csv") if dest_dir.is_dir() else []:
        with open(csv_path) as f:
            if sum(1 for _ in f) > 1:  # more than just the header row
                return True
    return False


def fetch_one(agency: dict, fy: int, label: str, max_attempts: int = 3) -> None:
    date_range = fiscal_year_date_range(fy)
    dest_subdir = f"{label}/fy{fy}"
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Submitting bulk download for {agency['name']} FY{fy} ({date_range}), attempt {attempt}...")
            submission = submit_bulk_download(agency, date_range)
            print(f"  file_name: {submission.get('file_name')}")

            result = poll_until_ready(submission["status_url"])
            file_url = result.get("file_url") or submission.get("file_url")
            if not file_url:
                raise RuntimeError(f"No file_url in response: {result}")

            dest_dir = download_and_extract(file_url, dest_subdir)
            print(f"  extracted to {dest_dir} ({result.get('total_rows')} rows)")
            return
        except (EmptyResultError, RuntimeError, TimeoutError, requests.exceptions.RequestException) as e:
            print(f"  attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                raise
            time.sleep(10)


def main():
    for agency in AGENCIES:
        label = agency["name"].lower().replace(" ", "_")
        for fy in FISCAL_YEARS:
            if already_fetched(f"{label}/fy{fy}"):
                print(f"Skipping {agency['name']} FY{fy} (already fetched)")
                continue
            fetch_one(agency, fy, label)


if __name__ == "__main__":
    main()
