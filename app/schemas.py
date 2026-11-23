import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Design section 13: the checklist validator blocks protected characteristics.
PROTECTED_TERMS = re.compile(
    r"\b(age|aged|gender|male|female|religion|religious|caste|marital|married|"
    r"pregnan\w*|photo|photograph|nationality|race|ethnicity|disability)\b",
    re.IGNORECASE,
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Requirement(Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,20}$", examples=["R1"])
    text: str = Field(min_length=3, max_length=300, examples=["Builds REST APIs in ASP.NET Core"])
    type: Literal["must", "nice"]
    weight: int = Field(ge=1, le=10)

    @field_validator("text")
    @classmethod
    def no_protected_characteristics(cls, value: str) -> str:
        if found := PROTECTED_TERMS.search(value):
            raise ValueError(f"Requirement refers to a protected characteristic: '{found.group()}'")
        return value


class JobSettings(Strict):
    entail_threshold: float = Field(0.9, ge=0, le=1, description="Entailment probability at or above which a resume line proves a requirement part")
    met_threshold: float = Field(0.5, ge=0, le=1, description="Reranker score at or above which a requirement is 'met'")
    met_similarity: float = Field(0.72, ge=0, le=1, description="Meaning similarity at or above which a requirement is 'met' even when the reranker is unsure")
    partial_similarity: float = Field(0.66, ge=0, le=1, description="Vector similarity at or above which an unconfirmed match is 'partial'")
    evidence_weight: float = Field(0.8, ge=0, le=1, description="Share of the fit score from coverage; the rest is semantic")
    strong_fit: float = Field(75, ge=0, le=100)
    floor: float = Field(40, ge=0, le=100)

    @model_validator(mode="after")
    def ordered(self):
        if self.floor > self.strong_fit:
            raise ValueError("floor must not exceed strong_fit")
        return self


class JobCreate(Strict):
    code: str = Field(pattern=r"^[A-Za-z0-9-]{2,30}$", examples=["JOB-1023"])
    title: str = Field(min_length=2, max_length=200, examples=["Backend Engineer"])
    description: str = Field(min_length=1, max_length=20000)
    settings: JobSettings = JobSettings()


class RubricUpdate(Strict):
    requirements: list[Requirement] = Field(min_length=1, max_length=30)

    @field_validator("requirements")
    @classmethod
    def unique_ids(cls, value: list[Requirement]) -> list[Requirement]:
        ids = [r.id for r in value]
        if len(ids) != len(set(ids)):
            raise ValueError("Requirement ids must be unique")
        return value


class JobDraft(BaseModel):
    title: str
    description: str
    requirements: list[Requirement]


class JobOut(BaseModel):
    id: int
    code: str
    title: str
    description: str
    rubric: list[Requirement]
    rubric_version: int
    rubric_approved: bool
    settings: JobSettings
    application_count: int
    attention_count: int
    created_at: str


class Candidate(BaseModel):
    part: str | None = None  # absent on evidence scored before requirements were split
    text: str
    start: int
    end: int
    entail_score: float | None = None
    rerank_score: float
    vector_similarity: float
    keyword_score: float


class Part(BaseModel):
    """One part of a compound requirement ("Python" in "Python and libraries such as NumPy") and the line proving it."""
    text: str
    verdict: Literal["met", "partial", "not_found"]
    score: float
    quote: str | None
    start: int | None
    end: int | None


class Evidence(BaseModel):
    requirement_id: str
    requirement: str
    type: Literal["must", "nice"]
    weight: int
    verdict: Literal["met", "partial", "not_found"]
    match_score: float
    similarity: float
    quote: str | None
    start: int | None
    end: int | None
    parts: list[Part] = []
    reason: str | None = None  # the LLM judge's one-line explanation
    candidates: list[Candidate]


class Mark(BaseModel):
    requirement_id: str
    verdict: Literal["met", "partial", "not_found"]


class DocumentText(BaseModel):
    text: str


class FormField(BaseModel):
    label: str
    value: str


class ApplicationSummary(BaseModel):
    id: int
    job_id: int
    document_id: int
    filename: str
    candidate_name: str | None = None  # from the website's application form; the filename is shown otherwise
    status: Literal["SCORED", "NEEDS_ATTENTION"]
    band: str | None
    fit_score: float | None
    coverage: float | None
    semantic: float | None
    rubric_version: int | None
    marks: list[Mark] | None
    created_at: str


class Sent(BaseModel):
    to: str
    sent_at: str
    test: bool = False  # sent in test mode: it went to delivered_to, not the candidate
    delivered_to: str | None = None


class OutreachSettings(BaseModel):
    mailbox: str | None  # SmartHR's sending mailbox; null when Microsoft 365 is not set up
    redirect_to: str | None


class MailboxStatus(BaseModel):
    mailbox: str | None
    connected: bool
    detail: str | None
    check_every_minutes: float | None


class MailboxCheck(BaseModel):
    emails: int
    applications: int
    needs_attention: int
    retry_later: int


class Interview(Sent):
    interviewer: str | None = None
    start: datetime
    end: datetime
    event_id: str
    join_url: str | None


class Outreach(BaseModel):
    shortlisted: Sent | None = None
    interview: Interview | None = None


class ApplicationOut(ApplicationSummary):
    quality: str
    evidence: list[Evidence] | None
    models: dict | None
    candidate_email: str | None = None  # from the application form, else read from the resume; HR can correct it
    form: list[FormField] = []  # the website form's fields, whatever that job's form asked
    outreach: Outreach = Outreach()


EMAIL_PATTERN = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


class EmailIn(Strict):
    to: str = Field(pattern=EMAIL_PATTERN, max_length=254, examples=["candidate@example.com"])
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)


class InterviewIn(EmailIn):
    interviewer: str = Field(pattern=EMAIL_PATTERN, max_length=254, description="The HR interviewer, also invited")
    start: datetime = Field(description="With a time zone offset, e.g. 2026-11-20T10:00:00+05:30")
    end: datetime

    @model_validator(mode="after")
    def sensible_times(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("start and end need a time zone offset")
        if self.start <= datetime.now(timezone.utc):
            raise ValueError("start must be in the future")
        if not timedelta(minutes=5) <= self.end - self.start <= timedelta(hours=4):
            raise ValueError("the interview must last between 5 minutes and 4 hours")
        return self


class Slot(BaseModel):
    start: datetime
    end: datetime
