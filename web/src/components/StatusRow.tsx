type StatusRowProps = {
  project: string
  state: string | null
  unknownLabel: string
}

// Matches the real `repository_state` values emitted by
// `command_center/webapi/serializers.py` (`ok` / `unconfigured` /
// `invalid_path` / `not_git_repo`). `unconfigured` is not itself a health
// problem (no repository configured yet), so it stays neutral rather than
// warning-colored.
const REPO_STATE_COLOR: Record<string, string> = {
  ok: 'var(--ok)',
  unconfigured: 'var(--tx3)',
  invalid_path: 'var(--bad)',
  not_git_repo: 'var(--bad)',
}

export default function StatusRow({ project, state, unknownLabel }: StatusRowProps) {
  const color = state ? REPO_STATE_COLOR[state] || 'var(--tx3)' : 'var(--tx3)'
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0.55rem 0',
        borderBottom: '1px solid var(--gline)',
      }}
    >
      <span style={{ color: 'var(--tx)', fontSize: '0.84rem' }}>{project}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.72rem', color: 'var(--tx2)' }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: color, display: 'inline-block' }} />
        {state || unknownLabel}
      </span>
    </div>
  )
}
