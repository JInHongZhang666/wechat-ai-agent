import type { ButtonHTMLAttributes, ReactNode } from 'react'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'danger' | 'link'
  children: ReactNode
}

export default function Button({ variant = 'default', className = '', children, ...props }: ButtonProps) {
  const cls = `btn btn-${variant}${className ? ` ${className}` : ''}`
  return <button className={cls} {...props}>{children}</button>
}
