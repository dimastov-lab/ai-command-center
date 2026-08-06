import type { ReactNode } from 'react'

type GlassPanelProps = {
  title?: string
  action?: ReactNode
  className?: string
  children: ReactNode
}

// Generic glass-morphism surface used by every panel on the Home screen.
// The visual treatment (blur, border, radius) comes from the shared `.glass`
// class in theme/tokens.css; this component only adds panel padding and an
// optional header row (title + trailing action).
export default function GlassPanel({ title, action, className = '', children }: GlassPanelProps) {
  return (
    <section className={`glass${className ? ` ${className}` : ''}`} style={{ padding: '1.25rem' }}>
      {(title || action) && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.9rem' }}>
          {title && (
            <h2
              style={{
                margin: 0,
                fontSize: '0.8rem',
                fontWeight: 600,
                letterSpacing: '0.02em',
                color: 'var(--tx)',
                textTransform: 'uppercase',
              }}
            >
              {title}
            </h2>
          )}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
