import { DEFAULT_BACKGROUND } from '../lib/backgroundPresets'

// Fixed, full-viewport background layer rendered once at the app root, sat
// behind every glass panel (z-index: -1). BackgroundSwitcher swaps the
// gradient by writing the `--bg` custom property on <html>; the `var(--bg,
// …)` fallback below reproduces the "auto" preset so the correct dusk-city
// gradient is visible on first paint, before React/JS ever runs.
// This component additionally owns the faint city silhouette pinned to the
// bottom of the layer, which stays constant across presets.
const CITY_RECTS: ReadonlyArray<readonly [number, number, number, number]> = [
  [40, 120, 70, 110],
  [120, 80, 54, 150],
  [185, 140, 60, 90],
  [255, 60, 44, 170],
  [310, 110, 66, 120],
  [470, 150, 80, 80],
  [560, 40, 40, 190],
  [610, 95, 58, 135],
  [680, 130, 70, 100],
  [900, 70, 46, 160],
  [955, 120, 64, 110],
  [1030, 150, 80, 80],
  [1180, 90, 52, 140],
  [1245, 130, 70, 100],
  [1330, 60, 42, 170],
]

export default function BackgroundLayer() {
  return (
    <div
      aria-hidden="true"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: -1,
        overflow: 'hidden',
        background: `var(--bg, ${DEFAULT_BACKGROUND.value})`,
        transition: 'background 0.4s ease',
      }}
    >
      <svg
        viewBox="0 0 1440 230"
        preserveAspectRatio="none"
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          width: '100%',
          height: '22vh',
          minHeight: 150,
          opacity: 0.5,
        }}
      >
        {CITY_RECTS.map(([x, y, w, h], i) => (
          <rect key={i} x={x} y={y} width={w} height={h} fill="#0a0e1e" />
        ))}
      </svg>
    </div>
  )
}
