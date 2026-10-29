"""Transcription provider boundary and the Speaches implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

from wechat_agent.video.models import TranscriptionResult, VideoSegment


class TranscriptionProvider(Protocol):
    name: str

    def health(self) -> tuple[bool, str]: ...

    def transcribe(self, audio_path: Path) -> TranscriptionResult: ...


class SpeachesProvider:
    """Call an isolated Speaches server through its OpenAI-compatible API."""

    name = "speaches"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "local-video-transcriber",
        timeout_seconds: float = 3600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def health(self) -> tuple[bool, str]:
        health_url = self.base_url.removesuffix("/v1") + "/health"
        try:
            with httpx.Client(timeout=3, trust_env=False) as client:
                response = client.get(health_url)
                response.raise_for_status()
                models = client.get(f"{self.base_url}/models")
                models.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            return False, f"Speaches 未连接：{exc}"
        installed_ids = {
            str(item.get("id"))
            for item in (models.json().get("data") or [])
            if isinstance(item, dict) and item.get("id")
        }
        if self.model not in installed_ids:
            return False, f"Speaches 已连接，但模型 {self.model} 尚未安装"
        return True, "Speaches 已连接"

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with (
            audio_path.open("rb") as audio,
            httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client,
        ):
            response = client.post(
                url,
                headers=headers,
                data={
                    "model": self.model,
                    "response_format": "verbose_json",
                    "vad_filter": "true",
                },
                files={"file": (audio_path.name, audio, "audio/wav")},
            )
        response.raise_for_status()
        payload = response.json()
        raw_segments = payload.get("segments") or []
        segments: list[VideoSegment] = []
        for index, raw in enumerate(raw_segments, start=1):
            text = str(raw.get("text") or "").strip()
            if not text:
                continue
            start = max(0.0, float(raw.get("start") or 0))
            end = max(start, float(raw.get("end") or start))
            segments.append(
                VideoSegment(id=index, start=start, end=end, text=text)
            )
        text = str(payload.get("text") or "").strip()
        if not segments and text:
            segments = [
                VideoSegment(
                    id=1,
                    start=0,
                    end=max(0.0, float(payload.get("duration") or 0)),
                    text=text,
                )
            ]
        return TranscriptionResult(
            text=text or "\n".join(item.text for item in segments),
            language=payload.get("language"),
            duration=payload.get("duration"),
            segments=segments,
        )
