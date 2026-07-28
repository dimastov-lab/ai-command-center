import type { ActivityItem } from '../lib/api'

type ActivityRowProps = {
  item: ActivityItem
  fallbackLabel: string
}

// `activity` entries come from two merged upstream shapes (raw activity-log
// events and derived v2-run lifecycle events — see the `ActivityItem`
// comment in lib/api.ts), so most fields are optional. We render whatever is
// actually present rather than assume a fixed shape.
export default function ActivityRow({ item, fallbackLabel }: ActivityRowProps) {
  const label = item.message || item.event_type || item.type || fallbackLabel
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.15rem', padding: '0.6rem 0', borderBottom: '1px solid var(--gline)' }}>
      <div style={{ color: 'var(--tx)', fontSize: '0.84rem' }}>{label}</div>
      <div style={{ display: 'flex', gap: '0.5rem', color: 'var(--tx3)', fontSize: '0.72rem' }}>
        {item.project && <span>{item.project}</span>}
        {item.ts && <span>{item.ts}</span>}
      </div>
    </div>
  )
}
