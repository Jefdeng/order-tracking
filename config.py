"""Settings, read from environment variables (and .env if present)."""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# gmail.readonly: read messages + history + watch. spreadsheets: gspread read/write.
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name) or default)
    return p if p.is_absolute() else BASE_DIR / p


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _opt(name: str) -> Optional[str]:
    raw = os.getenv(name, "").strip()
    return raw or None


@dataclass(frozen=True)
class Settings:
    gemini_api_key: Optional[str]
    gemini_model: str
    credentials_file: Path
    token_file: Path
    state_db: Path
    state_backend: str  # "sqlite" (local) or "firestore" (Cloud Run)
    gcp_project: Optional[str]
    google_token_json: Optional[str]  # token.json contents, for hosts without a writable disk
    review_tab: str
    log_tab: str
    pending_days: int
    sheet_id: Optional[str]
    worksheet_name: Optional[str]
    ingest_mode: str  # "poll" or "pubsub"
    poll_interval: int
    pubsub_topic: Optional[str]
    pubsub_audience: Optional[str]
    pubsub_service_account: Optional[str]
    dry_run: bool
    max_body_chars: int
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("INGEST_MODE", "poll").strip().lower()
        if mode not in ("poll", "pubsub"):
            raise ValueError("INGEST_MODE must be 'poll' or 'pubsub', got %r" % mode)
        return cls(
            gemini_api_key=_opt("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
            credentials_file=_path("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
            token_file=_path("GOOGLE_TOKEN_FILE", "token.json"),
            state_db=_path("STATE_DB", "state.db"),
            state_backend=os.getenv("STATE_BACKEND", "sqlite").strip().lower(),
            gcp_project=_opt("GOOGLE_CLOUD_PROJECT"),
            google_token_json=_opt("GOOGLE_TOKEN_JSON"),
            review_tab=os.getenv("REVIEW_TAB", "Needs Review"),
            log_tab=os.getenv("LOG_TAB", "Log"),
            pending_days=int(os.getenv("PENDING_DAYS", "3")),
            sheet_id=_opt("SHEET_ID"),
            worksheet_name=_opt("WORKSHEET_NAME"),
            ingest_mode=mode,
            poll_interval=int(os.getenv("POLL_INTERVAL_SECONDS", "15")),
            pubsub_topic=_opt("PUBSUB_TOPIC"),
            pubsub_audience=_opt("PUBSUB_AUDIENCE"),
            pubsub_service_account=_opt("PUBSUB_SERVICE_ACCOUNT"),
            dry_run=_bool("DRY_RUN", True),
            max_body_chars=int(os.getenv("MAX_BODY_CHARS", "12000")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


settings = Settings.from_env()
