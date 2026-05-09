"""Property data CLI. Run `python cli.py --help` for commands."""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from reports.comparable_sales import (
    hdb_comparable_sales,
    hdb_summary,
    ura_comparable_sales,
    write_csv,
)
from scraper.hdb_client import HDBClient
from scraper.store import Store

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def cmd_pull_hdb(args):
    store = Store(args.db)
    client = HDBClient()
    print(f"pulling HDB resale records since {args.since or 'all time'} (max={args.max or 'unlimited'})...")
    batch: list[dict] = []
    total = 0
    for record in client.fetch_all(since=args.since, max_records=args.max):
        batch.append(record)
        if len(batch) >= 1000:
            store.upsert_hdb(batch)
            total += len(batch)
            print(f"  ingested {total}...")
            batch = []
    if batch:
        store.upsert_hdb(batch)
        total += len(batch)
    print(f"done. {total} records processed. db stats: {store.stats()}")


def cmd_pull_ura(args):
    if not os.getenv("URA_ACCESS_KEY"):
        print(
            "ERROR: URA_ACCESS_KEY not set.\n"
            "  1. Sign up free at https://www.ura.gov.sg/maps/api\n"
            "  2. Copy .env.example to .env and paste your key in.\n"
        )
        sys.exit(1)
    from scraper.ura_client import URAClient
    store = Store(args.db)
    client = URAClient()
    records = client.fetch_private_residential_transactions()
    inserted = store.upsert_ura(records)
    print(f"fetched {len(records)} URA transactions, {inserted} new in db. stats: {store.stats()}")


def cmd_report_hdb(args):
    store = Store(args.db)
    summary = hdb_summary(store, town=args.town, flat_type=args.flat_type, months_back=args.months)
    print(f"\n=== HDB SUMMARY: {args.town} {args.flat_type or ''} (last {args.months} months) ===")
    print(f"  transactions: {summary['n']}")
    if summary["n"]:
        print(f"  avg price: SGD {summary['avg_price']:,.0f}")
        print(f"  range: SGD {summary['min_price']:,.0f} - {summary['max_price']:,.0f}")
        print(f"  avg PSM: SGD {summary['avg_psm']:,.0f}\n")
    rows = hdb_comparable_sales(
        store, town=args.town, flat_type=args.flat_type,
        months_back=args.months, limit=args.limit,
    )
    if args.out:
        write_csv(rows, args.out)
    else:
        for r in rows[:20]:
            print(
                f"  {r['month']} | {r['flat_type']:<8} | blk {r['block']:<5} {r['street_name']:<25} "
                f"| {r['storey_range']:<8} | {r['floor_area_sqm']}sqm | SGD {r['resale_price']:>10,.0f} "
                f"| PSM {r['price_per_sqm']:,.0f}"
            )
        if len(rows) > 20:
            print(f"  ... +{len(rows) - 20} more (use --out file.csv to export full list)")


def cmd_report_ura(args):
    store = Store(args.db)
    rows = ura_comparable_sales(
        store, project=args.project, street=args.street, district=args.district,
        months_back=args.months, limit=args.limit,
    )
    if args.out:
        write_csv(rows, args.out)
    else:
        if not rows:
            print("no matching transactions. try pulling URA data first: python cli.py pull-ura")
            return
        print(f"\n=== URA TRANSACTIONS ({len(rows)} matches, last {args.months} months) ===")
        for r in rows[:20]:
            print(
                f"  {r['contractDate']} | {r['project']:<30} | {r['propertyType']:<15} "
                f"| {r['areaSQFT']}sqft | SGD {r['price']:>12,.0f} | PSF {r['psf']:,.0f}"
            )
        if len(rows) > 20:
            print(f"  ... +{len(rows) - 20} more (use --out file.csv to export)")


def cmd_stats(args):
    store = Store(args.db)
    print(store.stats())


def main():
    parser = argparse.ArgumentParser(prog="property-data", description="Singapore property comp tool")
    parser.add_argument("--db", default="data/property.db")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("pull-hdb", help="Pull HDB resale data from data.gov.sg (no key needed)")
    p.add_argument("--since", help="Only ingest months >= YYYY-MM (e.g. 2024-01)")
    p.add_argument("--max", type=int, help="Cap number of records ingested")
    p.set_defaults(func=cmd_pull_hdb)

    p = sub.add_parser("pull-ura", help="Pull URA private residential transactions (needs URA_ACCESS_KEY)")
    p.set_defaults(func=cmd_pull_ura)

    p = sub.add_parser("report-hdb", help="HDB comparable sales for a town")
    p.add_argument("--town", required=True, help="e.g. 'ANG MO KIO', 'TAMPINES'")
    p.add_argument("--flat-type", help="e.g. '4 ROOM', '5 ROOM'")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", help="Write CSV to this path")
    p.set_defaults(func=cmd_report_hdb)

    p = sub.add_parser("report-ura", help="URA private residential comparable sales")
    p.add_argument("--project", help="Project name (partial match, e.g. 'Marina')")
    p.add_argument("--street", help="Street name (partial match)")
    p.add_argument("--district", help="District code (e.g. '09')")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--out", help="Write CSV to this path")
    p.set_defaults(func=cmd_report_ura)

    p = sub.add_parser("stats", help="Show DB row counts")
    p.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
