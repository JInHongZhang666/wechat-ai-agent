"""Extract burned-in captions from sampled video frames with RapidOCR."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
import importlib.util
import math
from pathlib import Path
import re
from threading import Lock
from typing import Callable, Protocol

from wechat_agent.video.models import TranscriptionResult, VideoSegment


class CaptionExtractor(Protocol):
    name: str

    def health(self) -> tuple[bool, str]: ...

    def extract(
        self,
        video_path: Path,
        duration: float,
        progress_callback: Callable[[float], None] | None = None,
    ) -> TranscriptionResult: ...


@dataclass
class _ObservedLine:
    text: str
    y: float
    x: float = 0.5


@dataclass
class _CaptionTrack:
    start: float
    last_seen: float
    y: float
    variants: Counter[str] = field(default_factory=Counter)
    hits: int = 0

    @property
    def text(self) -> str:
        return self.variants.most_common(1)[0][0]


class RapidOcrCaptionExtractor:
    """Read visible captions instead of inventing text from background audio."""

    name = "rapidocr"

    def __init__(
        self,
        *,
        sample_interval_seconds: float = 0.5,
        max_samples: int = 360,
        max_frame_side: int = 1280,
        min_score: float = 0.72,
    ) -> None:
        self.sample_interval_seconds = sample_interval_seconds
        self.max_samples = max_samples
        self.max_frame_side = max_frame_side
        self.min_score = min_score
        self._engine = None
        self._engine_lock = Lock()

    def health(self) -> tuple[bool, str]:
        if importlib.util.find_spec("rapidocr") is None:
            return False, "RapidOCR 未安装"
        if importlib.util.find_spec("cv2") is None:
            return False, "OpenCV 未安装"
        return True, "画面字幕识别已就绪"

    def extract(
        self,
        video_path: Path,
        duration: float,
        progress_callback: Callable[[float], None] | None = None,
    ) -> TranscriptionResult:
        import cv2

        engine = self._get_engine()
        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError("无法读取视频画面。")
        interval = self._sample_interval(duration)
        expected_samples = max(1, int(duration / interval) + 1)
        observations: list[tuple[float, list[_ObservedLine]]] = []
        timestamp = min(0.4, max(0.0, duration / 4))
        try:
            while timestamp < max(duration, 0.01):
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)
                ok, frame = capture.read()
                if not ok:
                    break
                frame = _resize_for_ocr(frame, self.max_frame_side, cv2)
                ocr_frame = _crop_primary_caption_region(frame)
                with self._engine_lock:
                    output = engine(ocr_frame)
                lines = _lines_from_output(
                    output,
                    ocr_frame.shape,
                    self.min_score,
                )
                observations.append((timestamp, lines))
                if progress_callback is not None:
                    progress_callback(
                        min(1.0, len(observations) / expected_samples)
                    )
                timestamp += interval
        finally:
            capture.release()

        segments = _build_segments(observations, duration, interval)
        return TranscriptionResult(
            text="\n".join(segment.text for segment in segments),
            language="zh" if segments else None,
            duration=duration,
            segments=segments,
        )

    def _sample_interval(self, duration: float) -> float:
        if duration <= 10:
            adaptive = self.sample_interval_seconds
        elif duration <= 20:
            adaptive = 0.8
        elif duration <= 120:
            adaptive = 1.0
        elif duration <= 300:
            adaptive = 1.5
        else:
            adaptive = 2.0
        return max(adaptive, duration / max(1, self.max_samples))

    def _get_engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            with self._engine_lock:
                if self._engine is None:
                    self._engine = RapidOCR()
        return self._engine


def _lines_from_output(output, frame_shape, min_score: float) -> list[_ObservedLine]:
    height, width = frame_shape[:2]
    boxes = output.boxes if output is not None else None
    texts = output.txts if output is not None else None
    scores = output.scores if output is not None else None
    if boxes is None or texts is None or scores is None:
        return []

    items: list[dict[str, float | str]] = []
    for box, raw_text, raw_score in zip(boxes, texts, scores, strict=False):
        text = _normalize_text(str(raw_text))
        score = float(raw_score)
        if score < min_score or len(text) < 2:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        items.append(
            {
                "text": text,
                "x": min(xs),
                "right": max(xs),
                "y": sum(ys) / len(ys),
                "height": max(ys) - min(ys),
            }
        )
    items.sort(key=lambda item: (float(item["y"]), float(item["x"])))

    rows: list[list[dict[str, float | str]]] = []
    for item in items:
        matching_row = next(
            (
                row
                for row in rows
                if abs(float(item["y"]) - _row_y(row))
                <= max(float(item["height"]), _row_height(row), 8.0) * 0.65
            ),
            None,
        )
        if matching_row is None:
            rows.append([item])
        else:
            matching_row.append(item)

    lines: list[_ObservedLine] = []
    seen: set[str] = set()
    for row in rows:
        row.sort(key=lambda item: float(item["x"]))
        for group in _split_horizontal_groups(row, width):
            text = _normalize_text("".join(str(item["text"]) for item in group))
            left = min(float(item["x"]) for item in group)
            right = max(float(item["right"]) for item in group)
            center_x = ((left + right) / 2) / max(1, width)
            if len(text) < 2 or text in seen:
                continue
            if not 0.18 <= center_x <= 0.82:
                continue
            seen.add(text)
            lines.append(
                _ObservedLine(
                    text=text,
                    y=_row_y(group) / max(1, height),
                    x=center_x,
                )
            )
    return lines


def _build_segments(
    observations: list[tuple[float, list[_ObservedLine]]],
    duration: float,
    interval: float,
) -> list[VideoSegment]:
    tracks: list[_CaptionTrack] = []
    active: list[_CaptionTrack] = []
    for timestamp, lines in observations:
        next_active: list[_CaptionTrack] = []
        claimed: set[int] = set()
        for line in lines:
            match = next(
                (
                    track
                    for index, track in enumerate(active)
                    if index not in claimed
                    and _similar(track.text, line.text)
                ),
                None,
            )
            if match is None:
                match = _CaptionTrack(
                    start=timestamp,
                    last_seen=timestamp,
                    y=line.y,
                )
                tracks.append(match)
            else:
                claimed.add(active.index(match))
                match.last_seen = timestamp
                match.y = (match.y * match.hits + line.y) / (match.hits + 1)
            match.variants[line.text] += 1
            match.hits += 1
            next_active.append(match)
        active = next_active

    minimum_hits = 1
    persistent_hits = max(4, math.ceil(len(observations) * 0.6))
    stable = [
        track
        for track in tracks
        if track.hits >= minimum_hits
        and not (
            len(observations) >= 8
            and track.hits >= persistent_hits
        )
    ]
    stable.sort(key=lambda track: (track.start, track.y))
    return [
        VideoSegment(
            id=index,
            start=round(track.start, 3),
            end=round(min(duration, track.last_seen + interval), 3),
            text=track.text,
        )
        for index, track in enumerate(stable, start=1)
    ]


def _similar(left: str, right: str) -> bool:
    return left == right or SequenceMatcher(None, left, right).ratio() >= 0.86


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", "", text.strip())
    return text.strip("|｜_—-·•")


def _resize_for_ocr(frame, max_frame_side: int, cv2):
    height, width = frame.shape[:2]
    longest_side = max(height, width)
    if longest_side <= max_frame_side:
        return frame
    scale = max_frame_side / longest_side
    return cv2.resize(
        frame,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_AREA,
    )


def _crop_primary_caption_region(frame):
    """Keep the central lower frame where spoken subtitles normally appear."""
    height, width = frame.shape[:2]
    top = max(0, int(height * 0.48))
    bottom = min(height, int(height * 0.96))
    left = max(0, int(width * 0.08))
    right = min(width, int(width * 0.92))
    crop = frame[top:bottom, left:right]
    if not crop.size:
        return frame
    return crop


def _row_y(row: list[dict[str, float | str]]) -> float:
    return sum(float(item["y"]) for item in row) / len(row)


def _row_height(row: list[dict[str, float | str]]) -> float:
    return max(float(item["height"]) for item in row)


def _split_horizontal_groups(
    row: list[dict[str, float | str]],
    frame_width: int,
) -> list[list[dict[str, float | str]]]:
    groups: list[list[dict[str, float | str]]] = []
    for item in row:
        if not groups:
            groups.append([item])
            continue
        previous = groups[-1][-1]
        gap = float(item["x"]) - float(previous["right"])
        split_threshold = max(
            frame_width * 0.055,
            float(item["height"]) * 2.2,
            float(previous["height"]) * 2.2,
        )
        if gap > split_threshold:
            groups.append([item])
        else:
            groups[-1].append(item)
    return groups
