"""Video ingestion, audio extraction, transcription, persistence and export."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from threading import RLock
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import UploadFile

from wechat_agent.api.errors import AppError, invalid_request, not_found
from wechat_agent.video.models import (
    TranscriptSource,
    TranscriptionResult,
    VideoSegment,
    VideoTask,
    VideoTaskStatus,
)
from wechat_agent.video.ocr import CaptionExtractor, RapidOcrCaptionExtractor
from wechat_agent.video.providers import SpeachesProvider, TranscriptionProvider


@dataclass(frozen=True)
class VideoConfig:
    enabled: bool
    data_dir: Path
    transcriber_url: str
    transcriber_model: str
    max_file_bytes: int
    max_duration_seconds: int
    allow_url_import: bool
    cookies_file: Path | None = None
    cookies_from_browser: str | None = None

    @classmethod
    def from_env(cls) -> "VideoConfig":
        backend_dir = Path(__file__).resolve().parents[3]
        configured_cookie_file = os.getenv(
            "WECHAT_AGENT_VIDEO_COOKIES_FILE",
            str(backend_dir / "data" / "video-cookies.txt"),
        ).strip()
        configured_browser = os.getenv(
            "WECHAT_AGENT_VIDEO_COOKIES_FROM_BROWSER",
            "edge",
        ).strip().lower()
        return cls(
            enabled=os.getenv("WECHAT_AGENT_VIDEO_ENABLED", "1").lower()
            not in {"0", "false", "off", "no"},
            data_dir=Path(
                os.getenv(
                    "WECHAT_AGENT_VIDEO_DATA_DIR",
                    str(backend_dir / "data" / "video_tasks"),
                )
            ).resolve(),
            transcriber_url=os.getenv(
                "WECHAT_AGENT_VIDEO_TRANSCRIBER_URL", "http://127.0.0.1:8011/v1"
            ),
            transcriber_model=os.getenv(
                "WECHAT_AGENT_VIDEO_MODEL", "Systran/faster-whisper-small"
            ),
            max_file_bytes=max(
                1, int(os.getenv("WECHAT_AGENT_VIDEO_MAX_FILE_MB", "2048"))
            )
            * 1024
            * 1024,
            max_duration_seconds=max(
                60,
                int(os.getenv("WECHAT_AGENT_VIDEO_MAX_DURATION_MINUTES", "180"))
                * 60,
            ),
            allow_url_import=os.getenv(
                "WECHAT_AGENT_VIDEO_ALLOW_URL_IMPORT", "1"
            ).lower()
            not in {"0", "false", "off", "no"},
            cookies_file=(
                Path(configured_cookie_file).resolve()
                if configured_cookie_file
                else None
            ),
            cookies_from_browser=(
                configured_browser
                if configured_browser
                in {"edge", "chrome", "firefox", "brave", "vivaldi"}
                else None
            ),
        )


class VideoAnalysisService:
    allowed_extensions = {
        ".mp4",
        ".webm",
        ".mov",
        ".mkv",
        ".avi",
        ".m4v",
        ".mp3",
        ".m4a",
        ".wav",
        ".ogg",
        ".flac",
    }

    def __init__(
        self,
        config: VideoConfig,
        provider: TranscriptionProvider | None = None,
        caption_extractor: CaptionExtractor | None = None,
    ) -> None:
        self.config = config
        self.provider = provider or SpeachesProvider(
            base_url=config.transcriber_url,
            model=config.transcriber_model,
        )
        self.caption_extractor = caption_extractor or RapidOcrCaptionExtractor()
        self.config.data_dir.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, VideoTask] = {}
        self._lock = RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="video-analysis",
        )
        self._restore()

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def runtime_status(self) -> dict[str, object]:
        provider_ready, provider_message = self.provider.health()
        ocr_ready, ocr_message = self.caption_extractor.health()
        return {
            "schemaVersion": 1,
            "enabled": self.config.enabled,
            "provider": self.provider.name,
            "providerReady": provider_ready,
            "providerMessage": provider_message,
            "ocrProvider": self.caption_extractor.name,
            "ocrReady": ocr_ready,
            "ocrMessage": ocr_message,
            "model": self.config.transcriber_model,
            "ffmpegReady": shutil.which("ffmpeg") is not None,
            "ffprobeReady": shutil.which("ffprobe") is not None,
            "urlImportEnabled": self.config.allow_url_import,
            "ytDlpReady": self._yt_dlp_ready(),
            "cookieFileReady": bool(
                self.config.cookies_file and self.config.cookies_file.is_file()
            ),
            "cookieBrowser": self.config.cookies_from_browser,
            "maxFileMb": self.config.max_file_bytes // 1024 // 1024,
            "maxDurationMinutes": self.config.max_duration_seconds // 60,
        }

    async def create_upload(self, upload: UploadFile) -> VideoTask:
        self._ensure_enabled()
        source_name = Path(upload.filename or "video.mp4").name
        extension = Path(source_name).suffix.lower()
        if extension not in self.allowed_extensions:
            raise invalid_request("暂不支持该视频或音频格式。")
        task = self._new_task("upload", source_name)
        task_dir = self._task_dir(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        input_path = task_dir / f"source{extension}"
        written = 0
        try:
            with input_path.open("wb") as target:
                while chunk := await upload.read(1024 * 1024):
                    written += len(chunk)
                    if written > self.config.max_file_bytes:
                        raise AppError(
                            413,
                            "VIDEO_TOO_LARGE",
                            "视频文件超过允许的大小。",
                        )
                    target.write(chunk)
        except Exception:
            input_path.unlink(missing_ok=True)
            with self._lock:
                self._tasks.pop(task.id, None)
            raise
        finally:
            await upload.close()
        self._persist(task)
        self._executor.submit(self._process_input, task.id, input_path)
        return task.model_copy(deep=True)

    def create_url(self, raw_url: str) -> VideoTask:
        self._ensure_enabled()
        if not self.config.allow_url_import:
            raise invalid_request("当前部署未启用视频链接解析。")
        url = raw_url.strip()
        self._validate_public_url(url)
        task = self._new_task("url", url, source_url=url)
        self._persist(task)
        self._executor.submit(self._process_url, task.id, url)
        return task.model_copy(deep=True)

    def get_task(self, task_id: str) -> VideoTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise not_found("视频解析任务不存在。")
            return task.model_copy(deep=True)

    def latest_task(self) -> VideoTask:
        """Return the most recently updated local task for session recovery."""
        with self._lock:
            if not self._tasks:
                raise not_found("暂无可恢复的视频解析任务。")
            task = max(self._tasks.values(), key=lambda item: item.updated_at)
            return task.model_copy(deep=True)

    def save_refinement(
        self,
        task_id: str,
        refined_transcript: str,
        model: str,
    ) -> VideoTask:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise not_found("视频解析任务不存在。")
            task.refined_transcript = refined_transcript.strip()
            task.refined_model = model
            task.updated_at = datetime.now(UTC)
            self._persist(task)
            return task.model_copy(deep=True)

    def source_media(self, task_id: str) -> tuple[Path, str]:
        task = self.get_task(task_id)
        if task.source_type != "upload":
            raise not_found("当前任务没有可恢复的本地视频。")
        task_dir = self._task_dir(task_id).resolve()
        source_path = next(
            (
                candidate.resolve()
                for candidate in task_dir.glob("source.*")
                if candidate.is_file()
            ),
            None,
        )
        if source_path is None or source_path.parent != task_dir:
            raise not_found("视频原文件不存在，请重新上传。")
        media_type = mimetypes.guess_type(source_path.name)[0]
        return source_path, media_type or "application/octet-stream"

    def export(self, task_id: str, export_format: str) -> tuple[str, str, str]:
        task = self.get_task(task_id)
        if task.status is not VideoTaskStatus.COMPLETED:
            raise AppError(409, "VIDEO_NOT_READY", "视频字幕尚未解析完成。")
        safe_stem = Path(task.source_name).stem[:80] or "transcript"
        if export_format == "text":
            return task.transcript, "text/plain; charset=utf-8", f"{safe_stem}.txt"
        if export_format == "srt":
            body = "\n\n".join(
                f"{index}\n{_srt_time(item.start)} --> {_srt_time(item.end)}\n{item.text}"
                for index, item in enumerate(task.segments, start=1)
            )
            return body + "\n", "application/x-subrip; charset=utf-8", f"{safe_stem}.srt"
        if export_format == "vtt":
            cues = "\n\n".join(
                f"{_vtt_time(item.start)} --> {_vtt_time(item.end)}\n{item.text}"
                for item in task.segments
            )
            return f"WEBVTT\n\n{cues}\n", "text/vtt; charset=utf-8", f"{safe_stem}.vtt"
        raise invalid_request("导出格式仅支持 text、srt 或 vtt。")

    def _new_task(
        self,
        source_type: str,
        source_name: str,
        *,
        source_url: str | None = None,
    ) -> VideoTask:
        now = datetime.now(UTC)
        task = VideoTask(
            id=f"video_{uuid4().hex}",
            status=VideoTaskStatus.QUEUED,
            progress=0,
            sourceType=source_type,
            sourceName=source_name,
            sourceUrl=source_url,
            createdAt=now,
            updatedAt=now,
        )
        with self._lock:
            self._tasks[task.id] = task
        return task

    def _process_url(self, task_id: str, url: str) -> None:
        try:
            self._transition(task_id, VideoTaskStatus.DOWNLOADING, 10)
            task_dir = self._task_dir(task_id)
            task_dir.mkdir(parents=True, exist_ok=True)
            output_template = str(task_dir / "remote.%(ext)s")
            failures: list[RuntimeError] = []
            completed: subprocess.CompletedProcess[str] | None = None
            for cookie_args in self._url_cookie_options(url):
                command = [
                    sys.executable,
                    "-m",
                    "yt_dlp",
                    "--no-playlist",
                    "--no-progress",
                    "--restrict-filenames",
                    "--max-filesize",
                    str(self.config.max_file_bytes),
                    *cookie_args,
                    "-x",
                    "--audio-format",
                    "wav",
                    "--audio-quality",
                    "0",
                    "--print",
                    "after_move:filepath",
                    "-o",
                    output_template,
                    url,
                ]
                try:
                    completed = self._run(command, timeout=1800)
                    break
                except RuntimeError as exc:
                    failures.append(exc)
            if completed is None:
                raise _url_download_error(url, failures)
            candidates = [
                Path(line.strip())
                for line in completed.stdout.splitlines()
                if line.strip()
            ]
            audio_path = next(
                (path for path in reversed(candidates) if path.is_file()),
                task_dir / "remote.wav",
            )
            if not audio_path.is_file():
                raise RuntimeError("链接中的音频未能下载。")
            if audio_path.stat().st_size > self.config.max_file_bytes:
                raise RuntimeError("链接中的媒体超过当前允许的大小。")
            duration = self._probe_duration(audio_path)
            if duration > self.config.max_duration_seconds:
                raise RuntimeError("链接中的媒体时长超过当前允许的上限。")
            self._set_duration(task_id, duration)
            self._process_audio(task_id, audio_path)
        except Exception as exc:
            self._fail(task_id, exc)

    def _url_cookie_options(self, url: str) -> list[list[str]]:
        options: list[list[str]] = [[]]
        if not _is_douyin_url(url):
            return options
        cookie_file = self.config.cookies_file
        if cookie_file and cookie_file.is_file():
            options.append(["--cookies", str(cookie_file)])
        if self.config.cookies_from_browser:
            options.append(
                ["--cookies-from-browser", self.config.cookies_from_browser]
            )
        return options

    def _process_input(self, task_id: str, input_path: Path) -> None:
        try:
            duration = self._probe_duration(input_path)
            if duration > self.config.max_duration_seconds:
                raise RuntimeError("视频时长超过当前允许的上限。")
            self._set_duration(task_id, duration)
            if input_path.suffix.lower() in self._video_extensions():
                self._transition(
                    task_id,
                    VideoTaskStatus.EXTRACTING_CAPTIONS,
                    25,
                )
                result = self.caption_extractor.extract(
                    input_path,
                    duration,
                    lambda ratio: self._update_ocr_progress(task_id, ratio),
                )
                if result.segments:
                    self._complete(
                        task_id,
                        result,
                        TranscriptSource.SCREEN_OCR,
                    )
                else:
                    self._complete_without_captions(task_id)
                return
            self._transition(task_id, VideoTaskStatus.EXTRACTING_AUDIO, 20)
            audio_path = self._task_dir(task_id) / "audio.wav"
            self._run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio_path),
                ],
                timeout=1800,
            )
            self._process_audio(task_id, audio_path)
        except Exception as exc:
            self._fail(task_id, exc)

    def _process_audio(self, task_id: str, audio_path: Path) -> None:
        self._transition(task_id, VideoTaskStatus.TRANSCRIBING, 45)
        result = self.provider.transcribe(audio_path)
        self._complete(task_id, result, TranscriptSource.SPEECH)

    def _complete(
        self,
        task_id: str,
        result: TranscriptionResult,
        source: TranscriptSource,
    ) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = VideoTaskStatus.COMPLETED
            task.progress = 100
            task.language = result.language
            task.transcript_source = source
            task.transcript = result.text
            task.segments = result.segments
            if result.duration is not None:
                task.duration = result.duration
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    def _complete_without_captions(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = VideoTaskStatus.NO_CAPTIONS
            task.progress = 100
            task.transcript_source = None
            task.transcript = ""
            task.segments = []
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    @classmethod
    def _video_extensions(cls) -> set[str]:
        return cls.allowed_extensions - {".mp3", ".m4a", ".wav", ".ogg", ".flac"}

    def _probe_duration(self, path: Path) -> float:
        completed = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            timeout=60,
        )
        try:
            return max(0.0, float(completed.stdout.strip()))
        except ValueError as exc:
            raise RuntimeError("无法读取视频时长。") from exc

    @staticmethod
    def _run(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"缺少必要工具：{command[0]}") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "外部工具执行失败").strip()
            raise RuntimeError(detail[-800:]) from exc

    def _transition(
        self, task_id: str, status_value: VideoTaskStatus, progress: int
    ) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.status = status_value
            task.progress = progress
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    def _set_duration(self, task_id: str, duration: float) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.duration = duration
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    def _update_ocr_progress(self, task_id: str, ratio: float) -> None:
        progress = min(90, 25 + round(max(0.0, min(1.0, ratio)) * 65))
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None or progress <= task.progress:
                return
            if progress < 90 and progress - task.progress < 3:
                return
            task.progress = progress
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    def _fail(self, task_id: str, error: Exception) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = VideoTaskStatus.FAILED
            task.error = _friendly_error(error)
            task.updated_at = datetime.now(UTC)
            self._persist(task)

    def _restore(self) -> None:
        for state_file in self.config.data_dir.glob("*/task.json"):
            try:
                task = VideoTask.model_validate_json(state_file.read_text("utf-8"))
            except (OSError, ValueError):
                continue
            if task.status not in {
                VideoTaskStatus.COMPLETED,
                VideoTaskStatus.NO_CAPTIONS,
                VideoTaskStatus.FAILED,
            }:
                task.status = VideoTaskStatus.FAILED
                task.error = "服务重启导致任务中断，请重新提交。"
                task.updated_at = datetime.now(UTC)
                self._persist(task)
            self._tasks[task.id] = task

    def _persist(self, task: VideoTask) -> None:
        task_dir = self._task_dir(task.id)
        task_dir.mkdir(parents=True, exist_ok=True)
        temporary = task_dir / "task.json.tmp"
        temporary.write_text(
            task.model_dump_json(by_alias=True, indent=2),
            encoding="utf-8",
        )
        temporary.replace(task_dir / "task.json")

    def _task_dir(self, task_id: str) -> Path:
        return self.config.data_dir / task_id

    def _ensure_enabled(self) -> None:
        if not self.config.enabled:
            raise AppError(503, "VIDEO_DISABLED", "视频解析模块尚未启用。")

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise invalid_request("请输入有效的 http:// 或 https:// 视频链接。")
        try:
            lookup_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(parsed.hostname, lookup_port)
            }
        except socket.gaierror as exc:
            raise invalid_request("视频链接域名无法解析。") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise invalid_request("视频链接不能指向本机或内网地址。")

    @staticmethod
    def _yt_dlp_ready() -> bool:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            return False
        return True


def _friendly_error(error: Exception) -> str:
    if isinstance(error, AppError):
        return error.message
    if isinstance(error, Exception):
        text = str(error).strip()
        if text:
            return text[:800]
    return "视频解析失败，请检查文件或链接后重试。"


def _is_douyin_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname == "douyin.com" or hostname.endswith(".douyin.com")


def _url_download_error(url: str, failures: list[RuntimeError]) -> RuntimeError:
    details = "\n".join(str(item) for item in failures).lower()
    if _is_douyin_url(url) and any(
        marker in details
        for marker in (
            "fresh cookies",
            "http error 403",
            "forbidden",
            "cookie database",
            "failed to decrypt with dpapi",
        )
    ):
        return RuntimeError(
            "抖音限制了公开链接解析。请先用 Edge 登录抖音并打开一次该视频，"
            "然后完全退出 Edge 后重新解析；如果仍失败，请下载视频后切换到“本地视频”上传。"
        )
    if failures:
        return failures[-1]
    return RuntimeError("链接中的视频未能下载。")


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _vtt_time(seconds: float) -> str:
    return _srt_time(seconds).replace(",", ".")
