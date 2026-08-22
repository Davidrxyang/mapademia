"""
Pull NIH ExPORTER bulk files: Projects (award metadata incl. CORE_PROJECT_NUM,
which matches USAspending's award_id_fain format), Publications (PMID ->
paper metadata), and Link tables (PMID <-> PROJECT_NUMBER). Together these
give genuine award-level linkage - which specific grant produced which
specific papers - without needing OpenAlex's metered API at all.

Simple synchronous downloads (unlike USAspending's async job queue), one
file per (kind, fiscal year). Source: https://reporter.nih.gov/exporter
"""

import time
import zipfile
from pathlib import Path

import requests

BASE = "https://reporter.nih.gov/exporter"
RAW_DIR = Path(__file__).resolve().parent.parent / "raw" / "nih_exporter"

FISCAL_YEARS = range(2016, 2027)
KINDS = ["projects", "publications", "linktables"]


def already_fetched(dest_dir: Path) -> bool:
    for csv_path in dest_dir.glob("*.csv") if dest_dir.is_dir() else []:
        with open(csv_path, encoding="latin-1") as f:
            if sum(1 for _ in f) > 1:
                return True
    return False


def fetch_one(kind: str, fy: int, max_attempts: int = 3) -> None:
    dest_dir = RAW_DIR / kind / f"fy{fy}"
    if already_fetched(dest_dir):
        print(f"Skipping {kind} FY{fy} (already fetched)")
        return

    url = f"{BASE}/{kind}/download/{fy}"
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Fetching {kind} FY{fy} (attempt {attempt})...")
            resp = requests.get(url, timeout=120)
            if resp.status_code == 404:
                # The current, still-open fiscal year isn't published as a
                # snapshot yet - not a transient error, retrying won't help.
                print(f"  not available yet (404), skipping FY{fy}")
                return
            resp.raise_for_status()
            dest_dir.mkdir(parents=True, exist_ok=True)
            zip_path = dest_dir / "download.zip"
            with open(zip_path, "wb") as f:
                f.write(resp.content)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
            zip_path.unlink()
            print(f"  extracted to {dest_dir}")
            return
        except (requests.exceptions.RequestException, zipfile.BadZipFile) as e:
            print(f"  attempt {attempt} failed: {e}")
            if attempt == max_attempts:
                raise
            time.sleep(10)


def main():
    for kind in KINDS:
        for fy in FISCAL_YEARS:
            fetch_one(kind, fy)


if __name__ == "__main__":
    main()
