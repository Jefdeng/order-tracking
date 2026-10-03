"""Gmail access: OAuth credentials, watch, history polling, message parsing."""
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import GOOGLE_SCOPES, settings

logger = logging.getLogger(__name__)


class HistoryExpired(Exception):
    """Gmail no longer has history for the stored historyId (older than ~a week)."""


@dataclass
class InboundEmail:
    id: str
    thread_id: str
    sender: str
    subject: str
    date_iso: str
    body: str
    attachments: List[str] = field(default_factory=list)


_creds: Optional[Credentials] = None


def get_credentials() -> Credentials:
    """Load the Google login (GOOGLE_TOKEN_JSON secret on Cloud Run, else token.json) and keep it fresh.

    Run auth.py once locally to create token.json. The refresh token itself never changes, so a host
    with a read-only or ephemeral disk only needs the secret, not write-back.
    """
    global _creds
    if _creds is None:
        if settings.google_token_json:
            _creds = Credentials.from_authorized_user_info(json.loads(settings.google_token_json), GOOGLE_SCOPES)
        elif settings.token_file.exists():
            _creds = Credentials.from_authorized_user_file(str(settings.token_file), GOOGLE_SCOPES)
        else:
            raise RuntimeError("%s not found. Run `python auth.py` once to sign in to Google." % settings.token_file)
    if not _creds.valid:
        if not _creds.refresh_token:
            raise RuntimeError("Google login has no refresh token. Re-run `python auth.py`.")
        _creds.refresh(Request())
        if not settings.google_token_json:
            try:
                settings.token_file.write_text(_creds.to_json())
            except OSError:
                logger.warning("Could not save refreshed token to %s", settings.token_file)
    return _creds


# ---------- message parsing (pure functions, unit-tested) ----------

class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _walk(part: Dict[str, Any], plain: List[str], html: List[str], attachments: List[str]) -> None:
    mime = part.get("mimeType", "")
    data = part.get("body", {}).get("data")
    if part.get("filename"):
        attachments.append(part["filename"])
    elif data and mime == "text/plain":
        plain.append(_decode(data))
    elif data and mime == "text/html":
        html.append(_decode(data))
    for sub in part.get("parts") or []:
        _walk(sub, plain, html, attachments)


def extract_body(payload: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Return (body_text, attachment_filenames). Prefers text/plain, falls back to HTML."""
    plain: List[str] = []
    html: List[str] = []
    attachments: List[str] = []
    _walk(payload, plain, html, attachments)
    text = "\n".join(plain).strip()
    if len(text) < 20 and html:
        text = html_to_text("\n".join(html))
    return text, attachments


def parse_message(msg: Dict[str, Any], max_chars: int) -> InboundEmail:
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    body, attachments = extract_body(payload)
    if len(body) > max_chars:
        logger.warning("Message %s body truncated from %d to %d chars", msg["id"], len(body), max_chars)
        body = body[:max_chars] + "\n[... truncated ...]"
    try:
        sent = date.fromtimestamp(int(msg["internalDate"]) / 1000).isoformat()
    except (KeyError, ValueError):
        sent = datetime.now().date().isoformat()
    return InboundEmail(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender=headers.get("from", ""),
        subject=headers.get("subject", "(no subject)"),
        date_iso=sent,
        body=body,
        attachments=attachments,
    )


# ---------- API wrapper ----------

class GmailClient:
    def __init__(self, creds: Optional[Credentials] = None):
        # A fresh service per run: googleapiclient services are not thread-safe.
        self.svc = build("gmail", "v1", credentials=creds or get_credentials(), cache_discovery=False)

    def profile_history_id(self) -> str:
        return str(self.svc.users().getProfile(userId="me").execute()["historyId"])

    def start_watch(self, topic: str) -> Dict[str, Any]:
        """Register (or renew) Gmail push notifications to a Pub/Sub topic. Expires in ~7 days."""
        body = {"topicName": topic, "labelIds": ["INBOX"], "labelFilterBehavior": "INCLUDE"}
        return self.svc.users().watch(userId="me", body=body).execute()

    def new_message_ids(self, start_history_id: str) -> Tuple[List[str], str]:
        """Messages added to INBOX since start_history_id, plus the newest historyId."""
        ids: List[str] = []
        seen = set()
        latest = start_history_id
        page_token = None
        while True:
            try:
                resp = self.svc.users().history().list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                    pageToken=page_token,
                ).execute()
            except HttpError as e:
                if e.resp.status == 404:
                    raise HistoryExpired(str(e)) from e
                raise
            for h in resp.get("history", []):
                for added in h.get("messagesAdded", []):
                    msg = added["message"]
                    if "INBOX" in msg.get("labelIds", []) and msg["id"] not in seen:
                        seen.add(msg["id"])
                        ids.append(msg["id"])
            latest = str(resp.get("historyId", latest))
            page_token = resp.get("nextPageToken")
            if not page_token:
                return ids, latest

    def search_message_ids(self, query: str, limit: int = 25) -> List[str]:
        resp = self.svc.users().messages().list(userId="me", q=query, maxResults=limit).execute()
        return [m["id"] for m in resp.get("messages", [])]

    def get_message(self, msg_id: str) -> InboundEmail:
        msg = self.svc.users().messages().get(userId="me", id=msg_id, format="full").execute()
        return parse_message(msg, settings.max_body_chars)
