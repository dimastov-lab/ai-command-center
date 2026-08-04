import type { ReactNode } from 'react'

type NavItemProps = {
  label: string
  icon: ReactNode
  active?: boolean
}

export default function NavItem({ label, icon, active }: NavItemProps) {
  return (
    <div
      className="nav-item"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.65rem',
        padding: '0.55rem 0.7rem',
        borderRadius: 10,
        color: active ? 'var(--tx)' : 'var(--tx3)',
        background: active ? 'rgba(124,92,255,0.16)' : 'transparent',
        fontSize: '0.82rem',
        fontWeight: active ? 600 : 500,
      }}
    >
      <span style={{ display: 'inline-flex', width: 16, color: active ? 'var(--accent-2)' : 'inherit' }}>{icon}</span>
      {label}
    </div>
  )
}
