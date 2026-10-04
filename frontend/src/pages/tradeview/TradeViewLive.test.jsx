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
    },
  }
})
vi.mock('../../lib/sampleMode', async (importOriginal) => {
  const actual = await importOriginal()
  return { ...actual, isSampleMode: vi.fn(() => false) }
})

import TradeView, { runSandboxQuery, LiveDossier, LiveDiagnosticsReport } from '../TradeView'
import { tradeviewApi } from '../../lib/api'

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
