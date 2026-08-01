import type { SVGProps } from 'react'

// Simple, crisp outline icons for the nav rail — one per NavItem, matching
// its label. All share the same stroke treatment; only the shapes differ.
const iconProps: SVGProps<SVGSVGElement> = {
  width: 17,
  height: 17,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 2,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': 'true',
}

export function HomeIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 11.5 12 4l9 7.5" />
      <path d="M5.5 10v9a1 1 0 0 0 1 1h4v-6h3v6h4a1 1 0 0 0 1-1v-9" />
    </svg>
  )
}

export function WorkspaceIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 7a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7Z" />
    </svg>
  )
}

export function AgentsIcon() {
  return (
    <svg {...iconProps}>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.2" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.2" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.2" />
    </svg>
  )
}

export function ExecutionIcon() {
  return (
    <svg {...iconProps}>
      <path d="M13 3 4 14h6l-1 7 9-11h-6l1-7Z" />
    </svg>
  )
}

export function GitIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="6" cy="6" r="2.3" />
      <circle cx="6" cy="18" r="2.3" />
      <circle cx="18" cy="12" r="2.3" />
      <path d="M6 8.3V15.7" />
      <path d="M8.3 6H12a3 3 0 0 1 3 3v.5" />
    </svg>
  )
}

export function TasksIcon() {
  return (
    <svg {...iconProps}>
      <path d="M4 6h.01" />
      <path d="M4 12h.01" />
      <path d="M4 18h.01" />
      <path d="M8.5 6h12" />
      <path d="M8.5 12h12" />
      <path d="M8.5 18h12" />
    </svg>
  )
}

export function ReportsIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  )
}

export function ArtifactsIcon() {
  return (
    <svg {...iconProps}>
      <path d="M7 3.5h7l4 4v13a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1v-16a1 1 0 0 1 1-1Z" />
      <path d="M14 3.5V8h4" />
      <path d="M9 13h6" />
      <path d="M9 16.5h6" />
    </svg>
  )
}

export function ReviewCenterIcon() {
  return (
    <svg {...iconProps}>
      <path d="M12 3.5 14.6 9l6 .9-4.3 4.2 1 6-5.3-2.8-5.3 2.8 1-6-4.3-4.2 6-.9Z" />
    </svg>
  )
}

const SETTINGS_SPOKE_ANGLES = [0, 45, 90, 135, 180, 225, 270, 315]

export function SettingsIcon() {
  return (
    <svg {...iconProps}>
      <circle cx="12" cy="12" r="3.2" />
      {SETTINGS_SPOKE_ANGLES.map((deg) => (
        <line key={deg} x1="12" y1="3.8" x2="12" y2="6.4" transform={`rotate(${deg} 12 12)`} />
      ))}
    </svg>
  )
}
