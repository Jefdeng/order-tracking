"""FastAPI app: Gmail -> LLM -> Google Sheet.

Local:   INGEST_MODE=poll   -> checks Gmail every POLL_INTERVAL_SECONDS. Run: uvicorn main:app --port 8000
Cloud:   INGEST_MODE=pubsub -> Cloud Run, nothing runs in the background. Work happens inside requests:
           POST /api/webhook/gmail   Pub/Sub push (Gmail says "mailbox changed")
           POST /tasks/poll          Cloud Scheduler backup check, in case a push is missed
           POST /tasks/renew-watch   Cloud Scheduler, daily: Gmail watches expire after 7 days
         Cloud Run IAM keeps these private (--no-allow-unauthenticated).
"""
import asyncio
import base64
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request

from config import settings
from pipeline import ensure_watch, process_new_mail

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("main")


async def _poll_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_new_mail)
        except Exception:
            logger.exception("Poll run failed")
        await asyncio.sleep(settings.poll_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting in %s mode (dry_run=%s, model=%s, state=%s)", settings.ingest_mode, settings.dry_run,
                settings.gemini_model, settings.state_backend)
    task = asyncio.create_task(_poll_loop()) if settings.ingest_mode == "poll" else None
    yield
    if task:
        task.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _verify_pubsub_token(authorization: Optional[str]) -> None:
    """Optional extra check (PUBSUB_AUDIENCE set): require the Google-signed OIDC token Pub/Sub attaches.
    Not needed on Cloud Run, where IAM already rejects callers that aren't allowed to invoke the service."""
    if not settings.pubsub_audience:
        return
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        claims = id_token.verify_oauth2_token(
            authorization.split(" ", 1)[1], g_requests.Request(), audience=settings.pubsub_audience
        )
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid token")
    if settings.pubsub_service_account and claims.get("email") != settings.pubsub_service_account:
        raise HTTPException(status_code=403, detail="unexpected service account")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "mode": settings.ingest_mode, "dry_run": settings.dry_run}


async def _run_pipeline() -> dict:
    # Done inside the request: Cloud Run throttles CPU once a response is sent, so no background work.
    # A failure returns 500 and Pub/Sub / Scheduler retry the delivery.
    try:
        return await asyncio.to_thread(process_new_mail)
    except Exception:
        logger.exception("Pipeline run failed")
        raise HTTPException(status_code=500, detail="pipeline run failed")


@app.post("/api/webhook/gmail")
async def gmail_webhook(request: Request) -> dict:
    await asyncio.to_thread(_verify_pubsub_token, request.headers.get("authorization"))
    try:
        envelope = await request.json()
        payload = json.loads(base64.b64decode(envelope["message"]["data"]))
    except (ValueError, KeyError, TypeError):
        # Malformed: ack it (2xx) so Pub/Sub doesn't redeliver garbage forever.
        logger.warning("Ignoring malformed Pub/Sub push")
        return {"status": "ignored"}
    logger.info("Gmail push: %s historyId=%s", payload.get("emailAddress"), payload.get("historyId"))
    # The push only says "something changed"; the pipeline fetches changes from its stored historyId.
    return {"status": "ok", "result": await _run_pipeline()}


@app.post("/tasks/poll")
async def task_poll() -> dict:
    return {"status": "ok", "result": await _run_pipeline()}


@app.post("/tasks/renew-watch")
async def task_renew_watch() -> dict:
    try:
        await asyncio.to_thread(ensure_watch)
    except Exception:
        logger.exception("Watch renewal failed")
        raise HTTPException(status_code=500, detail="watch renewal failed")
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000)
