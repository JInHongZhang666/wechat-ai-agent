import { useMemo, useRef, useState, type ChangeEvent } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import Button from '../components/Button'
import { CreatorPageHeader, CreatorSectionTitle, CreatorStatus } from '../components/CreatorPageScaffold'
import { useTheme } from '../hooks/useTheme'
import { useAppearance } from '../hooks/useAppearance'
import type { AmbientEffect, BackgroundFit } from '../contexts/AppearanceContext'
import type { ThemeMode } from '../types'

interface CreatorPreferences {
  autoSave: boolean
  confirmBeforeExport: boolean
  defaultLength: 'short' | 'medium' | 'long'
  defaultStyle: string
  defaultPrompt: 'aigc' | 'classic'
}

type SettingSection = 'general' | 'privacy' | 'appearance'

interface SettingSectionDefinition {
  id: SettingSection
  title: string
  description: string
  summary: string
}

const STORAGE_KEY = 'ai-cabin.creator-preferences.v1'
const WRITER_SETTINGS_KEY = 'ai-cabin.writer-settings.v1'
const DEFAULT_AVATAR = '/assets/user-avatar-default.jpg'
const DEFAULTS: CreatorPreferences = {
  autoSave: true,
  confirmBeforeExport: true,
  defaultLength: 'medium',
  defaultStyle: '专业理性',
  defaultPrompt: 'aigc',
}

const SETTING_SECTIONS: SettingSectionDefinition[] = [
  {
    id: 'general',
    title: '通用设置',
    description: '管理自动暂存、导出确认和新建文章的默认值。',
    summary: '应用行为与创作默认值',
  },
  {
    id: 'privacy',
    title: '数据与隐私',
    description: '查看本地数据、项目数据库和服务端配置的边界。',
    summary: '本地存储与数据边界',
  },
  {
    id: 'appearance',
    title: '外观',
    description: '更换图片或 GIF 背景，并设置主题和环境效果。',
    summary: '背景、主题与环境效果',
  },
]

function loadPreferences() {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') } as CreatorPreferences
  } catch {
    return DEFAULTS
  }
}

function isSettingSection(value: string | undefined): value is SettingSection {
  return SETTING_SECTIONS.some(section => section.id === value)
}

export default function CreatorSettingsPage() {
  const navigate = useNavigate()
  const { section } = useParams<{ section?: string }>()
  const { mode, setMode } = useTheme()
  const { effect } = useAppearance()
  const [saved, setSaved] = useState<CreatorPreferences>(loadPreferences)
  const [draft, setDraft] = useState<CreatorPreferences>(saved)
  const [notice, setNotice] = useState('')
  const dirty = useMemo(() => JSON.stringify(saved) !== JSON.stringify(draft), [saved, draft])

  const update = <K extends keyof CreatorPreferences>(key: K, value: CreatorPreferences[K]) => {
    setDraft(current => ({ ...current, [key]: value }))
    setNotice('')
  }

  const save = () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(draft))
    let writerSettings: Record<string, unknown> = {}
    try { writerSettings = JSON.parse(localStorage.getItem(WRITER_SETTINGS_KEY) || '{}') }
    catch { writerSettings = {} }
    localStorage.setItem(WRITER_SETTINGS_KEY, JSON.stringify({
      ...writerSettings,
      length: draft.defaultLength,
      writingStyle: draft.defaultStyle,
      promptVariant: draft.defaultPrompt,
    }))
    setSaved(draft)
    setNotice('设置已保存到本机，新建创作任务时生效。')
  }

  const reset = () => {
    if (!window.confirm('确定恢复本页默认设置吗？已有文章和项目不会被删除。')) return
    setDraft(DEFAULTS)
    setNotice('已恢复默认值，点击“保存设置”后生效。')
  }

  if (section && !isSettingSection(section)) {
    return <Navigate replace to="/create/settings" />
  }

  const activeSection = section as SettingSection | undefined

  if (!activeSection) {
    return (
      <div className="creator-tool-page creator-settings-page">
        <CreatorPageHeader
          title="设置"
          description="选择一个设置分类，进入对应页面进行调整。"
        />
        <main className="creator-surface settings-home-panel">
          <CreatorSectionTitle title="设置分类" description="所有选项按用途归类，修改内容仍保存在原来的位置。" />
          <nav className="settings-home-list" aria-label="设置分类">
            {SETTING_SECTIONS.map(item => (
              <Link className="settings-home-link" to={`/create/settings/${item.id}`} key={item.id}>
                <span className="settings-home-icon" aria-hidden="true"><SettingSectionIcon section={item.id} /></span>
                <span className="settings-home-copy">
                  <strong>{item.title}</strong>
                  <span>{item.description}</span>
                </span>
                <span className="settings-home-end">
                  <small>{item.id === 'appearance' ? `当前：${themeModeLabel(mode)} · ${ambientEffectLabel(effect)}` : item.summary}</small>
                  <ChevronRightIcon />
                </span>
              </Link>
            ))}
          </nav>
        </main>
      </div>
    )
  }

  const activeDefinition = SETTING_SECTIONS.find(item => item.id === activeSection)!

  return (
    <div className="creator-tool-page creator-settings-page">
      <CreatorPageHeader
        title={activeDefinition.title}
        description={activeDefinition.description}
        actions={<Button className="settings-back-button" onClick={() => navigate('/create/settings')}><BackIcon />返回设置</Button>}
      />
      {notice ? <p className="creator-inline-notice">{notice}</p> : null}

      <main className="creator-surface settings-form-panel settings-single-panel">
        {activeSection === 'general' ? <>
          <AvatarSettings />

          <section>
            <CreatorSectionTitle title="通用设置" description="调整创作过程中常用的本地行为。" />
            <SettingToggle label="自动暂存" description="编辑文章时在本机保存未提交内容。" checked={draft.autoSave} onChange={value => update('autoSave', value)} />
            <SettingToggle label="导出前再次确认" description="避免尚未核对的内容被误导出。" checked={draft.confirmBeforeExport} onChange={value => update('confirmBeforeExport', value)} />
          </section>

          <section>
            <CreatorSectionTitle title="创作默认值" description="用于新建项目，不会覆盖已有项目设置。" />
            <SettingSelect label="默认文章长度" description="控制新文章初始字数目标。" value={draft.defaultLength} onChange={value => update('defaultLength', value as CreatorPreferences['defaultLength'])} options={[["short", "短文 · 650 字"], ["medium", "中等 · 800 字"], ["long", "长文 · 950 字"]]} />
            <SettingSelect label="默认表达风格" description="工作台中仍可针对单篇文章调整。" value={draft.defaultStyle} onChange={value => update('defaultStyle', value)} options={['专业理性', '温暖治愈', '犀利直接', '轻松口语', '故事感强', '干货清单'].map(value => [value, value])} />
            <SettingSelect label="默认提示词模板" description="所有模板仍受证据和原创度规则约束。" value={draft.defaultPrompt} onChange={value => update('defaultPrompt', value as CreatorPreferences['defaultPrompt'])} options={[["aigc", "通用模板 · 口语增强"], ["classic", "经典模板 · 稳重克制"]]} />
          </section>

          <SettingsSaveBar dirty={dirty} onReset={reset} onSave={save} />
        </> : null}

        {activeSection === 'privacy' ? (
          <section>
            <CreatorSectionTitle title="数据与隐私" description="明确区分本地数据与服务端运行配置。" />
            <div className="settings-data-row"><div><strong>创作偏好、提示词、头像与自定义背景</strong><span>保存在当前浏览器 localStorage 与 IndexedDB</span></div><CreatorStatus tone="success">仅本机</CreatorStatus></div>
            <div className="settings-data-row"><div><strong>项目、证据与草稿版本</strong><span>保存在项目配置的 SQLite 数据库</span></div><CreatorStatus tone="accent">项目数据</CreatorStatus></div>
            <div className="settings-data-row"><div><strong>模型与采集服务</strong><span>由后端环境变量和运行策略控制</span></div><CreatorStatus>服务端</CreatorStatus></div>
          </section>
        ) : null}

        {activeSection === 'appearance' ? <AppearanceSettings mode={mode} setMode={setMode} /> : null}
      </main>
    </div>
  )
}

function AvatarSettings() {
  const { avatar, setAvatarFile, resetAvatar } = useAppearance()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const handleFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('请选择 JPG、PNG 或 WebP 图片。')
      setMessage('')
      return
    }
    if (file.size > 8 * 1024 * 1024) {
      setError('头像图片不能超过 8 MB。')
      setMessage('')
      return
    }
    setUploading(true)
    setError('')
    try {
      await setAvatarFile(file)
      setMessage(`已更新头像为“${file.name}”。`)
    } catch {
      setError('头像保存失败，请检查浏览器存储空间后重试。')
      setMessage('')
    } finally {
      setUploading(false)
    }
  }

  const restoreDefault = async () => {
    setUploading(true)
    setError('')
    try {
      await resetAvatar()
      setMessage('已恢复默认头像。')
    } catch {
      setError('恢复默认头像失败，请稍后重试。')
      setMessage('')
    } finally {
      setUploading(false)
    }
  }

  return (
    <section>
      <CreatorSectionTitle title="我的头像" description="用于灵犀探讯和灵犀策划中的用户消息，图片只保存在当前浏览器。" />
      <div className="settings-avatar-editor">
        <img src={avatar?.url ?? DEFAULT_AVATAR} alt="当前用户头像" />
        <div className="settings-avatar-copy">
          <strong>{avatar ? '使用自定义头像' : '使用默认头像'}</strong>
          <span>{avatar ? `${avatar.name} · ${formatFileSize(avatar.size)}` : '支持 JPG、PNG 和 WebP，最大 8 MB。'}</span>
          <div>
            <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp" onChange={handleFile} />
            <Button variant="primary" onClick={() => inputRef.current?.click()} disabled={uploading}>{uploading ? '处理中…' : '更换头像'}</Button>
            <Button onClick={restoreDefault} disabled={uploading || !avatar}>恢复默认头像</Button>
          </div>
          {message ? <p className="settings-background-message is-success">{message}</p> : null}
          {error ? <p className="settings-background-message is-error">{error}</p> : null}
        </div>
      </div>
    </section>
  )
}

function AppearanceSettings({ mode, setMode }: { mode: ThemeMode; setMode: (mode: ThemeMode) => void }) {
  const {
    background,
    effect,
    backgroundFit,
    backgroundPositionX,
    backgroundPositionY,
    isLoading,
    setBackgroundFile,
    resetBackground,
    setEffect,
    setBackgroundFit,
    setBackgroundPosition,
  } = useAppearance()
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const handleFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp', 'image/gif'].includes(file.type)) {
      setError('请选择 JPG、PNG、WebP 或 GIF 文件。')
      setMessage('')
      return
    }
    if (file.size > 25 * 1024 * 1024) {
      setError('背景文件不能超过 25 MB。')
      setMessage('')
      return
    }
    setUploading(true)
    setError('')
    try {
      await setBackgroundFile(file)
      setMessage(`已应用“${file.name}”作为全站背景。`)
    } catch {
      setError('背景保存失败，请检查浏览器存储空间后重试。')
      setMessage('')
    } finally {
      setUploading(false)
    }
  }

  const restoreDefault = async () => {
    setUploading(true)
    setError('')
    try {
      await resetBackground()
      setMessage('已恢复项目默认动态背景。')
    } catch {
      setError('恢复默认背景失败，请稍后重试。')
      setMessage('')
    } finally {
      setUploading(false)
    }
  }

  const moveBackground = (axis: 'x' | 'y', value: number) => {
    if (background && background.width && background.height && backgroundFit !== 'original') {
      const imageRatio = background.width / background.height
      const viewportRatio = window.innerWidth / window.innerHeight
      const canMove = axis === 'x'
        ? (backgroundFit === 'cover' ? imageRatio > viewportRatio : imageRatio < viewportRatio)
        : (backgroundFit === 'cover' ? imageRatio < viewportRatio : imageRatio > viewportRatio)

      if (!canMove) {
        const nextFit: BackgroundFit = backgroundFit === 'cover' ? 'contain' : 'cover'
        setBackgroundFit(nextFit)
        setMessage(`已自动切换为“${backgroundFitLabel(nextFit)}”，现在可以调整${axis === 'x' ? '水平' : '垂直'}位置。`)
        setError('')
      }
    }

    setBackgroundPosition(
      axis === 'x' ? value : backgroundPositionX,
      axis === 'y' ? value : backgroundPositionY,
    )
  }

  return <>
    <section>
      <CreatorSectionTitle title="背景" description="上传后会立即应用到全部页面，并仅保存在当前浏览器。" />
      <div className="settings-background-editor">
        <div className="settings-background-preview">
          <img
            src={background?.url ?? '/assets/columbina-wallpaper-05-hq-poster.jpg'}
            alt="当前背景预览"
            style={background ? {
              objectFit: backgroundFit === 'original' ? 'none' : backgroundFit,
              objectPosition: `${backgroundPositionX}% ${backgroundPositionY}%`,
            } : undefined}
          />
          <span>{isLoading ? '正在读取背景…' : background ? background.name : '项目默认动态背景'}</span>
        </div>
        <div className="settings-background-details">
          <strong>{background ? '自定义背景已启用' : '使用项目默认背景'}</strong>
          <span>{background ? `${background.type.replace('image/', '').toUpperCase()} · ${formatFileSize(background.size)}${background.width && background.height ? ` · ${background.width} × ${background.height}` : ''}` : '支持 JPG、PNG、WebP 和 GIF，最大 25 MB。'}</span>
          {background ? <span className="settings-quality-note">原文件直接显示，不压缩、不重新编码，并已关闭背景滤镜。</span> : null}
          <div>
            <input ref={inputRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={handleFile} />
            <Button variant="primary" onClick={() => inputRef.current?.click()} disabled={uploading}>{uploading ? '处理中…' : '选择图片或 GIF'}</Button>
            <Button onClick={restoreDefault} disabled={uploading || !background}>恢复默认背景</Button>
          </div>
          {message ? <p className="settings-background-message is-success">{message}</p> : null}
          {error ? <p className="settings-background-message is-error">{error}</p> : null}
        </div>
      </div>
    </section>

    <section>
      <CreatorSectionTitle title="图片布局" description="调整图片如何铺满屏幕，以及画面焦点在水平和垂直方向的位置。" />
      <div className="settings-control-row">
        <div><strong>显示方式</strong><span>“原始尺寸”不放大图片，清晰度最高；“填满屏幕”可能裁切边缘。</span></div>
        <div className="settings-fit-switch" role="group" aria-label="背景图片显示方式">
          {(['cover', 'contain', 'original'] as BackgroundFit[]).map(value => (
            <button
              type="button"
              aria-pressed={backgroundFit === value}
              className={backgroundFit === value ? 'is-active' : ''}
              onClick={() => setBackgroundFit(value)}
              disabled={!background}
              key={value}
            >{backgroundFitLabel(value)}</button>
          ))}
        </div>
      </div>
      <div className={`settings-position-panel${background ? '' : ' is-disabled'}`}>
        <div className="settings-position-head">
        <div><strong>画面位置</strong><span>拖动滑块调整焦点；当前模式没有对应方向的移动空间时，会自动切换显示方式。</span></div>
          <Button onClick={() => setBackgroundPosition(50, 50)} disabled={!background || (backgroundPositionX === 50 && backgroundPositionY === 50)}>恢复居中</Button>
        </div>
        <label className="settings-position-slider">
          <span>水平位置</span>
          <input aria-label="背景水平位置" type="range" min="0" max="100" step="1" value={backgroundPositionX} onChange={event => moveBackground('x', Number(event.target.value))} disabled={!background} />
          <output>{backgroundPositionX}%</output>
        </label>
        <label className="settings-position-slider">
          <span>垂直位置</span>
          <input aria-label="背景垂直位置" type="range" min="0" max="100" step="1" value={backgroundPositionY} onChange={event => moveBackground('y', Number(event.target.value))} disabled={!background} />
          <output>{backgroundPositionY}%</output>
        </label>
        {!background ? <p>上传自定义图片或 GIF 后即可调整。</p> : <p>位置设置会自动保存，并同步应用到全部页面。</p>}
      </div>
    </section>

    <section>
      <CreatorSectionTitle title="主题" description="主题设置会立即应用到全部页面。" />
      <div className="settings-control-row"><div><strong>主题模式</strong><span>可跟随系统，也可以固定深色或浅色。</span></div><div className="settings-theme-switch" role="group" aria-label="主题模式">{(['auto', 'dark', 'light'] as ThemeMode[]).map(value => <button type="button" className={mode === value ? 'is-active' : ''} onClick={() => setMode(value)} key={value}>{themeModeLabel(value)}</button>)}</div></div>
    </section>

    <section>
      <CreatorSectionTitle title="环境效果" description="在背景上叠加轻量动画，选择后自动保存。" />
      <div className="settings-control-row"><div><strong>动态效果</strong><span>系统启用“减少动态效果”时将自动隐藏动画层。</span></div><div className="settings-effect-switch" role="group" aria-label="环境效果">{(['none', 'petals', 'rain'] as AmbientEffect[]).map(value => <button type="button" aria-pressed={effect === value} className={effect === value ? 'is-active' : ''} onClick={() => setEffect(value)} key={value}>{ambientEffectLabel(value)}</button>)}</div></div>
    </section>
  </>
}

function SettingsSaveBar({ dirty, onReset, onSave }: { dirty: boolean; onReset: () => void; onSave: () => void }) {
  return <div className="settings-save-bar"><span>{dirty ? '有未保存的设置' : '全部设置已保存'}</span><div><Button onClick={onReset}>恢复默认值</Button><Button variant="primary" onClick={onSave} disabled={!dirty}>保存设置</Button></div></div>
}

function themeModeLabel(mode: ThemeMode) {
  return mode === 'auto' ? '跟随系统' : mode === 'dark' ? '深色' : '浅色'
}

function ambientEffectLabel(effect: AmbientEffect) {
  return effect === 'petals' ? '落花' : effect === 'rain' ? '雨滴' : '无效果'
}

function backgroundFitLabel(fit: BackgroundFit) {
  return fit === 'contain' ? '完整显示' : fit === 'original' ? '原始尺寸' : '填满屏幕'
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function SettingSectionIcon({ section }: { section: SettingSection }) {
  if (section === 'privacy') return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3 5.5 6v5.2c0 4.2 2.5 7.7 6.5 9.8 4-2.1 6.5-5.6 6.5-9.8V6L12 3Z"/><path d="M9.5 12.2 11.2 14l3.6-4"/></svg>
  if (section === 'appearance') return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="3.5"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4"/></svg>
  return <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="17" r="2"/></svg>
}

function ChevronRightIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m7.5 4.5 5.5 5.5-5.5 5.5"/></svg>
}

function BackIcon() {
  return <svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="m9 4-6 6 6 6M3 10h14"/></svg>
}

function SettingToggle({ label, description, checked, onChange }: { label: string; description: string; checked: boolean; onChange: (value: boolean) => void }) {
  return <div className="settings-control-row"><div><strong>{label}</strong><span>{description}</span></div><button type="button" role="switch" aria-checked={checked} className={`settings-toggle${checked ? ' is-on' : ''}`} onClick={() => onChange(!checked)}><i /></button></div>
}

function SettingSelect({ label, description, value, onChange, options }: { label: string; description: string; value: string; onChange: (value: string) => void; options: string[][] }) {
  return <label className="settings-control-row"><div><strong>{label}</strong><span>{description}</span></div><select value={value} onChange={event => onChange(event.target.value)}>{options.map(([optionValue, optionLabel]) => <option value={optionValue} key={optionValue}>{optionLabel}</option>)}</select></label>
}
