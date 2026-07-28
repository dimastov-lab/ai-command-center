import { useState } from 'react'

type Preset = { id: string; label: string; value: string }

// Gradient presets built from the same design tokens used everywhere else
// (--accent-1/--accent-2/--teal/--warn), applied by setting the `--bg`
// custom property that theme/tokens.css'/index.css's `body` background
// reads from.
const PRESETS: Preset[] = [
  { id: 'midnight', label: 'Midnight', value: '#0b0e1a' },
  {
    id: 'nebula',
    label: 'Nebula',
    value:
      'radial-gradient(circle at 20% 20%, rgba(124,92,255,0.35), transparent 55%), radial-gradient(circle at 80% 0%, rgba(77,159,255,0.3), transparent 50%), #0b0e1a',
  },
  {
    id: 'aurora',
    label: 'Aurora',
    value:
      'radial-gradient(circle at 15% 85%, rgba(51,224,192,0.28), transparent 55%), radial-gradient(circle at 85% 15%, rgba(124,92,255,0.3), transparent 50%), #0b0e1a',
  },
  {
    id: 'dusk',
    label: 'Dusk',
    value:
      'radial-gradient(circle at 75% 75%, rgba(245,178,58,0.18), transparent 55%), radial-gradient(circle at 20% 20%, rgba(77,159,255,0.28), transparent 55%), #0b0e1a',
  },
]

type BackgroundSwitcherProps = { label: string }

export default function BackgroundSwitcher({ label }: BackgroundSwitcherProps) {
  const [active, setActive] = useState(PRESETS[0].id)

  function apply(preset: Preset) {
    document.documentElement.style.setProperty('--bg', preset.value)
    setActive(preset.id)
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
      <span style={{ fontSize: '0.7rem', color: 'var(--tx3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <div style={{ display: 'flex', gap: '0.4rem' }}>
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            title={p.label}
            aria-label={p.label}
            aria-pressed={active === p.id}
            onClick={() => apply(p)}
            className={`bg-swatch${active === p.id ? ' active' : ''}`}
            style={{
              width: 22,
              height: 22,
              borderRadius: '50%',
              border: '1px solid var(--gline)',
              padding: 0,
              cursor: 'pointer',
              backgroundImage: p.value.includes('gradient') ? p.value : undefined,
              backgroundColor: p.value.includes('gradient') ? undefined : p.value,
            }}
          />
        ))}
      </div>
    </div>
  )
}
