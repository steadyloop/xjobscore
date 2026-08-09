"""Validated command payloads for the browser API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


OpportunityType = Literal[
    "spring_internship",
    "spring_coop",
    "student_researcher",
    "summer_internship",
    "fall_coop",
    "fall_internship",
    "student_role",
    "fellowship",
    "unclassified",
]

TriageState = Literal["unreviewed", "saved", "queued", "dismissed"]
ApplicationStage = Literal[
    "considering",
    "preparing",
    "submitted",
    "assessment",
    "screen",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "no_reply",
]


class FetchBatchCommand(BaseModel):
    feed_refs: list[str] = Field(min_length=1, max_length=25)

    @field_validator("feed_refs")
    @classmethod
    def unique_refs(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("feed_refs must be unique")
        return values


class SourceSelectionCommand(BaseModel):
    enabled: bool


class TriageCommand(BaseModel):
    state: TriageState


class ClassificationCorrection(BaseModel):
    opportunity_type: Literal[
        "spring_internship",
        "spring_coop",
        "student_researcher",
        "summer_internship",
        "fall_coop",
        "fall_internship",
        "student_role",
        "fellowship",
    ]
    rationale: str = Field(min_length=1, max_length=2000)


class ApplicationCreate(BaseModel):
    stage: Literal["considering", "preparing", "submitted"] = "submitted"
    confirmation: Literal["human_confirmed"] | None = None
    submitted_at: str | None = None
    resume_variant: Literal["ml-systems", "quant-systems", "backend-infra"] | None = None
    note: str = Field(default="", max_length=4000)
    source_observation_id: str | None = None

    @model_validator(mode="after")
    def submission_requires_confirmation(self) -> "ApplicationCreate":
        if self.stage == "submitted" and self.confirmation != "human_confirmed":
            raise ValueError("human submission confirmation is required")
        return self


class ApplicationEventCreate(BaseModel):
    event_type: Literal[
        "stage_changed",
        "submitted",
        "task_added",
        "task_completed",
        "assessment",
        "screen",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
        "no_reply",
        "note",
    ] = "stage_changed"
    stage: ApplicationStage | None = None
    occurred_at: datetime | None = None
    due_at: datetime | None = None
    note: str = Field(default="", max_length=8000)
    confirmed: bool = False


class ExternalApplicationCreate(BaseModel):
    company: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4000)
    location: str = Field(default="", max_length=1000)
    note: str = Field(default="", max_length=4000)


class ProfileUpdate(BaseModel):
    graduation_date: str = Field(default="2027-12", max_length=20)
    citizenship: str = Field(default="US", max_length=80)
    home_location: str = Field(default="San Diego, CA", max_length=300)
    preferred_segments: list[str] = Field(
        default_factory=lambda: ["ml_systems", "systems", "quant_dev", "backend_infra"]
    )
    relocate_for_internship: bool = True
    reduced_enrollment: bool = False
