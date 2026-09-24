import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

/* The cinematic primitives are motion-tested elsewhere; here they render
 * children directly so tests assert narrative structure, not animation. */
vi.mock('../../motion', () => ({
  Reveal: ({ children }) => <>{children}</>,
  usePrefersReducedMotion: () => true, // final values render instantly
}))

vi.mock('../../../lib/api', () => ({
  leakApi: { brief: vi.fn(), detectors: vi.fn() },
}))

import { leakApi } from '../../../lib/api'
import MorningBrief from '../MorningBrief'
import BriefScene, { detectorLabel, SEVERITY_EDGE } from '../BriefScene'
import BriefHero from '../BriefHero'

const briefFixture = {
  org_id: 'org-1',
  vertical: 'hvac',
  generated_at: '2026-09-24T13:00:00Z',
  what_happened: [
    {
      ladder: 'happened',
      severity: 'watch',
      title: '14 installed systems past expected life',
      detail: 'These customers are running equipment past its expected lifespan.',
      count: 14,
      estimated_value: null,
      entities: [],
      recommended_action: 'Call the oldest units first.',
      detector: 'equipment-age-graveyard',
      vertical: 'hvac',
      is_demo: false,
    },
  ],
  what_will_happen: [],
  what_should_we_do: [
    {
      ladder: 'should',
      severity: 'urgent',
      title: '9 stalled quotes still winnable',
      detail: 'Quotes that died without follow-up.',
      count: 9,
      estimated_value: 45200,
      entities: [],
      recommended_action: 'Two-touch follow-up this week, highest value first.',
      detector: 'quote-resurrection',
      vertical: 'hvac',
      is_demo: true,
    },
  ],
  totals: { findings: 2, dollars_at_stake: 45200 },
  data_status: { insufficient: [] },
}

function renderWithClient(ui, briefData = briefFixture) {
  leakApi.brief.mockResolvedValue({ data: briefData })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>{ui}</QueryClientProvider>
    </MemoryRouter>
  )
}

describe('detectorLabel', () => {
  it('maps known slugs to human labels, never raw slugs', () => {
    expect(detectorLabel('equipment-age-graveyard')).toBe('Equipment age')
    expect(detectorLabel('plan-churn-risk')).toBe('Plan churn')
    expect(detectorLabel('quote-resurrection')).toBe('Quote follow-up')
  })
  it('humanizes unknown slugs instead of leaking them raw', () => {
    expect(detectorLabel('some-future-detector')).toBe('Some Future Detector')
  })
})

describe('BriefScene', () => {
  it('renders eyebrow, headline, subcopy, money line, and the move', () => {
    const f = briefFixture.what_should_we_do[0]
    render(<BriefScene finding={f} />)
    expect(screen.getByText('Quote follow-up')).toBeInTheDocument()
    expect(screen.getByText('9 stalled quotes still winnable')).toBeInTheDocument()
    expect(screen.getByText('Quotes that died without follow-up.')).toBeInTheDocument()
    expect(screen.getByText('$45,200')).toBeInTheDocument()
    expect(
      screen.getByText('Two-touch follow-up this week, highest value first.')
    ).toBeInTheDocument()
  })

  it('shows the SAMPLE DATA badge on demo findings', () => {
    const { rerender } = render(<BriefScene finding={briefFixture.what_should_we_do[0]} />)
    expect(screen.getByText('SAMPLE DATA')).toBeInTheDocument()
    rerender(<BriefScene finding={briefFixture.what_happened[0]} />)
    expect(screen.queryByText('SAMPLE DATA')).not.toBeInTheDocument()
  })

  it('maps severity to edge accents, never full surfaces', () => {
    render(<BriefScene finding={briefFixture.what_should_we_do[0]} />)
    const article = screen.getByLabelText('9 stalled quotes still winnable')
    expect(article.className).toContain(SEVERITY_EDGE.urgent)
    expect(article.className).toContain('bg-[#0d0d17]') // near-black surface holds
  })
})

describe('BriefHero', () => {
  it('opens on the voice line with counted totals (reduced motion = final values)', () => {
    render(<BriefHero findings={2} dollarsAtStake={45200} generatedAt="2026-09-24T13:00:00Z" />)
    expect(screen.getByText(/walked out the door/)).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('$45,200')).toBeInTheDocument()
  })
})

describe('MorningBrief', () => {
  it('renders the three acts in ladder order, skipping empty rungs', async () => {
    renderWithClient(<MorningBrief />)
    expect(await screen.findByText('Act I')).toBeInTheDocument()
    expect(screen.getByText('What Happened')).toBeInTheDocument()
    expect(screen.queryByText('Act II')).not.toBeInTheDocument()
    expect(screen.getByText('Act III')).toBeInTheDocument()
    expect(screen.getByText('What Should We Do')).toBeInTheDocument()
  })

  it('degrades honestly when there is nothing to report', async () => {
    const empty = {
      ...briefFixture,
      what_happened: [],
      what_should_we_do: [],
      totals: { findings: 0, dollars_at_stake: 0 },
      data_status: { insufficient: ['service_assets'] },
    }
    renderWithClient(<MorningBrief />, empty)
    expect(
      await screen.findByText(/Connect your data and the brief writes itself/)
    ).toBeInTheDocument()
    expect(screen.queryByText(/walked out the door/)).not.toBeInTheDocument()
  })
})
