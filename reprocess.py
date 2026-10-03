"""Run the pipeline on existing mail matching a Gmail search (testing / backfill).

  python reprocess.py "from:vendor@example.com" --limit 5
  python reprocess.py "in:inbox newer_than:7d" --limit 50 --force

Respects DRY_RUN. Without --force, skips messages already processed.
"""
import argparse
import logging

from config import settings
from pipeline import reprocess

parser = argparse.ArgumentParser()
parser.add_argument("query", help="Gmail search query")
parser.add_argument("--limit", type=int, default=10)
parser.add_argument("--force", action="store_true", help="reprocess even if already seen")
args = parser.parse_args()

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
print("dry_run=%s" % settings.dry_run)
print(reprocess(args.query, args.limit, args.force))
