"""Microsoft Graph layer and mail ingestor, with Graph faked. Only @example.com addresses: never real resume emails."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import graph, main
from app.schemas import InterviewIn

CANDIDATE, INTERVIEWER, TESTER = "candidate@example.com", "hr.interviewer@example.com", "hr.tester@example.com"


@pytest.fixture
def calls(monkeypatch):
    """Records every Graph call; answers come from the `answers` dict by path prefix."""
    made, answers = [], {}

    def call(method, path, body=None):
        made.append((method, path, body))
        return next((v for k, v in answers.items() if path.startswith(k)), {})
    monkeypatch.setattr(graph, "_call", call)
    monkeypatch.setattr(graph, "REDIRECT_TO", None)
    return made, answers


def test_find_email_takes_the_first_address():
    assert graph.find_email("Asha K | asha.k+jobs@example.co.in. | +91 98") == "asha.k+jobs@example.co.in"
    assert graph.find_email("no contact here") is None


def test_free_slots_skip_busy_time_weekends_and_hours_outside_work(monkeypatch):
    monkeypatch.setattr(graph, "TIMEZONE", timezone.utc)
    monkeypatch.setattr(graph, "WORK_START", datetime.strptime("09:00", "%H:%M").time())
    monkeypatch.setattr(graph, "WORK_END", datetime.strptime("10:30", "%H:%M").time())
    friday_8 = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
    # Fri from 08:00 in 15-minute cells: 08:00-09:15 free, 09:15 busy, then free (work hours end 10:30)
    view = "00000" + "2" + "0000"
    view += "0" * (4 * 24 * 3 - len(view))  # rest of Friday and the weekend free; Monday not reached
    slots = graph.slots_from_view(view, friday_8, 30, 3)

    assert [s["start"].strftime("%a %H:%M") for s in slots] == ["Fri 09:30", "Fri 10:00"]  # 09:00 is cut by busy 09:15


def test_free_slots_reads_the_interviewers_free_busy_through_the_mailbox(calls):
    made, answers = calls
    answers["/calendar/getSchedule"] = {"value": [{"availabilityView": ""}]}
    graph.free_slots(INTERVIEWER, 45)
    method, path, body = made[0]
    assert (method, path, body["schedules"], body["availabilityViewInterval"]) == ("POST", "/calendar/getSchedule",
                                                                                   [INTERVIEWER], 15)
    answers["/calendar/getSchedule"] = {"value": [{"error": {"message": "no free/busy sharing"}}]}
    with pytest.raises(graph.GraphError):
        graph.free_slots(INTERVIEWER, 45)


def test_interview_is_a_teams_meeting_with_candidate_and_interviewer_and_an_idempotency_key(calls):
    made, answers = calls
    answers["/events"] = {"id": "evt1", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/x"}}
    ist = timezone(timedelta(hours=5, minutes=30))
    meeting = graph.create_interview(CANDIDATE, INTERVIEWER, "Interview", "Hello", datetime(2026, 11, 30, 10, 0, tzinfo=ist),
                                     datetime(2026, 11, 30, 10, 30, tzinfo=ist), "smarthr-7-abc")
    sent = made[0][2]
    assert meeting == {"event_id": "evt1", "join_url": "https://teams.microsoft.com/l/x"}
    assert sent["start"] == {"dateTime": "2026-11-30T04:30:00", "timeZone": "UTC"} and sent["transactionId"] == "smarthr-7-abc"
    assert sent["isOnlineMeeting"] and sent["onlineMeetingProvider"] == "teamsForBusiness"
    assert [a["emailAddress"]["address"] for a in sent["attendees"]] == [CANDIDATE, INTERVIEWER]


def test_test_mode_sends_mail_and_invites_to_the_tester_never_the_candidate(calls, monkeypatch):
    made, answers = calls
    answers["/messages"], answers["/events"] = {"id": "draft1"}, {"id": "evt1"}
    monkeypatch.setattr(graph, "REDIRECT_TO", TESTER)
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    graph.create_draft(CANDIDATE, "Shortlisted", "Hi")
    graph.create_interview(CANDIDATE, INTERVIEWER, "Interview", "Hi", soon, soon + timedelta(minutes=30), "t1")

    mail, event = made[0][2], made[1][2]
    assert mail["toRecipients"] == [{"emailAddress": {"address": TESTER}}]
    assert mail["subject"] == f"[TEST for {CANDIDATE}] Shortlisted"
    assert [a["emailAddress"]["address"] for a in event["attendees"]] == [TESTER, INTERVIEWER]
    assert CANDIDATE not in json.dumps([mail["toRecipients"], event["attendees"]])


def test_a_saved_draft_that_is_gone_or_sent_counts_as_sent(monkeypatch):
    def gone(method, path, body=None):
        raise graph.GraphError(404, "not found")
    monkeypatch.setattr(graph, "_call", gone)
    assert graph.was_sent("draft1")
    monkeypatch.setattr(graph, "_call", lambda method, path, body=None: {"isDraft": True})
    assert not graph.was_sent("draft1")


def test_interview_input_rejects_past_naive_or_overlong_times():
    soon = datetime.now(timezone.utc) + timedelta(days=1)
    ok = {"to": CANDIDATE, "interviewer": INTERVIEWER, "subject": "s", "body": "b"}
    InterviewIn(**ok, start=soon, end=soon + timedelta(minutes=30))
    for start, end in [(soon - timedelta(days=2), soon - timedelta(days=2) + timedelta(minutes=30)),
                       (soon.replace(tzinfo=None), (soon + timedelta(minutes=30)).replace(tzinfo=None)),
                       (soon, soon + timedelta(hours=5))]:
        with pytest.raises(ValidationError):
            InterviewIn(**ok, start=start, end=end)
    with pytest.raises(ValidationError):
        InterviewIn(**{**ok, "to": "not an email"}, start=soon, end=soon + timedelta(minutes=30))


class _Jobs:
    def __init__(self, jobs):
        self.jobs = {j["code"]: j for j in jobs}

    def find_one(self, query):
        return self.jobs.get(query["code"])


class _Db:
    def __init__(self, *jobs):
        self.jobs = _Jobs(jobs)


def test_ingestor_files_resumes_by_job_code_and_moves_every_email_out_of_the_inbox(monkeypatch):
    messages = [
        {"id": "m1", "subject": "[JOB-1] Application", "hasAttachments": True, **_form_email()},
        {"id": "m2", "subject": "Hello, no code", "hasAttachments": True, **_form_email()},
        {"id": "m3", "subject": "[JOB-2] Application", "hasAttachments": True, **_form_email()},
        {"id": "m4", "subject": "[JOB-1] Application", "hasAttachments": True, **_form_email()},
    ]
    applied, moved = [], {}
    monkeypatch.setattr(graph, "inbox", lambda: messages)
    monkeypatch.setattr(graph, "resume_attachments", lambda mid: [(f"{mid}.pdf", b"%PDF")])
    monkeypatch.setattr(graph, "move", lambda mid, folder: moved.__setitem__(mid, folder))

    def apply(d, job_id, name, data, details):
        if name == "m4.pdf":
            raise HTTPException(503, "index catching up")
        applied.append((job_id, name, details["candidate_email"]))
        return {}, True
    monkeypatch.setattr(main, "_apply", apply)
    counts = main._poll_mailbox(_Db({"_id": 1, "code": "JOB-1", "rubric_approved": True},
                                     {"_id": 2, "code": "JOB-2", "rubric_approved": False}))

    assert applied == [(1, "m1.pdf", CANDIDATE)]
    assert moved == {"m1": main.PROCESSED_FOLDER, "m2": main.ATTENTION_FOLDER, "m3": main.ATTENTION_FOLDER}
    assert counts == {"emails": 4, "applications": 1, "needs_attention": 2, "retry_later": 1}  # m4 stays for next check


# What the website sends: from SmartHR's own mailbox, applicant in Reply-To and in the form body.
FORM_BODY = """New application for Backend Engineer

First name: Asha
Last name: Kumar
Email: candidate@example.com
Phone: +91 98000 00000
Current location: Kochi
LinkedIn profile: https://www.linkedin.com/in/example
Expected CTC: 15 LPA
Notice period (days): 30
https://kiaratech.ai/careers
Anything else we should know?: Available for a call after 5 pm."""


def _form_email(reply_to=None, body=FORM_BODY):
    return {"from": {"emailAddress": {"address": "noreply@example.com"}},
            "replyTo": [{"emailAddress": {"address": a}} for a in reply_to or []],
            "body": {"contentType": "text", "content": body}}


def test_applicant_comes_from_the_form_never_from_the_sending_mailbox(monkeypatch):
    monkeypatch.setattr(graph, "MAILBOX", "noreply@example.com")
    details = graph.applicant(_form_email())

    assert details["candidate_email"] == CANDIDATE and details["candidate_name"] == "Asha Kumar"
    labels = [f["label"] for f in details["form"]]
    assert labels == ["First name", "Last name", "Email", "Phone", "Current location", "LinkedIn profile",
                      "Expected CTC", "Notice period (days)", "Anything else we should know?"]
    assert details["form"][5]["value"] == "https://www.linkedin.com/in/example"  # the URL stays whole

    assert graph.applicant(_form_email(["applicant.replyto@example.com"]))["candidate_email"] == "applicant.replyto@example.com"
    # a form with other fields and no email at all: the sending mailbox is never taken as the candidate
    other = graph.applicant(_form_email(body="Full name: Ravi Menon\nPortfolio: https://example.com/ravi"))
    assert other == {"candidate_email": None, "candidate_name": "Ravi Menon",
                     "form": [{"label": "Full name", "value": "Ravi Menon"},
                              {"label": "Portfolio", "value": "https://example.com/ravi"}]}
