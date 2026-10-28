from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.kb.evaluation import ChunkRef, EvalCase


class FileSourceRef(ChunkRef):
    kind: Literal['ch04']='ch04'


class ReviewSourceRef(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    kind: Literal['review']='review'
    review_id: int = Field(gt=0,strict=True)
    approved_content_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    fingerprint: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_marker: str = Field(min_length=1)


class VersionedCase(EvalCase):
    relevant_chunk_refs: list[Annotated[FileSourceRef | ReviewSourceRef,Field(discriminator='kind')]]


@dataclass(frozen=True)
class ActiveRelease:
    release_id: str
    dataset_version: str
    labels_hash: str
    split_hash: str
    runtime_corpus_hash: str
    profile_hash: str


class DatasetVersion(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    version: str
    parent_version: str | None
    baseline_version: str
    original_labels_hash: str
    labels_hash: str
    split_hash: str
    calibration_ids: tuple[str,...]
    test_ids: tuple[str,...]
    cases: tuple[VersionedCase,...]
    source_hashes: dict[str,str]
    changed_case_ids: tuple[str,...]=()
    differences: tuple[dict,...]=()
    confirmation: dict | None=None
    validation: dict=Field(default_factory=dict)


class ImpactDraft(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    digest: str
    base: dict
    reviews: tuple[dict,...]
    suggestions: tuple[dict,...]


class ConfirmedImpact(BaseModel):
    model_config=ConfigDict(extra='forbid',frozen=True)
    digest: str
    base: dict
    reviews_hash: str
    edits: tuple[dict,...]
    unchanged_case_ids: tuple[str,...]
    confirmed_by: str
    confirmed_at: str


class ImpactSuggestion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    affected: bool = Field(strict=True)
    reason: str = Field(min_length=1,max_length=500)
    changes: dict
