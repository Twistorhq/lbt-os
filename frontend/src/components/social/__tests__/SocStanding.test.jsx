// Prepared by Twistor Holdings LLC.
//
// TW-941 (display half): behavior tests for the member-profile
// standing display (pattern 06). The load-bearing assertions are the
// display rules from the brief and the catalog: Verified is a
// future-phase rung only and never renders as earned, activity
// rungs show real progress facts, a suspended band hides the tier
// everywhere, the show rate waits for 5 decided RSVPs, teen accounts
// cap at Pillar, and no commerce affordance exists on the surface.
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import SocStanding from '../SocStanding'
import SocStandingScreen from '../SocStandingScreen'
import { FIXTURE_STANDING, normalizeStanding } from '../../../lib/socialStanding'
import {
  createFixtureStandingClient,
  fetchMyStanding,
} from '../../../lib/socialStandingClient'

const renderFixture = (key, locale = 'en') =>
  render(<SocStanding standing={normalizeStanding(FIXTURE_STANDING[key])} locale={locale} />)

describe('SocStanding (TW-941, pattern 06)', () => {
  it('shows a climbing Regular their tier, show rate, and Fixture progress facts', () => {
    const { container } = renderFixture('regularClimbing')
    expect(screen.getByRole('heading', { name: 'Regular' })).toBeInTheDocument()
    expect(screen.getByText('Shows up: 92 percent')).toBeInTheDocument()
    expect(screen.getByText('Next: Fixture')).toBeInTheDocument()
    const nights = container.querySelector('[data-condition="nights"]')
    expect(nights.textContent).toContain('12 of 20')
    expect(nights.textContent).toContain('8 to go')
    const show = container.querySelector('[data-condition="showRate"]')
    expect(show.dataset.met).toBe('true')
    expect(show.textContent).toContain('Done')
    const corroboration = container.querySelector('[data-condition="corroboration"]')
    expect(corroboration.dataset.met).toBe('false')
    expect(corroboration.textContent).toContain('2 of 4')
    expect(corroboration.textContent).toContain('1 of 3')
    // No percentage bar anywhere in the pattern (pattern 06).
    expect(container.querySelector('progress, [role="progressbar"]')).toBeNull()
  })

  it('renders Verified only as a future-phase rung, never as earned or current', () => {
    const { container } = renderFixture('newMember')
    const verified = container.querySelector('[data-rung="verified"]')
    expect(verified.dataset.state).toBe('future')
    expect(verified.textContent).toContain('Coming in a later phase')
    expect(verified.textContent).toContain('not a tier you can earn today')
    for (const rung of container.querySelectorAll('[data-rung]')) {
      if (rung.dataset.rung === 'verified') continue
      expect(rung.textContent).not.toContain('Verified')
    }
    // A member with no activity tier is never titled Verified.
    expect(screen.queryByRole('heading', { name: 'Verified' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'No standing tier yet' })).toBeInTheDocument()
    expect(screen.getByText('Next: Regular')).toBeInTheDocument()
  })

  it('never marks Verified earned even for a Keyholder', () => {
    const { container } = renderFixture('keyholder')
    expect(container.querySelector('[data-rung="verified"]').dataset.state).toBe('future')
    expect(container.querySelector('[data-rung="keyholder"]').dataset.state).toBe('current')
    expect(container.querySelector('[data-rung="regular"]').dataset.state).toBe('earned')
    expect(screen.getByText(/top of the ladder/)).toBeInTheDocument()
  })

  it('hides the show rate below 5 decided RSVPs and shows the building-record line', () => {
    renderFixture('newMember')
    expect(screen.getAllByText('New: still building a record').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Shows up:/)).toBeNull()
  })

  it('hides tier, ladder, and progress entirely while the band is suspended', () => {
    const { container } = renderFixture('suspendedFixture')
    expect(screen.getByText('Your standing is paused')).toBeInTheDocument()
    expect(screen.queryByText('Fixture')).toBeNull()
    expect(container.querySelector('[data-rung]')).toBeNull()
    expect(container.querySelector('[data-condition]')).toBeNull()
    // The member still sees their own record, and why it moved.
    expect(screen.getByText('26 completed nights out')).toBeInTheDocument()
    expect(screen.getByText(/moved because of conduct/)).toBeInTheDocument()
  })

  it('caps the ladder at Pillar for teen accounts with no Keyholder rung', () => {
    const { container } = render(
      <SocStanding
        standing={normalizeStanding({
          ...FIXTURE_STANDING.regularClimbing,
          age_band: 'teen',
        })}
      />,
    )
    expect(container.querySelector('[data-rung="keyholder"]')).toBeNull()
    expect(screen.getAllByText(/Pillar is the top of the ladder/).length).toBeGreaterThan(0)
  })

  it('contains no purchase, boost, gift, or transfer affordance', () => {
    const { container } = renderFixture('regularClimbing')
    expect(container.textContent).not.toMatch(/buy|boost|gift|transfer|checkout/i)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders nothing for a null standing instead of inventing one', () => {
    const { container } = render(<SocStanding standing={null} />)
    expect(container.textContent).toBe('')
  })
})

describe('SocStandingScreen (TW-941)', () => {
  it('loads standing through the injected fetcher and renders it', async () => {
    render(
      <SocStandingScreen
        fetchStanding={() => fetchMyStanding(createFixtureStandingClient('regularClimbing'))}
      />,
    )
    expect(await screen.findByRole('heading', { name: 'Regular' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your standing' })).toBeInTheDocument()
  })

  it('shows the error state with a working retry on a failed load', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(normalizeStanding(FIXTURE_STANDING.keyholder))
    render(<SocStandingScreen fetchStanding={fetcher} />)
    expect(await screen.findByText('We could not load your standing')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('heading', { name: 'Keyholder' })).toBeInTheDocument()
  })
})
