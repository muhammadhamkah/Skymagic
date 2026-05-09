import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_DB_PATH = "data/property.db"

URA_SCHEMA = """
CREATE TABLE IF NOT EXISTS ura_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    street TEXT,
    marketSegment TEXT,
    x REAL,
    y REAL,
    contractDate TEXT,
    areaSQM REAL,
    areaSQFT REAL,
    price REAL,
    propertyType TEXT,
    tenure TEXT,
    typeOfArea TEXT,
    typeOfSale TEXT,
    noOfUnits INTEGER,
    floorRange TEXT,
    district TEXT,
    UNIQUE(project, contractDate, price, areaSQM, floorRange, propertyType)
);
CREATE INDEX IF NOT EXISTS idx_ura_project ON ura_transactions(project);
CREATE INDEX IF NOT EXISTS idx_ura_district ON ura_transactions(district);
CREATE INDEX IF NOT EXISTS idx_ura_date ON ura_transactions(contractDate);
CREATE INDEX IF NOT EXISTS idx_ura_street ON ura_transactions(street);
"""

HDB_SCHEMA = """
CREATE TABLE IF NOT EXISTS hdb_resale (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month TEXT,
    town TEXT,
    flat_type TEXT,
    block TEXT,
    street_name TEXT,
    storey_range TEXT,
    floor_area_sqm REAL,
    flat_model TEXT,
    lease_commence_date TEXT,
    remaining_lease TEXT,
    resale_price REAL,
    UNIQUE(month, town, block, street_name, flat_type, storey_range, floor_area_sqm, resale_price)
);
CREATE INDEX IF NOT EXISTS idx_hdb_town ON hdb_resale(town);
CREATE INDEX IF NOT EXISTS idx_hdb_month ON hdb_resale(month);
CREATE INDEX IF NOT EXISTS idx_hdb_street ON hdb_resale(street_name);
CREATE INDEX IF NOT EXISTS idx_hdb_flat_type ON hdb_resale(flat_type);
"""


def _f(v) -> Optional[float]:
    if v in (None, "", "na", "NA"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    if v in (None, "", "na", "NA"):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


class Store:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(URA_SCHEMA)
        self.conn.executescript(HDB_SCHEMA)

    def upsert_ura(self, records: Iterable[dict]) -> int:
        cur = self.conn.cursor()
        inserted = 0
        for r in records:
            cur.execute(
                """INSERT OR IGNORE INTO ura_transactions
                (project, street, marketSegment, x, y, contractDate, areaSQM, areaSQFT,
                 price, propertyType, tenure, typeOfArea, typeOfSale, noOfUnits, floorRange, district)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("project"), r.get("street"), r.get("marketSegment"),
                    _f(r.get("x")), _f(r.get("y")),
                    r.get("contractDate"),
                    _f(r.get("areaSQM")), _f(r.get("areaSQFT")),
                    _f(r.get("price")),
                    r.get("propertyType"), r.get("tenure"), r.get("typeOfArea"),
                    r.get("typeOfSale"),
                    _i(r.get("noOfUnits")),
                    r.get("floorRange"), r.get("district"),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        self.conn.commit()
        return inserted

    def upsert_hdb(self, records: Iterable[dict]) -> int:
        cur = self.conn.cursor()
        inserted = 0
        for r in records:
            cur.execute(
                """INSERT OR IGNORE INTO hdb_resale
                (month, town, flat_type, block, street_name, storey_range, floor_area_sqm,
                 flat_model, lease_commence_date, remaining_lease, resale_price)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.get("month"), r.get("town"), r.get("flat_type"),
                    r.get("block"), r.get("street_name"), r.get("storey_range"),
                    _f(r.get("floor_area_sqm")),
                    r.get("flat_model"), r.get("lease_commence_date"),
                    r.get("remaining_lease"),
                    _f(r.get("resale_price")),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        self.conn.commit()
        return inserted

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def stats(self) -> dict:
        ura_count = self.conn.execute("SELECT COUNT(*) FROM ura_transactions").fetchone()[0]
        hdb_count = self.conn.execute("SELECT COUNT(*) FROM hdb_resale").fetchone()[0]
        return {"ura_transactions": ura_count, "hdb_resale": hdb_count}
