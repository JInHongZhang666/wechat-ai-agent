import { useContext } from 'react'
import { AppearanceContext } from '../contexts/AppearanceContext'

export function useAppearance() {
  return useContext(AppearanceContext)
}
