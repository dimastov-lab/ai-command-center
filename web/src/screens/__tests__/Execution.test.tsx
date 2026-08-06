import { render, screen, waitFor } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import Execution from '../Execution'

vi.mock('../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../lib/api')>('../../lib/api')
  return {
    ...actual,
    fetchExecution: vi.fn().mockResolvedValue({
      summary: { visible_runs: 1, active: 1, completed: 0, needs_attention: 0 },
      state_counts: { RUNNING: 1 },
      runs: [{
        id: 'r1', source: 'v2', title: 'implementation', project: 'AICC', project_name: 'AI Command Center',
        task_type: 'implementation', state: 'RUNNING', created_at: '2026-08-03T10:00:00', started_at: '2026-08-03T10:00:00',
        completed_at: null, duration_seconds: null, exit_code: null, failure_reason: null, verdict: null,
      }],
    }),
  }
})

test('renders real execution summary and run rows', async () => {
  render(<Execution onNavigate={vi.fn()} />)

  await waitFor(() => expect(screen.getByText('implementation')).toBeInTheDocument())
  expect(screen.getByText('AI Command Center')).toBeInTheDocument()
  expect(screen.getAllByText('RUNNING').length).toBeGreaterThan(0)
  expect(screen.getByText('Visible runs')).toBeInTheDocument()
})
