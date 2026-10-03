"""Preflight: verifies each external dependency without changing anything. Run after setup."""
import sys

from config import settings

ok = True


def check(name, fn):
    global ok
    try:
        detail = fn()
        print("OK    %s%s" % (name, (" - " + str(detail)) if detail else ""))
    except Exception as e:
        ok = False
        print("FAIL  %s - %s" % (name, e))


def files():
    for p in (settings.credentials_file, settings.token_file):
        if not p.exists():
            raise RuntimeError("%s missing" % p.name)


def gmail():
    from gmail_client import GmailClient
    return GmailClient().svc.users().getProfile(userId="me").execute()["emailAddress"]


def sheet():
    from sheets import SheetClient, locate_table, tracked_rows
    sc = SheetClient()
    rows = sc.read()
    t = locate_table(rows)
    missing = [c for c in ("order_no", "item_sku", "status", "ordered", "shipped", "received", "notes") if c not in t.col]
    tabs = "Needs Review tab: %s, Log tab: %s" % ("yes" if sc.review.ws else "MISSING", "yes" if sc.log.ws else "no (optional)")
    return "header on row %d; %d rows with an order #/SKU; missing columns: %s; %s" % (
        t.header_idx + 1, len(tracked_rows(rows)), ", ".join(missing) or "none", tabs)


def gemini_key():
    from extractor import Extractor
    extractor = Extractor()  # keep a reference: the genai client closes when its owner is garbage-collected
    # models.get() succeeds even for models that refuse generation, so do a real 1-token call
    extractor.client.models.generate_content(model=settings.gemini_model, contents="Reply with: ok")
    return settings.gemini_model


def pubsub():
    if settings.ingest_mode != "pubsub":
        return "skipped (INGEST_MODE=poll)"
    if not settings.pubsub_topic:
        raise RuntimeError("PUBSUB_TOPIC not set")
    return settings.pubsub_topic


check("credentials.json / token.json present", files)
check("Gmail API (read profile)", gmail)
check("Google Sheet readable", sheet)
check("Gemini API key + model", gemini_key)
check("Pub/Sub config", pubsub)
print("\nDRY_RUN=%s  INGEST_MODE=%s" % (settings.dry_run, settings.ingest_mode))
sys.exit(0 if ok else 1)
