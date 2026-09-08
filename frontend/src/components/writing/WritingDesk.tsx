import { useState } from 'react';
import type { AppState } from '../../App';
import { api, ApiClientError } from '../../api/client';

type Props = { state: AppState; setPartial: (p: Partial<AppState>) => void };

export default function WritingDesk({ state, setPartial }: Props) {
  const { draft, evidence } = state;
  const [selectedTitle, setSelectedTitle] = useState(draft?.selected_title || '');
  const [body, setBody] = useState(draft?.body || '');
  const [saving, setSaving] = useState(false);
  const [exportFormat, setExportFormat] = useState('markdown');

  if (!draft) return null;

  const quality = draft.quality;

  async function save() {
    setSaving(true);
    try {
      const updated = await api.updateDraft(draft!.id, selectedTitle || draft!.selected_title, body);
      setPartial({ draft: updated });
    } catch (err) {
      setPartial({ error: err instanceof ApiClientError ? err.message : '保存失败' });
    } finally {
      setSaving(false);
    }
  }

  function copyExport() {
    api.exportDraft(draft!.id, exportFormat).then((text) => {
      navigator.clipboard.writeText(text).then(() => alert('已复制到剪贴板，可粘贴到秀米或公众号编辑器。'));
    }).catch(() => alert('导出失败'));
  }

  function downloadExport() {
    api.exportDraft(draft!.id, exportFormat).then((text) => {
      const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `draft-${draft!.id}.${exportFormat === 'html' ? 'html' : 'md'}`;
      a.click();
    }).catch(() => alert('导出失败'));
  }

  function rollbackToTrusted() {
    setSelectedTitle(draft!.titles[0]);
    setBody(draft!.body);
  }

  return (
    <article className="panel min-h-[640px]">
      <div className="panel-head">
        <h3>原创写作台</h3>
        <small>版本 {draft.version}</small>
      </div>
      <div className="p-6">
        <div className="mb-3 text-muted text-xs font-extrabold tracking-[1px]">标题候选</div>
        <div className="grid gap-1.5 mb-3.5">
          {draft.titles.map((title) => (
            <label key={title} className="flex gap-2 items-start text-[13px] leading-snug cursor-pointer">
              <input
                type="radio"
                name="draft-title"
                value={title}
                checked={selectedTitle === title}
                onChange={(e) => setSelectedTitle(e.target.value)}
              />
              <span>{title}</span>
            </label>
          ))}
        </div>

        <div className="mb-3 text-muted text-xs font-extrabold tracking-[1px]">
          正文 {state.evidence?.category === 'tool' ? '（工具研究稿）' : '（时讯研究稿）'}
        </div>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          className="w-full min-h-[320px] resize-y p-[18px] border border-line rounded-xl text-ink bg-white leading-relaxed outline-none focus:border-green focus:shadow-[0_0_0_3px_rgba(15,107,83,.1)]"
          aria-label="正文编辑器"
        />

        <div className={`mt-3 mb-3 py-3 px-3.5 rounded-xl text-xs leading-relaxed ${
          quality.can_export ? 'text-green bg-green-soft' : 'bg-[#f2eee5]'
        }`}>
          {quality.can_export
            ? '✅ 质量闸门已通过，可进入人工审核和导出。'
            : `⚠ 当前有 ${quality.blocking_issue_count} 个阻断项，${quality.issues.filter(i => !i.blocking).length} 个提示。`}
          {quality.issues.map((issue, i) => (
            <div key={i} className={issue.blocking ? 'text-red' : 'text-orange'}>
              {issue.blocking ? '🚫' : '💡'} {issue.message}
            </div>
          ))}
        </div>

        {draft.cited_source_urls.length > 0 && (
          <div className="mb-3 mt-3">
            <div className="mb-2 text-muted text-xs font-extrabold tracking-[1px]">参考来源</div>
            <div className="flex flex-wrap gap-2">
              {draft.cited_source_urls.map((url, i) => (
                <a key={url} href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 py-1.5 px-3 rounded-full bg-[#edf4ef] text-green text-xs no-underline hover:bg-green hover:text-white transition-colors">
                  查看原文 {i + 1} ↗
                </a>
              ))}
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-3 items-center">
          <button
            onClick={save}
            disabled={saving}
            className="border-0 rounded-xl py-3 px-[18px] font-extrabold text-white bg-green transition-transform hover:-translate-y-px disabled:cursor-wait disabled:opacity-60"
          >
            {saving ? '保存中…' : '保存修改并重新检查'}
          </button>
          <button
            onClick={rollbackToTrusted}
            className="border-0 rounded-xl py-3 px-[18px] font-extrabold text-ink bg-[#eee9dd] transition-transform hover:-translate-y-px"
          >
            恢复到可信版本
          </button>
          <select
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value)}
            className="py-2.5 px-3 border border-line rounded-xl bg-white text-sm"
          >
            <option value="markdown">导出 Markdown（秀米）</option>
            <option value="html">导出 HTML（公众号）</option>
            <option value="text">导出纯文本</option>
          </select>
          <button onClick={copyExport} className="border-0 rounded-xl py-2.5 px-3.5 font-bold text-sm text-green bg-green-soft hover:bg-green hover:text-white transition-colors">
            复制
          </button>
          <button onClick={downloadExport} className="border-0 rounded-xl py-2.5 px-3.5 font-bold text-sm text-green bg-green-soft hover:bg-green hover:text-white transition-colors">
            下载
          </button>
        </div>
      </div>
    </article>
  );
}
