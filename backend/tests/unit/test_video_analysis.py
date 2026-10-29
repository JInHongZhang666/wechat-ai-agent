from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from wechat_agent.api.errors import AppError
from wechat_agent.video.models import VideoSegment, VideoTask, VideoTaskStatus
from wechat_agent.video.ocr import (
    RapidOcrCaptionExtractor,
    _ObservedLine,
    _build_segments,
    _lines_from_output,
)
from wechat_agent.video.service import (
    VideoAnalysisService,
    VideoConfig,
    _is_douyin_url,
    _url_download_error,
)


class FakeProvider:
    name = "fake"

    def health(self):
        return True, "ready"

    def transcribe(self, _audio_path):
        raise AssertionError("not used in export tests")


@pytest.fixture
def service(tmp_path):
    instance = VideoAnalysisService(
        VideoConfig(
            enabled=True,
            data_dir=tmp_path,
            transcriber_url="http://127.0.0.1:8011/v1",
            transcriber_model="test-model",
            max_file_bytes=10_000,
            max_duration_seconds=600,
            allow_url_import=True,
        ),
        provider=FakeProvider(),
    )
    yield instance
    instance.close()


def test_exports_real_segments_as_text_srt_and_vtt(service):
    now = datetime.now(UTC)
    task = VideoTask(
        id="video_test",
        status=VideoTaskStatus.COMPLETED,
        progress=100,
        sourceType="upload",
        sourceName="示例.mp4",
        transcript="第一句话。第二句话。",
        segments=[
            VideoSegment(id=1, start=0.5, end=2.25, text="第一句话。"),
            VideoSegment(id=2, start=2.25, end=65.005, text="第二句话。"),
        ],
        createdAt=now,
        updatedAt=now,
    )
    service._tasks[task.id] = task

    text, text_type, _ = service.export(task.id, "text")
    srt, srt_type, _ = service.export(task.id, "srt")
    vtt, vtt_type, _ = service.export(task.id, "vtt")

    assert text == "第一句话。第二句话。"
    assert text_type.startswith("text/plain")
    assert "00:00:00,500 --> 00:00:02,250" in srt
    assert "00:00:02,250 --> 00:01:05,005" in srt
    assert srt_type.startswith("application/x-subrip")
    assert vtt.startswith("WEBVTT")
    assert "00:00:00.500 --> 00:00:02.250" in vtt
    assert vtt_type.startswith("text/vtt")


def test_private_or_loopback_video_urls_are_rejected(service):
    with pytest.raises(AppError) as loopback:
        service.create_url("http://127.0.0.1/private.mp4")
    assert loopback.value.code == "INVALID_REQUEST"


def test_douyin_cookie_failure_has_actionable_chinese_message():
    error = _url_download_error(
        "https://www.douyin.com/video/123456",
        [RuntimeError("HTTP Error 403: Forbidden; Fresh cookies are needed")],
    )

    assert _is_douyin_url("https://v.douyin.com/example") is True
    assert "完全退出 Edge" in str(error)
    assert "本地视频" in str(error)


def test_runtime_status_exposes_independent_dependencies(service):
    status = service.runtime_status()

    assert status["enabled"] is True
    assert status["provider"] == "fake"
    assert status["providerReady"] is True
    assert status["model"] == "test-model"
    assert status["urlImportEnabled"] is True
    assert status["ocrProvider"] == "rapidocr"
    assert status["ocrReady"] is True


def test_refinement_is_persisted_with_task(service):
    now = datetime.now(UTC)
    task = VideoTask(
        id="video_refined",
        status=VideoTaskStatus.COMPLETED,
        progress=100,
        sourceType="upload",
        sourceName="示例.mp4",
        transcript="原始内容。",
        createdAt=now,
        updatedAt=now,
    )
    service._tasks[task.id] = task

    saved = service.save_refinement(task.id, "  整理后的内容。  ", "deepseek-chat")

    assert saved.refined_transcript == "整理后的内容。"
    assert saved.refined_model == "deepseek-chat"
    reloaded = service.get_task(task.id)
    assert reloaded.refined_transcript == "整理后的内容。"
    assert reloaded.refined_model == "deepseek-chat"


def test_uploaded_source_media_can_be_restored(service):
    now = datetime.now(UTC)
    task = VideoTask(
        id="video_media",
        status=VideoTaskStatus.COMPLETED,
        progress=100,
        sourceType="upload",
        sourceName="示例.mp4",
        createdAt=now,
        updatedAt=now,
    )
    service._tasks[task.id] = task
    task_dir = service.config.data_dir / task.id
    task_dir.mkdir(parents=True)
    expected = task_dir / "source.mp4"
    expected.write_bytes(b"video")

    path, media_type = service.source_media(task.id)

    assert path == expected.resolve()
    assert media_type == "video/mp4"


def test_latest_task_returns_most_recently_updated_task(service):
    older = service._new_task("url", "older", source_url="https://example.com/older")
    newer = service._new_task("url", "newer", source_url="https://example.com/newer")

    assert service.latest_task().id == newer.id
    assert service.latest_task().id != older.id


def test_visible_caption_tracks_are_deduplicated_and_timed():
    observations = [
        (
            0.0,
            [
                _ObservedLine("我的鞋底，正和山说话", 0.08),
                _ObservedLine("鞋底与青苔的触碰", 0.82),
            ],
        ),
        (
            0.5,
            [
                _ObservedLine("我的鞋底，正和山说话", 0.08),
                _ObservedLine("鞋底与青苔的触碰", 0.82),
            ],
        ),
        (
            1.0,
            [
                _ObservedLine("我的鞋底，正和山说话", 0.08),
                _ObservedLine("喘息节奏变慢", 0.82),
            ],
        ),
        (
            1.5,
            [
                _ObservedLine("我的鞋底，正和山说话", 0.08),
                _ObservedLine("喘息节奏变慢", 0.82),
            ],
        ),
    ]

    segments = _build_segments(observations, duration=2.0, interval=0.5)

    assert [segment.text for segment in segments] == [
        "我的鞋底，正和山说话",
        "鞋底与青苔的触碰",
        "喘息节奏变慢",
    ]
    assert segments[0].start == 0.0
    assert segments[0].end == 2.0
    assert segments[1].end == 1.0
    assert segments[2].start == 1.0


def test_ocr_sampling_adapts_to_video_duration():
    extractor = RapidOcrCaptionExtractor()

    assert extractor._sample_interval(5) == 0.5
    assert extractor._sample_interval(15) == 0.8
    assert extractor._sample_interval(39) == 1.0
    assert extractor._sample_interval(180) == 1.5


def test_short_single_sample_caption_is_retained():
    segments = _build_segments(
        [(0.4, [_ObservedLine("市面上所有的", 0.8)])],
        duration=1.0,
        interval=1.0,
    )

    assert [segment.text for segment in segments] == ["市面上所有的"]


def test_persistent_overlay_text_is_not_exported_as_caption():
    observations = [
        (
            float(index),
            [
                _ObservedLine("固定作者水印", 0.1),
                _ObservedLine(f"第{index}句主字幕", 0.8),
            ],
        )
        for index in range(10)
    ]

    segments = _build_segments(observations, duration=10.0, interval=1.0)

    assert "固定作者水印" not in [segment.text for segment in segments]
    assert len(segments) == 10


def test_center_caption_is_separated_from_right_side_watermark():
    output = SimpleNamespace(
        boxes=[
            [[300, 300], [700, 300], [700, 350], [300, 350]],
            [[950, 305], [1180, 305], [1180, 350], [950, 350]],
        ],
        txts=["那之前下载提取", "抖音号：52789219181"],
        scores=[0.98, 0.99],
    )

    lines = _lines_from_output(output, (500, 1200, 3), 0.72)

    assert [line.text for line in lines] == ["那之前下载提取"]
