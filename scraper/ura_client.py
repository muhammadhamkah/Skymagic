import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

URA_BASE = "https://www.ura.gov.sg"
TOKEN_ENDPOINT = f"{URA_BASE}/uraDataService/insertNewToken.action"
SERVICE_ENDPOINT = f"{URA_BASE}/uraDataService/invokeUraDS"
TOKEN_CACHE_PATH = ".ura_token.json"

MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def normalize_contract_date(raw: Optional[str]) -> Optional[str]:
    """URA returns dates like 'MAR-24'. Normalize to '2024-03' for sortable storage."""
    if not raw or len(raw) != 6 or "-" not in raw:
        return None
    mon, yr = raw.upper().split("-")
    if mon not in MONTH_MAP or len(yr) != 2:
        return None
    return f"20{yr}-{MONTH_MAP[mon]}"


class URAClient:
    def __init__(self, access_key: Optional[str] = None, token_cache: str = TOKEN_CACHE_PATH):
        self.access_key = access_key or os.getenv("URA_ACCESS_KEY")
        if not self.access_key:
            raise ValueError(
                "URA_ACCESS_KEY required. Get a free key at "
                "https://www.ura.gov.sg/maps/api and set it in your .env"
            )
        self.token_cache_path = Path(token_cache)
        self.session = requests.Session()

    def _load_cached_token(self) -> Optional[str]:
        if not self.token_cache_path.exists():
            return None
        try:
            data = json.loads(self.token_cache_path.read_text())
            expiry = datetime.fromisoformat(data["expiry"])
            if expiry > datetime.utcnow() + timedelta(minutes=5):
                return data["token"]
        except Exception:
            pass
        return None

    def _save_token(self, token: str):
        self.token_cache_path.write_text(json.dumps({
            "token": token,
            "expiry": (datetime.utcnow() + timedelta(hours=20)).isoformat(),
        }))

    def get_token(self) -> str:
        cached = self._load_cached_token()
        if cached:
            return cached
        logger.info("requesting new URA token")
        resp = self.session.get(
            TOKEN_ENDPOINT,
            headers={"AccessKey": self.access_key, "User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("Status") != "Success":
            raise RuntimeError(f"URA token request failed: {data}")
        token = data["Result"]
        self._save_token(token)
        return token

    def fetch_service(self, service: str, params: Optional[dict] = None) -> dict:
        token = self.get_token()
        merged = dict(params or {})
        merged["service"] = service
        resp = self.session.get(
            SERVICE_ENDPOINT,
            headers={
                "AccessKey": self.access_key,
                "Token": token,
                "User-Agent": "Mozilla/5.0",
            },
            params=merged,
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_private_residential_transactions(self) -> list[dict]:
        """Last ~36 months of private residential transactions across all 4 batches."""
        all_records = []
        for batch in (1, 2, 3, 4):
            logger.info(f"fetching URA private residential transactions batch {batch}/4")
            data = self.fetch_service("PMI_Resi_Transaction", {"batch": batch})
            if data.get("Status") != "Success":
                logger.warning(f"batch {batch} returned status={data.get('Status')}")
                continue
            for project in data.get("Result", []):
                meta = {
                    "project": project.get("project"),
                    "street": project.get("street"),
                    "marketSegment": project.get("marketSegment"),
                    "x": project.get("x"),
                    "y": project.get("y"),
                }
                for tx in project.get("transaction", []):
                    all_records.append({
                        **meta,
                        "contractDate": normalize_contract_date(tx.get("contractDate")),
                        "areaSQM": tx.get("area"),
                        "areaSQFT": tx.get("areaSqft"),
                        "price": tx.get("price"),
                        "propertyType": tx.get("propertyType"),
                        "tenure": tx.get("tenure"),
                        "typeOfArea": tx.get("typeOfArea"),
                        "typeOfSale": tx.get("typeOfSale"),
                        "noOfUnits": tx.get("noOfUnits"),
                        "floorRange": tx.get("floorRange"),
                        "district": tx.get("district"),
                    })
            time.sleep(1)
        return all_records
