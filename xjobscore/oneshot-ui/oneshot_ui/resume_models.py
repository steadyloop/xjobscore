"""Validated command payloads for the resume workspace API."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# Variants are defined per account in `resume_workspace.variants`, so the wire
# type is only a bounded string: the store checks the value against that
# account's declared keys, which is the only place the list is readable. A
# Literal here would be one user's career shape compiled into the API.
# "all" stays reserved on an audience -- it means "show on every variant".
VariantKey = Annotated[str, StringConstraints(max_length=40)]
Audience = VariantKey
Variant = VariantKey
ALL_AUDIENCES = "all"
EntryKind = Literal["education", "experience", "research", "project", "leadership"]
SkillCategory = Literal["language", "framework", "tool", "coursework", "math"]
# The typeset design. "main" is the original serif layout; "barlow" is the
# sans layout built on the vendored Barlow faces.
Template = Literal["main", "barlow"]


class ProfileLink(BaseModel):
    label: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=2000)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    links: list[ProfileLink] | None = None


class VariantDefinition(BaseModel):
    """One entry in an account's variant list.

    ``key`` is what a bullet's ``audience`` and a resume's ``variant`` store;
    ``label`` is what the studio prints. The store lowercases and dedupes keys
    and drops the reserved ``all``.
    """

    key: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=80)


class VariantsUpdate(BaseModel):
    variants: list[VariantDefinition] = Field(default_factory=list)


class EntryCreate(BaseModel):
    kind: EntryKind
    org: str = Field(min_length=1, max_length=300)
    role: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=200)
    start_date: str = Field(default="", max_length=40)
    end_date: str = Field(default="", max_length=40)
    url: str = Field(default="", max_length=2000)
    detail: str = Field(default="", max_length=500)


class EntryUpdate(BaseModel):
    kind: EntryKind | None = None
    org: str | None = Field(default=None, min_length=1, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)
    url: str | None = Field(default=None, max_length=2000)
    detail: str | None = Field(default=None, max_length=500)
    position: int | None = Field(default=None, ge=0)


class BulletCreate(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    audience: Audience = "all"
    tags: list[str] = Field(default_factory=list)
    defense_notes: str = Field(default="", max_length=4000)


class BulletUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=600)
    audience: Audience | None = None
    tags: list[str] | None = None
    defense_notes: str | None = Field(default=None, max_length=4000)
    position: int | None = Field(default=None, ge=0)


class SkillCreate(BaseModel):
    category: SkillCategory
    name: str = Field(min_length=1, max_length=120)
    audience: Audience = "all"


class SkillUpdate(BaseModel):
    category: SkillCategory | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    audience: Audience | None = None


class BulletPick(BaseModel):
    bullet_id: int
    text_override: str | None = Field(default=None, max_length=600)


class EntryPick(BaseModel):
    entry_id: int
    bullets: list[BulletPick] = Field(default_factory=list)
    # The entry's detail line (GPA on education, stack on a project) is on by
    # default; a resume opts out per entry, so a pick with no bullets can
    # still carry a choice.
    show_detail: bool = True
    # Per-resume wording of the entry's header, as `text_override` is for a
    # bullet: None keeps the bank's value, "" blanks it on this resume only.
    org: str | None = Field(default=None, max_length=300)
    role: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=200)
    start_date: str | None = Field(default=None, max_length=40)
    end_date: str | None = Field(default=None, max_length=40)
    detail: str | None = Field(default=None, max_length=500)


class Selection(BaseModel):
    entries: list[EntryPick] = Field(default_factory=list)
    skills: list[int] = Field(default_factory=list)


class ResumeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    target_job_id: int | None = None
    variant: Variant = ""
    template: Template = "main"
    availability_line: str = Field(default="", max_length=300)
    education_note: str = Field(default="", max_length=300)
    selection: Selection = Field(default_factory=Selection)


class ResumeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    target_job_id: int | None = None
    variant: Variant | None = None
    template: Template | None = None
    availability_line: str | None = Field(default=None, max_length=300)
    education_note: str | None = Field(default=None, max_length=300)
    selection: Selection | None = None
