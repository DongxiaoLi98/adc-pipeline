from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CreateRunRequest(BaseModel):
    project_id: str | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: str


class RunStatus(BaseModel):
    run_id: str
    run_status: str
    current_step_id: str | None = None
    steps_written: int


class TokenRequest(BaseModel):
    user_id: str
    role: str = "viewer"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class StepSummary(BaseModel):
    step_id: str
    step_order: int
    step_status: str
    started_at: Any = None
    finished_at: Any = None


class StepDetail(BaseModel):
    step_id: str
    step_status: str
    schema_version: str | None = None
    output_payload: dict


class ArtifactOut(BaseModel):
    artifact_id: str
    artifact_type: str | None = None
    producing_step_id: str | None = None
    upload_status: str
    size_bytes: int | None = None
    download_url: str | None = None


class StepErrorOut(BaseModel):
    step_error_id: str
    step_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    retry_count: int


class ReviewOut(BaseModel):
    human_review_id: str
    candidate_id: str | None = None
    review_status: str
    reviewer_id: str | None = None
    decision: str | None = None
    notes: str | None = None


class ReviewRequest(BaseModel):
    decision: str                 # approve | reject
    candidate_id: str | None = None
    notes: str | None = None


class UploadRequest(BaseModel):
    filename: str
    content_type: str | None = None


class UploadResponse(BaseModel):
    upload_url: str
    object_key: str
    bucket: str
