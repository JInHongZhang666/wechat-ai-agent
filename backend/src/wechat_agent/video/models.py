"""Stable data structures shared by the video API and providers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class VideoTaskStatus(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    EXTRACTING_CAPTIONS = "extracting_captions"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    COMPLETED = "completed"
    NO_CAPTIONS = "no_captions"
    FAILED = "failed"


class TranscriptSource(StrEnum):
    SCREEN_OCR = "screen_ocr"
    SPEECH = "speech"


class VideoSegment(BaseModel):
    id: int
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str


class VideoTask(BaseModel):
    schema_version: int = Field(default=1, alias="schemaVersion")
    id: str
    status: VideoTaskStatus
    progress: int = Field(ge=0, le=100)
    source_type: str = Field(alias="sourceType")
    source_name: str = Field(alias="sourceName")
    source_url: str | None = Field(default=None, alias="sourceUrl")
    duration: float | None = None
    language: str | None = None
    transcript_source: TranscriptSource | None = Field(
        default=None,
        alias="transcriptSource",
    )
    transcript: str = ""
    refined_transcript: str = Field(default="", alias="refinedTranscript")
    refined_model: str | None = Field(default=None, alias="refinedModel")
    segments: list[VideoSegment] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class VideoUrlRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class TranscriptionResult(BaseModel):
    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[VideoSegment] = Field(default_factory=list)


class VideoRefinementResponse(BaseModel):
    task_id: str = Field(alias="taskId")
    refined_transcript: str = Field(alias="refinedTranscript")
    model: str

    model_config = {"populate_by_name": True}


class VideoQuestionTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class VideoQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[VideoQuestionTurn] = Field(default_factory=list, max_length=12)


class VideoQuestionResponse(BaseModel):
    answer: str
    model: str
