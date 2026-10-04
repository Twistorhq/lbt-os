// Prepared by Twistor Holdings LLC.
//
// TW-301: live-backend behavior — signed-in users get real pins/dossiers/
// diagnostics from /api/v1/tradeview; any backend failure falls back to the
// clearly-labeled sample dataset (the map never renders empty).
import { render, screen, waitFor } from '@testing-library/react'
import { vi, beforeEach } from 'vitest'

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    tradeviewApi: {
      pins: vi.fn(),
      dossier: vi.fn(),
      diagnostics: vi.fn(),
      layers: vi.fn(),
      leaks: vi.fn(),
      actions: vi.fn(),
      route: vi.fn(),
    },
  }
})
vi.mock('../../lib/sampleMode', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, isSampleMode: vi.fn(() => false) }
})

import TradeView, { runSandboxQuery, LiveDossier, LiveDiagnosticsReport, LeakExecutiveSummary } from '../TradeView'
import { tradeviewApi } from '../../lib/api'
import { isSampleMode } from '../../lib/sampleMode'

const LIVE_PINS = [
  {
    id: 'c-1', kind: 'customer', name: "Aunt May's Heating",
    trade: 'HVAC', address: '123 Colfax Ave, Denver, CO',
    lat: 39.7, lng: -105.0, located: true,
  },
  {
    id: 'l-1', kind: 'lead', name: 'Cousin Ray',
    trade: null, address: null, lat: 39.71, lng: -105.01, located: true,
  },
]

describe('TW-301 live backend', () => {
  beforeEach(() => {
    vi.mocked(tradeviewApi.pins).mockResolvedValue({
      data: { source: 'live', pins: LIVE_PINS, unlocated_count: 1 },
    })
    vi.mocked(tradeviewApi.dossier).mockResolvedValue({
      data: {
        kind: 'customer', id: 'c-1', name: "Aunt May's Heating",
        facts: { phone: '303-555-0101' }, leak_findings: [],
        provenance: 'Live from your connected data — no sample records.',
      },
    })
    vi.mocked(tradeviewApi.diagnostics).mockResolvedValue({
      data: {
        entity_id: 'c-1', entity_name: "Aunt May's Heating",
        snapshot: 'HVAC · customer', leak_findings: [],
        recommended_next_step: 'Call them today.',
      },
    })
    vi.mocked(tradeviewApi.actions).mockResolvedValue({ data: { actions: [], count: 0 } })
    vi.mocked(tradeviewApi.route).mockResolvedValue({ data: { order: [], total_km: 0 } })
  })

  test('shows Live data badge and live pins when signed in', async () => {
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    expect(screen.getByText(/2 companies · 1 prospects · 1 clients/)).toBeInTheDocument()
    expect(screen.getByText(/1 record has no mappable address yet/)).toBeInTheDocument()
  })

  test('falls back to sample data when the backend is unreachable', () => {
    vi.mocked(tradeviewApi.pins).mockRejectedValueOnce(new Error('no backend'))
    render(<TradeView />)
    // Never renders empty — the sample experience holds.
    expect(screen.getByText('Sample data')).toBeInTheDocument()
    expect(screen.getByText(/3 companies · 2 prospects · 1 client/)).toBeInTheDocument()
  })

  test('sandbox accepts an explicit dataset', () => {
    const r = runSandboxQuery('SELECT name FROM companies', LIVE_PINS)
    expect(r.rows).toHaveLength(2)
    const all = runSandboxQuery('SELECT * FROM companies')
    expect(all.rows.length).toBeGreaterThan(0)
  })

  test('LiveDossier renders facts and honest empty findings', () => {
    render(
      <LiveDossier
        dossier={{
          facts: { phone: '303-555-0101' },
          leak_findings: [],
          provenance: 'Live from your connected data.',
        }}
      />
    )
    expect(screen.getByText('303-555-0101')).toBeInTheDocument()
    expect(screen.getByText(/No leak findings for this record yet/)).toBeInTheDocument()
  })

  test('LiveDiagnosticsReport labels itself live with a next step', () => {
    render(
      <LiveDiagnosticsReport
        data={{
          entity_name: "Aunt May's Heating",
          snapshot: 'HVAC · customer',
          leak_findings: [],
          recommended_next_step: 'Call them today.',
        }}
        onClose={() => {}}
      />
    )
    expect(screen.getByText(/LIVE report/)).toBeInTheDocument()
    expect(screen.getByText(/Call them today/)).toBeInTheDocument()
  })
})

describe('TW-301 Rosa fix round: honest failure states', () => {
  beforeEach(() => {
    vi.mocked(tradeviewApi.pins).mockResolvedValue({
      data: { source: 'live', pins: LIVE_PINS, unlocated_count: 1, records_total: 3 },
    })
  })

  test('LiveDossier shows an error note instead of loading forever', () => {
    render(<LiveDossier dossier={null} error />)
    expect(screen.getByText(/Couldn't load the live dossier/)).toBeInTheDocument()
    expect(screen.queryByText(/Loading live dossier/)).not.toBeInTheDocument()
  })

  test('LiveDiagnosticsReport shows an error note instead of loading forever', () => {
    render(<LiveDiagnosticsReport data={null} error onClose={() => {}} />)
    expect(screen.getByText(/Couldn't load the live diagnostics/)).toBeInTheDocument()
    expect(screen.queryByText(/Loading diagnostics/)).not.toBeInTheDocument()
  })

  test('genuinely empty book gets an honest note, not a bare map', async () => {
    vi.mocked(tradeviewApi.pins).mockResolvedValueOnce({
      data: { source: 'live', pins: [], unlocated_count: 0, records_total: 0 },
    })
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    expect(screen.getByText(/Your book is empty/)).toBeInTheDocument()
  })

  test('non-empty 200 keeps the live map with no empty-book note', async () => {
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    expect(screen.queryByText(/Your book is empty/)).not.toBeInTheDocument()
  })
})

const LEAK_DATA = {
  source: 'live',
  headline: '$13,000 left on the table',
  totals: { findings: 1, dollars_at_stake: 13000.0, located: 1, unlocated: 1, partial: false },
  leaks: [
    {
      id: 'quote-resurrection:q-1', entity_id: 'q-1', entity_name: 'Acme Heating',
      detector: 'quote-resurrection', title: '2 stalled quotes still winnable',
      severity: 'urgent', dollars: 8500.0, days_idle: 21,
      recommended_action: 'Two-touch follow-up this week.',
      lat: 39.7, lng: -105.0, located: true,
    },
    {
      id: 'quote-resurrection:q-2', entity_id: 'q-2', entity_name: 'Beta Corp',
      detector: 'quote-resurrection', title: '2 stalled quotes still winnable',
      severity: 'watch', dollars: 4500.0, days_idle: 16,
      recommended_action: 'Two-touch follow-up this week.',
      lat: 39.71, lng: -105.01, located: true,
    },
  ],
}

describe('TW-303 Leak Map', () => {
  test('toggling the layer fetches leaks and shows the headline banner', async () => {
    vi.mocked(tradeviewApi.leaks).mockResolvedValue({ data: LEAK_DATA })
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    const toggle = screen.getByLabelText(/Leak Map — money on the table/)
    expect(toggle).not.toBeDisabled()
    toggle.click()
    await waitFor(() => expect(screen.getAllByText('$13,000 left on the table').length).toBeGreaterThan(0))
    expect(screen.getByText('Money walking out the door')).toBeInTheDocument()
    // Executive summary renders the top leaks in plain language.
    expect(screen.getByText('Money on the table')).toBeInTheDocument()
    expect(screen.getByText('Acme Heating')).toBeInTheDocument()
  })

  test('leak layer is disabled in sample mode', () => {
    vi.mocked(isSampleMode).mockReturnValueOnce(true)
    render(<TradeView />)
    expect(screen.getByLabelText(/Leak Map — money on the table/)).toBeDisabled()
  })

  test('LeakExecutiveSummary is client-safe: no SQL, no code', () => {
    render(<LeakExecutiveSummary data={LEAK_DATA} />)
    const text = document.body.textContent
    expect(text).toMatch(/\$13,000 left on the table/)
    expect(text).toMatch(/Acme Heating/)
    expect(text).not.toMatch(/SELECT/i)
  })

  test('LeakExecutiveSummary renders nothing without data', () => {
    const { container } = render(<LeakExecutiveSummary data={null} />)
    expect(container.textContent).toBe('')
  })
})

const ACTIONS = [
  {
    id: 'action:quote-resurrection:q-1', rank: 1, detector: 'quote-resurrection',
    title: '2 stalled quotes still winnable', entity_id: 'q-1', entity_name: 'Acme Heating',
    dollars: 8500.0, recoverability: 0.35, weight_basis: 'heuristic prior',
    expected_recovery: 2975.0, next_move: 'Two-touch follow-up this week.',
    located: true, lat: 39.7, lng: -105.0,
  },
  {
    id: 'action:plan-churn-risk:p-1', rank: 2, detector: 'plan-churn-risk',
    title: '1 plan at churn risk', entity_id: 'p-1', entity_name: 'Beta Corp',
    dollars: 2400.0, recoverability: 0.5, weight_basis: 'heuristic prior',
    expected_recovery: 1200.0, next_move: 'Call before the card fails again.',
    located: false, lat: null, lng: null,
  },
]

describe('TW-306 What Should We Do', () => {
  test('ranked action queue renders with expected recovery and next move', async () => {
    vi.mocked(tradeviewApi.leaks).mockResolvedValue({ data: LEAK_DATA })
    vi.mocked(tradeviewApi.actions).mockResolvedValue({ data: { actions: ACTIONS, count: 2 } })
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    screen.getByLabelText(/Leak Map — money on the table/).click()
    await waitFor(() => expect(screen.getByText('What should we do')).toBeInTheDocument())
    // Ranked: Acme ($2,975 expected) above Beta ($1,200).
    const items = screen.getAllByRole('listitem')
    expect(items[0].textContent).toMatch(/Acme Heating/)
    expect(screen.getByText(/\$2,975 expected/)).toBeInTheDocument()
    expect(screen.getByText(/Two-touch follow-up this week/)).toBeInTheDocument()
    // Weights are labeled as priors, not measured rates.
    expect(screen.getByText(/heuristic priors/)).toBeInTheDocument()
  })

  test('optimize route calls the API with located leak ids', async () => {
    vi.mocked(tradeviewApi.leaks).mockResolvedValue({ data: LEAK_DATA })
    vi.mocked(tradeviewApi.actions).mockResolvedValue({ data: { actions: ACTIONS, count: 2 } })
    vi.mocked(tradeviewApi.route).mockResolvedValue({ data: { order: ['q-1', 'q-2'], total_km: 1.2 } })
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    screen.getByLabelText(/Leak Map — money on the table/).click()
    await waitFor(() => expect(screen.getByText('Optimize route')).toBeInTheDocument())
    screen.getByText('Optimize route').click()
    await waitFor(() =>
      expect(tradeviewApi.route).toHaveBeenCalledWith(['q-1', 'q-2'], null)
    )
  })

  test('actions fetch failure renders an honest note, not a false all-clear', async () => {
    vi.mocked(tradeviewApi.leaks).mockResolvedValue({ data: LEAK_DATA })
    vi.mocked(tradeviewApi.actions).mockRejectedValue(new Error('backend down'))
    render(<TradeView />)
    await waitFor(() => expect(screen.getByText('Live data')).toBeInTheDocument())
    screen.getByLabelText(/Leak Map — money on the table/).click()
    // Same honesty class as TW-301 MINOR 1: a transport failure with leak
    // pins visible must never read as "nothing to do".
    await waitFor(() => expect(screen.getByText(/Couldn't load actions/)).toBeInTheDocument())
    expect(screen.queryByText(/Nothing to do — no open leaks/)).not.toBeInTheDocument()
  })
})
