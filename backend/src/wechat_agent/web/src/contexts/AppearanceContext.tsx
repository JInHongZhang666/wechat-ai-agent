import { createContext, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

export type AmbientEffect = 'none' | 'petals' | 'rain'
export type BackgroundFit = 'cover' | 'contain' | 'original'

interface AppearancePreferences {
  effect: AmbientEffect
  backgroundFit: BackgroundFit
  backgroundPositionX: number
  backgroundPositionY: number
}

export interface CustomBackground {
  url: string
  name: string
  type: string
  size: number
  width: number
  height: number
}

export interface CustomAvatar {
  url: string
  name: string
  type: string
  size: number
}

interface StoredBackground {
  blob: Blob
  name: string
  type: string
  size: number
  width?: number
  height?: number
}

interface AppearanceContextValue {
  background: CustomBackground | null
  avatar: CustomAvatar | null
  effect: AmbientEffect
  backgroundFit: BackgroundFit
  backgroundPositionX: number
  backgroundPositionY: number
  isLoading: boolean
  setBackgroundFile: (file: File) => Promise<void>
  resetBackground: () => Promise<void>
  setAvatarFile: (file: File) => Promise<void>
  resetAvatar: () => Promise<void>
  setEffect: (effect: AmbientEffect) => void
  setBackgroundFit: (fit: BackgroundFit) => void
  setBackgroundPosition: (x: number, y: number) => void
}

const DB_NAME = 'ai-cabin-appearance'
const DB_VERSION = 1
const STORE_NAME = 'assets'
const BACKGROUND_KEY = 'custom-background'
const AVATAR_KEY = 'custom-avatar'
const PREFERENCES_KEY = 'ai-cabin.appearance.v1'
const DEFAULT_BACKGROUND_REVISION_KEY = 'ai-cabin.default-background-revision'
const DEFAULT_BACKGROUND_REVISION = 'columbina-wallpaper-05-v1'

export const AppearanceContext = createContext<AppearanceContextValue>({
  background: null,
  avatar: null,
  effect: 'none',
  backgroundFit: 'cover',
  backgroundPositionX: 50,
  backgroundPositionY: 50,
  isLoading: true,
  setBackgroundFile: async () => {},
  resetBackground: async () => {},
  setAvatarFile: async () => {},
  resetAvatar: async () => {},
  setEffect: () => {},
  setBackgroundFit: () => {},
  setBackgroundPosition: () => {},
})

function openDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('无法打开背景存储。'))
  })
}

async function readStoredAsset(key: string) {
  const database = await openDatabase()
  return new Promise<StoredBackground | null>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readonly')
    const request = transaction.objectStore(STORE_NAME).get(key)
    request.onsuccess = () => resolve((request.result as StoredBackground | undefined) ?? null)
    request.onerror = () => reject(request.error ?? new Error('无法读取背景。'))
    transaction.oncomplete = () => database.close()
  })
}

async function writeStoredAsset(key: string, asset: StoredBackground) {
  const database = await openDatabase()
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    transaction.objectStore(STORE_NAME).put(asset, key)
    transaction.oncomplete = () => { database.close(); resolve() }
    transaction.onerror = () => { database.close(); reject(transaction.error ?? new Error('无法保存背景。')) }
    transaction.onabort = () => { database.close(); reject(transaction.error ?? new Error('背景保存已取消。')) }
  })
}

async function deleteStoredAsset(key: string) {
  const database = await openDatabase()
  return new Promise<void>((resolve, reject) => {
    const transaction = database.transaction(STORE_NAME, 'readwrite')
    transaction.objectStore(STORE_NAME).delete(key)
    transaction.oncomplete = () => { database.close(); resolve() }
    transaction.onerror = () => { database.close(); reject(transaction.error ?? new Error('无法恢复默认背景。')) }
    transaction.onabort = () => { database.close(); reject(transaction.error ?? new Error('恢复默认背景已取消。')) }
  })
}

async function readBackgroundForCurrentRevision() {
  try {
    if (localStorage.getItem(DEFAULT_BACKGROUND_REVISION_KEY) !== DEFAULT_BACKGROUND_REVISION) {
      await deleteStoredAsset(BACKGROUND_KEY)
      localStorage.setItem(DEFAULT_BACKGROUND_REVISION_KEY, DEFAULT_BACKGROUND_REVISION)
      return null
    }
  } catch { /* keep the last stored background when browser storage is unavailable */ }
  return readStoredAsset(BACKGROUND_KEY)
}

function loadPreferences(): AppearancePreferences {
  try {
    const saved = JSON.parse(localStorage.getItem(PREFERENCES_KEY) || '{}') as Partial<AppearancePreferences>
    const effect = saved.effect === 'petals' || saved.effect === 'rain' ? saved.effect : 'none'
    const backgroundFit = saved.backgroundFit === 'contain' || saved.backgroundFit === 'original' ? saved.backgroundFit : 'cover'
    const backgroundPositionX = clampPercentage(saved.backgroundPositionX)
    const backgroundPositionY = clampPercentage(saved.backgroundPositionY)
    return { effect, backgroundFit, backgroundPositionX, backgroundPositionY }
  } catch { /* storage disabled */ }
  return { effect: 'none', backgroundFit: 'cover', backgroundPositionX: 50, backgroundPositionY: 50 }
}

function clampPercentage(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 50
  return Math.min(100, Math.max(0, Math.round(value)))
}

function readImageDimensions(blob: Blob) {
  return new Promise<{ width: number; height: number }>((resolve, reject) => {
    const objectUrl = URL.createObjectURL(blob)
    const image = new Image()
    image.onload = () => {
      resolve({ width: image.naturalWidth, height: image.naturalHeight })
      URL.revokeObjectURL(objectUrl)
    }
    image.onerror = () => {
      reject(new Error('无法读取图片分辨率。'))
      URL.revokeObjectURL(objectUrl)
    }
    image.src = objectUrl
  })
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [background, setBackground] = useState<CustomBackground | null>(null)
  const [avatar, setAvatar] = useState<CustomAvatar | null>(null)
  const [preferences, setPreferences] = useState<AppearancePreferences>(loadPreferences)
  const [isLoading, setIsLoading] = useState(true)
  const objectUrlRef = useRef<string | null>(null)
  const avatarObjectUrlRef = useRef<string | null>(null)

  const showStoredBackground = useCallback((stored: StoredBackground | null) => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
    if (!stored) {
      objectUrlRef.current = null
      setBackground(null)
      return
    }
    const url = URL.createObjectURL(stored.blob)
    objectUrlRef.current = url
    setBackground({
      url,
      name: stored.name,
      type: stored.type,
      size: stored.size,
      width: stored.width ?? 0,
      height: stored.height ?? 0,
    })
  }, [])

  const showStoredAvatar = useCallback((stored: StoredBackground | null) => {
    if (avatarObjectUrlRef.current) URL.revokeObjectURL(avatarObjectUrlRef.current)
    if (!stored) {
      avatarObjectUrlRef.current = null
      setAvatar(null)
      return
    }
    const url = URL.createObjectURL(stored.blob)
    avatarObjectUrlRef.current = url
    setAvatar({ url, name: stored.name, type: stored.type, size: stored.size })
  }, [])

  useEffect(() => {
    let active = true
    Promise.all([
      readBackgroundForCurrentRevision().then(async stored => {
        if (!stored || (stored.width && stored.height)) return stored
        const dimensions = await readImageDimensions(stored.blob)
        const upgraded = { ...stored, ...dimensions }
        try { await writeStoredAsset(BACKGROUND_KEY, upgraded) }
        catch { /* dimensions can still be used for this session */ }
        return upgraded
      }),
      readStoredAsset(AVATAR_KEY),
    ])
      .then(([storedBackground, storedAvatar]) => {
        if (!active) return
        showStoredBackground(storedBackground)
        showStoredAvatar(storedAvatar)
      })
      .catch(() => {
        if (!active) return
        setBackground(null)
        setAvatar(null)
      })
      .finally(() => { if (active) setIsLoading(false) })
    return () => {
      active = false
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current)
      if (avatarObjectUrlRef.current) URL.revokeObjectURL(avatarObjectUrlRef.current)
    }
  }, [showStoredAvatar, showStoredBackground])

  const setBackgroundFile = useCallback(async (file: File) => {
    const dimensions = await readImageDimensions(file)
    const stored = { blob: file, name: file.name, type: file.type, size: file.size, ...dimensions }
    await writeStoredAsset(BACKGROUND_KEY, stored)
    showStoredBackground(stored)
  }, [showStoredBackground])

  const resetBackground = useCallback(async () => {
    await deleteStoredAsset(BACKGROUND_KEY)
    showStoredBackground(null)
  }, [showStoredBackground])

  const setAvatarFile = useCallback(async (file: File) => {
    await readImageDimensions(file)
    const stored = { blob: file, name: file.name, type: file.type, size: file.size }
    await writeStoredAsset(AVATAR_KEY, stored)
    showStoredAvatar(stored)
  }, [showStoredAvatar])

  const resetAvatar = useCallback(async () => {
    await deleteStoredAsset(AVATAR_KEY)
    showStoredAvatar(null)
  }, [showStoredAvatar])

  const updatePreferences = useCallback((patch: Partial<AppearancePreferences>) => {
    setPreferences(current => {
      const next = { ...current, ...patch }
      try { localStorage.setItem(PREFERENCES_KEY, JSON.stringify(next)) }
      catch { /* storage disabled */ }
      return next
    })
  }, [])

  const setEffect = useCallback((effect: AmbientEffect) => updatePreferences({ effect }), [updatePreferences])
  const setBackgroundFit = useCallback((backgroundFit: BackgroundFit) => updatePreferences({ backgroundFit }), [updatePreferences])
  const setBackgroundPosition = useCallback((x: number, y: number) => updatePreferences({
    backgroundPositionX: clampPercentage(x),
    backgroundPositionY: clampPercentage(y),
  }), [updatePreferences])

  const value = useMemo(() => ({
    background,
    avatar,
    effect: preferences.effect,
    backgroundFit: preferences.backgroundFit,
    backgroundPositionX: preferences.backgroundPositionX,
    backgroundPositionY: preferences.backgroundPositionY,
    isLoading,
    setBackgroundFile,
    resetBackground,
    setAvatarFile,
    resetAvatar,
    setEffect,
    setBackgroundFit,
    setBackgroundPosition,
  }), [avatar, background, isLoading, preferences, resetAvatar, resetBackground, setAvatarFile, setBackgroundFile, setBackgroundFit, setBackgroundPosition, setEffect])

  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>
}
