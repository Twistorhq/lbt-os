// Prepared by Twistor Holdings LLC.
import { render, screen, fireEvent } from '@testing-library/react'
import TradeView, { runSandboxQuery, DiagnosticsReport, InsightPanels } from '../TradeView'
import { safeHttpsUrl } from './urlSafe'
import { SAMPLE_COMPANIES } from './tradeviewSample'

describe('TradeView page', () => {
  test('renders boot sequence, badges, and honest counts', () => {
    render(<TradeView />)
    expect(screen.getByText('TRADE VIEW', { selector: '.tv-boot-title' })).toBeInTheDocument()
    expect(screen.getByText('Internal')).toBeInTheDocument()
    expect(screen.getByText('Sample data')).toBeInTheDocument()
    expect(screen.getByText(/3 companies · 2 prospects · 1 client/)).toBeInTheDocument()
  })

  test('renders map region with accessible label (map init skipped in test env)', () => {
    render(<TradeView />)
    expect(screen.getByRole('application', { name: 'Twistor Trade View trade map' })).toBeInTheDocument()
  })

  test('dossier shows honest empty state before a pin is selected', () => {
    render(<TradeView />)
    expect(screen.getByText(/Select a pin to open the dossier/)).toBeInTheDocument()
  })

  test('insight layers carry honest not-assessed notes', () => {
    render(<TradeView />)
    // Disabled layer toggles say why they're unavailable…
    expect(screen.getByText(/NWS storm alerts \(live\)/)).toBeInTheDocument()
    expect(screen.getAllByTitle(/Not assessed in sample data/)).toHaveLength(3)
    // …and the dossier insight panels show honest empty states.
    const { container } = render(<InsightPanels />)
    expect(container.textContent).toMatch(/Not assessed yet — add data/g)
    expect((container.textContent.match(/Not assessed yet — add data/g) || [])).toHaveLength(4)
  })

  test('sample provenance is visible on the page', () => {
    render(<TradeView />)
    expect(screen.getByText(/not real businesses/)).toBeInTheDocument()
  })

  test('kind filter changes the counts', () => {
    render(<TradeView />)
    fireEvent.click(screen.getByLabelText('Prospects'))
    expect(screen.getByText(/1 companies · 0 prospects · 1 client/)).toBeInTheDocument()
  })
})

describe('safeHttpsUrl', () => {
  test('allows https URLs', () => {
    expect(safeHttpsUrl('https://www.sos.colorado.gov')).toBe('https://www.sos.colorado.gov')
  })
  test('rejects javascript:, http:, data:, relative, and non-strings', () => {
    expect(safeHttpsUrl("javascript:alert('x')")).toBeNull()
    expect(safeHttpsUrl('http://example.com')).toBeNull()
    expect(safeHttpsUrl('data:text/html,<h1>x</h1>')).toBeNull()
    expect(safeHttpsUrl('/relative/path')).toBeNull()
    expect(safeHttpsUrl(null)).toBeNull()
    expect(safeHttpsUrl(42)).toBeNull()
  })
})

describe('runSandboxQuery', () => {
  test('runs SELECT * over the sample dataset', () => {
    const r = runSandboxQuery('SELECT * FROM companies')
    expect(r.error).toBeUndefined()
    expect(r.columns).toHaveLength(8)
    expect(r.rows).toHaveLength(3)
  })

  test('supports WHERE trade and LIMIT', () => {
    const r = runSandboxQuery("SELECT name, kind FROM companies WHERE trade = 'HVAC' LIMIT 1")
    expect(r.error).toBeUndefined()
    expect(r.rows).toHaveLength(1)
    expect(r.rows[0][0]).toBe('Mile High Air Pros')
  })

  test('supports name LIKE', () => {
    const r = runSandboxQuery("SELECT name FROM companies WHERE name LIKE '%denver%'")
    expect(r.error).toBeUndefined()
    expect(r.rows).toHaveLength(1)
  })

  test('rejects non-SELECT, semicolons, unknown columns, and bad shapes', () => {
    expect(runSandboxQuery('DELETE FROM companies').error).toMatch(/only SELECT/i)
    expect(runSandboxQuery('DROP TABLE companies').error).toMatch(/only SELECT/i)
    expect(runSandboxQuery('SELECT * FROM companies;').error).toMatch(/semicolon/i)
    expect(runSandboxQuery('SELECT ssn FROM companies').error).toMatch(/Unknown column/)
    expect(runSandboxQuery('SELECT name companies').error).toMatch(/Supported shape/)
    expect(runSandboxQuery('').error).toBeTruthy()
  })
})

describe('DiagnosticsReport', () => {
  test('labels itself as a sample report with provenance', () => {
    render(<DiagnosticsReport company={SAMPLE_COMPANIES[0]} onClose={() => {}} />)
    expect(screen.getByText(/SAMPLE report/)).toBeInTheDocument()
    expect(screen.getByText(/Provenance:/)).toBeInTheDocument()
    expect(screen.getByText(/Pitch angle:/)).toBeInTheDocument()
  })
})
