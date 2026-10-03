import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gmail_client import extract_body, html_to_text, parse_message


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")  # Gmail omits padding


def test_prefers_plain_text_in_multipart_alternative():
    payload = {"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": b64("Your order #123 shipped, arriving Nov 3.")}},
        {"mimeType": "text/html", "body": {"data": b64("<p>HTML version</p>")}},
    ]}
    body, att = extract_body(payload)
    assert body == "Your order #123 shipped, arriving Nov 3." and att == []


def test_falls_back_to_html_and_lists_attachments():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "text/html", "body": {"data": b64("<style>p{}</style><div>Quote:</div><p>Faucet &amp; drain: $389</p>")}},
        {"mimeType": "application/pdf", "filename": "quote.pdf", "body": {"attachmentId": "x"}},
    ]}
    body, att = extract_body(payload)
    assert "Faucet & drain: $389" in body and "p{}" not in body
    assert att == ["quote.pdf"]


def test_html_to_text_collapses_whitespace():
    assert html_to_text("<div>a</div><div></div><div></div><div>b</div>") == "a\n\nb"


def test_parse_message_headers_date_and_truncation():
    msg = {"id": "m1", "threadId": "t1", "internalDate": "1790000000000", "payload": {
        "mimeType": "text/plain", "body": {"data": b64("x" * 100)},
        "headers": [{"name": "From", "value": "Vendor <v@example.com>"}, {"name": "Subject", "value": "Quote"}]}}
    email = parse_message(msg, max_chars=50)
    assert email.sender == "Vendor <v@example.com>" and email.subject == "Quote"
    assert email.body.endswith("[... truncated ...]") and len(email.date_iso) == 10
