"""Safe, self-contained exports for WeChat official-account editors."""

from __future__ import annotations

from html import escape
import re

from wechat_agent.domain.models import DraftDocument, EvidencePack


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def export_wechat_html(draft: DraftDocument, pack: EvidencePack) -> str:
    """Return inline-styled HTML that survives paste into WeChat's editor."""
    blocks: list[str] = [
        '<section style="max-width:677px;margin:0 auto;color:#1f2937;'
        'font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;'
        'font-size:16px;line-height:1.85;letter-spacing:.02em;">',
        f'<h1 style="margin:0 0 28px;font-size:26px;line-height:1.4;'
        f'font-weight:750;color:#111827;">{escape(draft.title)}</h1>',
    ]
    for raw in draft.body.split("\n\n"):
        text = raw.strip()
        if not text:
            continue
        if text.startswith("## "):
            blocks.append(
                '<h2 style="margin:34px 0 14px;padding-left:12px;'
                'border-left:4px solid #126f7c;font-size:20px;line-height:1.5;'
                f'font-weight:700;color:#111827;">{escape(text[3:].strip())}</h2>'
            )
            continue
        if text.startswith("> "):
            blocks.append(
                '<blockquote style="margin:20px 0;padding:14px 16px;'
                'border-left:3px solid #94a3b8;background:#f8fafc;color:#475569;">'
                f'{_inline(text[2:])}</blockquote>'
            )
            continue
        blocks.append(
            '<p style="margin:0 0 16px;text-align:justify;word-break:break-word;">'
            f'{_inline(text)}</p>'
        )
    blocks.append(
        '<section style="margin-top:36px;padding-top:18px;border-top:1px solid #e5e7eb;">'
        '<p style="margin:0 0 10px;font-size:14px;font-weight:700;color:#334155;">参考来源</p>'
    )
    for index, url in enumerate(draft.cited_source_urls, 1):
        safe_url = escape(url, quote=True)
        blocks.append(
            f'<p style="margin:5px 0;font-size:12px;line-height:1.6;color:#64748b;">'
            f'[{index}] <a href="{safe_url}" style="color:#126f7c;text-decoration:none;">'
            f'{safe_url}</a></p>'
        )
    if pack.unverified_claims:
        blocks.append(
            '<p style="margin:18px 0 0;padding:10px 12px;background:#fff7ed;'
            'color:#9a3412;font-size:12px;">本文不将待核实内容作为确定结论。</p>'
        )
    blocks.extend([
        '<p style="margin:28px 0 0;padding:12px;background:#f8fafc;color:#64748b;'
        'font-size:12px;text-align:center;">配图建议：封面 2.35:1；正文关键章节可各配一张信息图。</p>',
        "</section></section>",
    ])
    return "".join(blocks)


def _inline(text: str) -> str:
    escaped = escape(text).replace("\n", "<br>")
    return _BOLD.sub(r'<strong style="font-weight:700;color:#111827;">\1</strong>', escaped)
