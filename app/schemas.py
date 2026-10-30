import re
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
    met_threshold: float = Field(0.5, ge=0, le=1, description="Reranker score at or above which a requirement is 'met'")
    partial_similarity: float = Field(0.6, ge=0, le=1, description="Vector similarity at or above which an unconfirmed match is 'partial'")
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
    text: str
    start: int
    end: int
    rerank_score: float
    vector_similarity: float
    keyword_score: float


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
    candidates: list[Candidate]


class Mark(BaseModel):
    requirement_id: str
    verdict: Literal["met", "partial", "not_found"]


class DocumentText(BaseModel):
    text: str


class ApplicationSummary(BaseModel):
    id: int
    job_id: int
    document_id: int
    filename: str
    status: Literal["SCORED", "NEEDS_ATTENTION"]
    band: str | None
    fit_score: float | None
    coverage: float | None
    semantic: float | None
    rubric_version: int | None
    marks: list[Mark] | None
    created_at: str


class ApplicationOut(ApplicationSummary):
    quality: str
    evidence: list[Evidence] | None
    models: dict | None
