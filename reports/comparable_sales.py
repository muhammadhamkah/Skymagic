import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from scraper.store import Store


def hdb_comparable_sales(
    store: Store,
    town: str,
    flat_type: Optional[str] = None,
    months_back: int = 12,
    limit: int = 50,
) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(days=months_back * 31)).strftime("%Y-%m")
    sql = (
        "SELECT month, town, flat_type, block, street_name, storey_range, "
        "floor_area_sqm, resale_price, "
        "ROUND(resale_price / floor_area_sqm, 0) AS price_per_sqm "
        "FROM hdb_resale WHERE town = ? AND month >= ?"
    )
    params: list = [town.upper(), cutoff]
    if flat_type:
        sql += " AND flat_type = ?"
        params.append(flat_type.upper())
    sql += " ORDER BY month DESC, resale_price DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in store.query(sql, tuple(params))]


def hdb_summary(store: Store, town: str, flat_type: Optional[str] = None, months_back: int = 12) -> dict:
    cutoff = (datetime.utcnow() - timedelta(days=months_back * 31)).strftime("%Y-%m")
    sql = (
        "SELECT COUNT(*) AS n, "
        "ROUND(AVG(resale_price), 0) AS avg_price, "
        "ROUND(MIN(resale_price), 0) AS min_price, "
        "ROUND(MAX(resale_price), 0) AS max_price, "
        "ROUND(AVG(resale_price / floor_area_sqm), 0) AS avg_psm "
        "FROM hdb_resale WHERE town = ? AND month >= ?"
    )
    params: list = [town.upper(), cutoff]
    if flat_type:
        sql += " AND flat_type = ?"
        params.append(flat_type.upper())
    row = store.query(sql, tuple(params))[0]
    return dict(row)


def ura_comparable_sales(
    store: Store,
    project: Optional[str] = None,
    street: Optional[str] = None,
    district: Optional[str] = None,
    months_back: int = 12,
    limit: int = 50,
) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(days=months_back * 31)).strftime("%Y-%m")
    sql_parts = [
        "SELECT project, street, district, contractDate, areaSQM, areaSQFT, price, "
        "ROUND(price / NULLIF(areaSQFT, 0), 0) AS psf, "
        "propertyType, tenure, floorRange, typeOfSale "
        "FROM ura_transactions WHERE contractDate >= ?"
    ]
    params: list = [cutoff]
    if project:
        sql_parts.append("AND project LIKE ?")
        params.append(f"%{project.upper()}%")
    if street:
        sql_parts.append("AND street LIKE ?")
        params.append(f"%{street.upper()}%")
    if district:
        sql_parts.append("AND district = ?")
        params.append(str(district))
    sql_parts.append("ORDER BY contractDate DESC, price DESC LIMIT ?")
    params.append(limit)
    sql = " ".join(sql_parts)
    return [dict(r) for r in store.query(sql, tuple(params))]


def write_csv(rows: list[dict], path: str):
    if not rows:
        print(f"no rows for {path}")
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows -> {path}")
