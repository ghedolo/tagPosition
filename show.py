import sys
import os
import json
import argparse
from collections import defaultdict

ARCHIVE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "positions.json")


def load_entries(tag_filter=None, date_from=None, date_to=None):
    if not os.path.exists(ARCHIVE_PATH):
        print(f"No archive found at {ARCHIVE_PATH}. Run poller.py first.")
        sys.exit(0)

    entries = []
    with open(ARCHIVE_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if tag_filter and entry.get("tag", "").lower() != tag_filter.lower():
                continue
            if date_from and entry.get("location_time", "") < date_from:
                continue
            if date_to and entry.get("location_time", "") > date_to:
                continue

            entries.append(entry)

    return entries


def print_summary(entries):
    by_tag = defaultdict(list)
    for e in entries:
        by_tag[e.get("tag", "unknown")].append(e)

    total = sum(len(v) for v in by_tag.values())
    print(f"Archive: {ARCHIVE_PATH}")
    print(f"Total entries: {total}")
    print()

    for tag, tag_entries in sorted(by_tag.items()):
        tag_entries_sorted = sorted(tag_entries, key=lambda e: e.get("location_time", ""), reverse=True)
        latest = tag_entries_sorted[0]

        print(f"  Tag : {tag}  ({len(tag_entries)} entries)")
        print(f"  Last: {latest.get('location_time', '?')}  (polled {latest.get('polled_at', '?')})")

        if latest.get("status") == "SEMANTIC":
            print(f"  Loc : {latest.get('semantic_name', '?')}")
        else:
            lat = latest.get("lat")
            lon = latest.get("lon")
            acc = latest.get("accuracy_m")
            status = latest.get("status", "?")
            print(f"  Loc : lat={lat}  lon={lon}  acc={acc}m  [{status}]")
            print(f"  Map : https://www.google.com/maps/search/?api=1&query={lat},{lon}")

        print()


def main():
    parser = argparse.ArgumentParser(description="Show tag position archive")
    parser.add_argument("--tag", help="Filter by tag name (case-insensitive)")
    parser.add_argument("--from", dest="date_from", help="Filter entries from this date (ISO 8601, e.g. 2026-05-01)")
    parser.add_argument("--to", dest="date_to", help="Filter entries up to this date (ISO 8601)")
    parser.add_argument("--all", action="store_true", help="Print all entries instead of summary")
    args = parser.parse_args()

    entries = load_entries(args.tag, args.date_from, args.date_to)

    if not entries:
        print("No entries match the given filters.")
        sys.exit(0)

    if args.all:
        for e in entries:
            print(json.dumps(e))
    else:
        print_summary(entries)


if __name__ == "__main__":
    main()
