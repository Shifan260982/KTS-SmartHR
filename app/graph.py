"""Microsoft Graph, as SmartHR's own app identity, on one mailbox only (design: "Secure access to the mailbox").

The app signs in with a certificate (client credentials) and works on SMARTHR_MAILBOX, e.g. noreply@kiaratech.ai.
Exchange Online RBAC for Applications limits it to that mailbox; see docs/SmartHR_Email_Setup.pdf.
Sending follows the design's crash-safe order: create a draft, save its id, then send that draft.
"""
import base64
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from functools import cache
from zoneinfo import ZoneInfo

log = logging.getLogger("smarthr")

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT_SECONDS = 30
SEARCH_DAYS = 7  # free slots are looked for in the coming week
SLOT_STEP_MINUTES = 15
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

MAILBOX = os.getenv("SMARTHR_MAILBOX", "").strip() or None
TENANT_ID = os.getenv("SMARTHR_TENANT_ID", "").strip()
CLIENT_ID = os.getenv("SMARTHR_CLIENT_ID", "").strip()
CERT_PATH = os.getenv("SMARTHR_CERT_PATH", "").strip()  # PEM with the private key; Key Vault in production
CERT_THUMBPRINT = os.getenv("SMARTHR_CERT_THUMBPRINT", "").strip()
TIMEZONE = ZoneInfo(os.getenv("SMARTHR_TIMEZONE", "Asia/Kolkata"))
WORK_START, WORK_END = (time.fromisoformat(t) for t in os.getenv("SMARTHR_WORK_HOURS", "09:00-18:00").split("-"))
# Test mode: every candidate email and invite goes to this address instead (e.g. the HR tester's own), so testing never
# reaches a real candidate. Applied here, under every route, so nothing can bypass it.
REDIRECT_TO = os.getenv("SMARTHR_EMAIL_REDIRECT", "").strip() or None


class GraphError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(MAILBOX and TENANT_ID and CLIENT_ID and CERT_PATH and CERT_THUMBPRINT)


def find_email(text: str) -> str | None:
    """The first email address in a resume, which is nearly always the candidate's own (in the header)."""
    match = EMAIL.search(text or "")
    return match.group().rstrip(".") if match else None


@cache
def _app():
    import msal
    with open(CERT_PATH, encoding="utf-8") as f:
        key = f.read()
    return msal.ConfidentialClientApplication(
        CLIENT_ID, authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential={"private_key": key, "thumbprint": CERT_THUMBPRINT})


def _token() -> str:
    if not configured():
        raise GraphError(503, "The SmartHR mailbox is not set up (SMARTHR_MAILBOX and the app sign-in settings)")
    result = _app().acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])  # cached by MSAL
    if "access_token" not in result:
        raise GraphError(503, f"SmartHR could not sign in to Microsoft 365: {result.get('error_description', result)}")
    return result["access_token"]


def _call(method: str, path: str, body: dict | None = None, prefer: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json",
               **({"Prefer": prefer} if prefer else {})}
    request = urllib.request.Request(f"{GRAPH}/users/{urllib.parse.quote(MAILBOX or '')}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())["error"]["message"]
        except (ValueError, KeyError, TypeError):
            detail = error.reason
        log.warning("Graph %s %s failed: %s %s", method, path.split("?")[0], error.code, detail)
        raise GraphError(error.code, detail) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise GraphError(503, f"Microsoft Graph is unreachable ({error})") from error


def check() -> str:
    """Proves sign-in and mailbox access; returns the mailbox address."""
    return _call("GET", "?$select=mail")["mail"]


# ---------- sending ----------

def _deliver(to: str, subject: str) -> tuple[str, str]:
    """(recipient, subject) after test mode: redirected mail says who it was meant for."""
    return (REDIRECT_TO, f"[TEST for {to}] {subject}") if REDIRECT_TO else (to, subject)


def create_draft(to: str, subject: str, body: str) -> str:
    to, subject = _deliver(to, subject)
    return _call("POST", "/messages", {"subject": subject, "body": {"contentType": "Text", "content": body},
                                       "toRecipients": [{"emailAddress": {"address": to}}]})["id"]


def send_draft(draft_id: str) -> None:
    _call("POST", f"/messages/{draft_id}/send")


def was_sent(draft_id: str) -> bool:
    """A saved draft that is gone or no longer a draft was already sent (sending moves it to Sent Items)."""
    try:
        return not _call("GET", f"/messages/{draft_id}?$select=isDraft").get("isDraft", True)
    except GraphError as error:
        if error.status == 404:
            return True
        raise


# ---------- calendar ----------

def _graph_time(moment: datetime) -> dict:
    return {"dateTime": moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"}


def free_slots(interviewer: str, minutes: int, count: int = 3) -> list[dict]:
    """Next free slots of the interviewer in working hours (SMARTHR_WORK_HOURS, Mon-Fri), soonest first.

    Reads the interviewer's free/busy through the SmartHR mailbox (getSchedule), which only needs the organisation's
    default free/busy sharing, not access to the interviewer's mailbox.
    """
    start = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    answer = _call("POST", "/calendar/getSchedule", {
        "schedules": [interviewer], "startTime": _graph_time(start),
        "endTime": _graph_time(start + timedelta(days=SEARCH_DAYS)), "availabilityViewInterval": SLOT_STEP_MINUTES})
    schedule = (answer.get("value") or [{}])[0]
    if "error" in schedule:
        raise GraphError(403, f"Cannot read {interviewer}'s calendar: {schedule['error'].get('message', 'no access')}")
    return slots_from_view(schedule.get("availabilityView", ""), start, minutes, count)


def slots_from_view(view: str, start: datetime, minutes: int, count: int) -> list[dict]:
    """Free runs in an availabilityView ('0' = free per SLOT_STEP_MINUTES cell) inside local working hours."""
    need, slots, i = -(-minutes // SLOT_STEP_MINUTES), [], 0
    while i + need <= len(view) and len(slots) < count:
        begin = start + timedelta(minutes=i * SLOT_STEP_MINUTES)
        end = begin + timedelta(minutes=minutes)
        local_begin, local_end = begin.astimezone(TIMEZONE), end.astimezone(TIMEZONE)
        in_hours = (local_begin.weekday() < 5 and local_begin.date() == local_end.date()
                    and WORK_START <= local_begin.time() and local_end.time() <= WORK_END)
        if in_hours and set(view[i:i + need]) == {"0"}:
            slots.append({"start": begin, "end": end})
            i += need  # next slot starts after this one
        else:
            i += 1
    return slots


def create_interview(to: str, interviewer: str, subject: str, body: str, start: datetime, end: datetime,
                     transaction_id: str) -> dict:
    """A Teams meeting organised by the SmartHR mailbox; Outlook sends the invite to the candidate and interviewer."""
    to, subject = _deliver(to, subject)
    event = _call("POST", "/events", {
        "subject": subject, "body": {"contentType": "Text", "content": body},
        "start": _graph_time(start), "end": _graph_time(end),
        "attendees": [{"emailAddress": {"address": to}, "type": "required"},
                      {"emailAddress": {"address": interviewer}, "type": "required"}],
        "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness", "allowNewTimeProposals": True,
        "transactionId": transaction_id,  # a retry with the same id does not create a second meeting
    })
    return {"event_id": event["id"], "join_url": (event.get("onlineMeeting") or {}).get("joinUrl")}


# ---------- receiving ----------

RESUME_TYPES = (".pdf", ".docx", ".txt")


def inbox(limit: int = 25) -> list[dict]:
    """Oldest unprocessed emails first; processed ones are moved out of the inbox, so the inbox is the work list."""
    return _call("GET", f"/mailFolders/inbox/messages?$top={limit}&$orderby=receivedDateTime"
                        "&$select=id,subject,from,replyTo,hasAttachments,body",
                 prefer='outlook.body-content-type="text"').get("value", [])  # the form body as plain text


MAX_FORM_FIELDS, MAX_FIELD_CHARS = 40, 1000
_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ()/&.,'?-]{0,59}?)\s*(?::|\t)\s*(.+?)\s*$")


def form_fields(text: str) -> list[dict]:
    """The website form's "Label: value" lines, in order. Any fields: each job's form can ask different things."""
    fields = []
    for line in (text or "").splitlines():
        match = _FIELD.match(line)
        if match and not match.group(2).startswith("//"):  # "https://..." on its own is not a field
            fields.append({"label": match.group(1), "value": match.group(2)[:MAX_FIELD_CHARS]})
    return fields[:MAX_FORM_FIELDS]


def _field(fields: list[dict], *words: str) -> str | None:
    return next((f["value"] for f in fields if any(w in f["label"].lower() for w in words)), None)


def applicant(message: dict) -> dict:
    """Candidate email, name and form details of an application email sent by the website (from SmartHR's own
    mailbox, so the sender is never the candidate): Reply-To first, then the form's email field."""
    fields = form_fields((message.get("body") or {}).get("content", ""))
    own = (MAILBOX or "").lower()
    addresses = [r.get("emailAddress", {}).get("address", "") for r in message.get("replyTo") or []]
    addresses += [m.group() for v in [_field(fields, "email")] if v for m in [EMAIL.search(v)] if m]
    addresses.append(((message.get("from") or {}).get("emailAddress") or {}).get("address", ""))
    email = next((a for a in addresses if a and a.lower() != own and EMAIL.fullmatch(a)), None)
    first, last = _field(fields, "first name"), _field(fields, "last name", "surname")
    name = " ".join(n for n in (first, last) if n) or _field(fields, "full name", "name")
    return {"candidate_email": email, "candidate_name": name, "form": fields}


def resume_attachments(message_id: str) -> list[tuple[str, bytes]]:
    """(filename, bytes) of each attached resume file; inline images such as signature logos are skipped."""
    items = _call("GET", f"/messages/{message_id}/attachments").get("value", [])
    return [(a["name"], base64.b64decode(a["contentBytes"])) for a in items
            if a.get("@odata.type") == "#microsoft.graph.fileAttachment" and not a.get("isInline")
            and a.get("name", "").lower().endswith(RESUME_TYPES) and a.get("contentBytes")]


@cache
def folder_id(name: str) -> str:
    """Id of a top-level mail folder, created on first use."""
    quoted = name.replace("'", "''")
    query = urllib.parse.quote(f"displayName eq '{quoted}'")
    found = _call("GET", f"/mailFolders?$filter={query}").get("value", [])
    return found[0]["id"] if found else _call("POST", "/mailFolders", {"displayName": name})["id"]


def move(message_id: str, folder: str) -> None:
    _call("POST", f"/messages/{message_id}/move", {"destinationId": folder_id(folder)})
