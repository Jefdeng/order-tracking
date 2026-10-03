"""One-time Google sign-in. Opens a browser, then writes token.json (Gmail read + Sheets)."""
from google_auth_oauthlib.flow import InstalledAppFlow

from config import GOOGLE_SCOPES, settings

if not settings.credentials_file.exists():
    raise SystemExit("%s not found. Download your OAuth Desktop client JSON from GCP and save it there." % settings.credentials_file)

flow = InstalledAppFlow.from_client_secrets_file(str(settings.credentials_file), GOOGLE_SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
settings.token_file.write_text(creds.to_json())
settings.token_file.chmod(0o600)
print("Saved %s" % settings.token_file)
