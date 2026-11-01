"""Run the pipeline on existing mail: for testing and backfill.

DRY-RUN BY DEFAULT: shows what it would change and writes nothing (and records nothing, so the
mail isn't marked as handled). Add --apply to really update the sheet.

  python reprocess.py --since 2026-10-01                       # everything since Oct 1 (local time), dry-run
  python reprocess.py --since "2026-10-01 09:30" --until "2026-10-03 17:00"
  python reprocess.py --since 2026-09-15 --query "from:vendor@example.com" --limit 200
  python reprocess.py --since 2026-10-01 --apply               # write to the sheet, oldest email first
  python reprocess.py --query "subject:order newer_than:7d"    # a raw Gmail search also works

Dates are interpreted in your local time zone unless --tz is given (e.g. --tz America/Los_Angeles).
Emails are processed oldest first so the newest email wins on any field. Without --force, emails
this app already handled are skipped.
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


def to_epoch(text: str, tz: Optional[str]) -> int:
    dt = datetime.fromisoformat(text.strip().replace("T", " "))
    dt = dt.replace(tzinfo=ZoneInfo(tz)) if tz else dt.astimezone()  # naive -> local zone
    return int(dt.timestamp())


def build_query(since: Optional[str], until: Optional[str], query: Optional[str], tz: Optional[str]) -> str:
    """Gmail search string. `after:`/`before:` take epoch seconds, so times (not just dates) work."""
    parts = ["in:inbox"] if not query else []
    if query:
        parts.append(query)
    if since:
        parts.append("after:%d" % to_epoch(since, tz))
    if until:
        parts.append("before:%d" % to_epoch(until, tz))
    return " ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Test or backfill the pipeline on existing Gmail messages.")
    ap.add_argument("query_positional", nargs="?", help="raw Gmail search (same as --query)")
    ap.add_argument("--since", help="start, e.g. 2026-10-01 or '2026-10-01 09:30'")
    ap.add_argument("--until", help="end (exclusive), same formats")
    ap.add_argument("--query", help="extra Gmail search terms, e.g. 'from:vendor@example.com'")
    ap.add_argument("--tz", help="time zone for --since/--until, e.g. America/Los_Angeles (default: this computer's)")
    ap.add_argument("--limit", type=int, default=50, help="max emails to look at (default 50)")
    ap.add_argument("--apply", action="store_true", help="actually update the sheet (default is dry-run)")
    ap.add_argument("--force", action="store_true", help="also reprocess emails already handled")
    args = ap.parse_args(argv)

    # Decide the mode here, before settings are read, so .env can't silently turn writes on.
    os.environ["DRY_RUN"] = "false" if args.apply else "true"
    query = build_query(args.since, args.until, args.query or args.query_positional, args.tz)
    if not (args.since or args.until or args.query or args.query_positional):
        ap.error("give --since/--until or a search query, so you don't process the whole inbox by accident")

    import logging
    from config import settings
    from pipeline import reprocess
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("mode: %s | gmail search: %s | limit %d" % ("APPLY" if args.apply else "dry-run", query, args.limit))
    print(reprocess(query, args.limit, args.force))
    return 0


if __name__ == "__main__":
    sys.exit(main())
