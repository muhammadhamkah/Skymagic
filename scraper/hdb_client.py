import csv
import io
import logging
import os
import time
from typing import Iterable, Optional

import requests

logger = logging.getLogger(__name__)

# data.gov.sg's legacy ckan API (datastore_search) was retired in 2024.
# Current API uses an async initiate-download / poll-download flow.
DATA_GOV_API_BASE = "https://api-open.data.gov.sg/v1"

# HDB Resale Flat Prices (Jan 2017 onwards). If data.gov.sg rotates the dataset,
# find the new ID at https://data.gov.sg/datasets and override via HDB_RESALE_RESOURCE_ID.
DEFAULT_DATASET_ID = "d_8b84c4ee58e3cfc0ece0d773c8ca6abc"


class HDBClient:
    def __init__(self, dataset_id: Optional[str] = None):
        self.dataset_id = (
            dataset_id
            or os.getenv("HDB_RESALE_RESOURCE_ID")
            or DEFAULT_DATASET_ID
        )
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def _initiate_download(self) -> dict:
        url = f"{DATA_GOV_API_BASE}/public/api/datasets/{self.dataset_id}/initiate-download"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _poll_download(self, max_attempts: int = 60, interval: float = 2.0) -> str:
        url = f"{DATA_GOV_API_BASE}/public/api/datasets/{self.dataset_id}/poll-download"
        for attempt in range(max_attempts):
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data", {}) or {}
            if data.get("url"):
                return data["url"]
            status = (data.get("status") or "").upper()
            if status in ("FAILED", "ERROR"):
                raise RuntimeError(f"data.gov.sg download failed: {payload}")
            time.sleep(interval)
        raise RuntimeError(f"data.gov.sg poll-download timed out after {max_attempts * interval}s")

    def download_csv(self) -> str:
        logger.info(f"initiating download for dataset {self.dataset_id}")
        self._initiate_download()
        url = self._poll_download()
        logger.info("downloading CSV...")
        resp = self.session.get(url, timeout=180)
        resp.raise_for_status()
        return resp.text

    def fetch_all(
        self,
        since: Optional[str] = None,
        max_records: Optional[int] = None,
    ) -> Iterable[dict]:
        """Yield all HDB resale records. Optionally filter to month >= since (YYYY-MM)."""
        csv_text = self.download_csv()
        reader = csv.DictReader(io.StringIO(csv_text))
        yielded = 0
        for row in reader:
            if since and (row.get("month") or "") < since:
                continue
            yield row
            yielded += 1
            if max_records and yielded >= max_records:
                return
