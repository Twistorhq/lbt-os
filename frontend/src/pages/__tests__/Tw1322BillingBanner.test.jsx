// Prepared by Twistor Holdings LLC.
//
// TW-1322: the Stripe return banner used to declare "Payment received!"
// whenever verification errored or only the ?upgraded= redirect param was
// present — a success claim with no backend confirmation behind it. These
// tests pin the honest states: success copy only when the checkout-session
// check succeeds; a neutral "still confirming" banner otherwise.
import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../../lib/api', () => ({
  orgApi: { getMe: vi.fn() },
  billingApi: {
    checkoutStatus: vi.fn(),
    checkout: vi.fn(),
    portal: vi.fn(),
  },
}))

import { orgApi, billingApi } from '../../lib/api'
import Billing from '../Billing'

function renderBilling(entry) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Billing />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Billing checkout-return banner (TW-1322)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    orgApi.getMe.mockResolvedValue({ data: { plan: 'free', name: 'Test Co' } })
  })

  test('redirect param alone (no session to verify) never claims payment', async () => {
    renderBilling('/app/billing?upgraded=trades_pro')

    expect(await screen.findByText('Activating your plan…')).toBeInTheDocument()
    expect(screen.getByText(/Stripe has not confirmed yet/)).toBeInTheDocument()
    expect(screen.queryByText(/Payment received/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Welcome to/)).not.toBeInTheDocument()
    expect(screen.queryByText(/payment went through/i)).not.toBeInTheDocument()
  })

  test('verified session shows the welcome state', async () => {
    billingApi.checkoutStatus.mockResolvedValue({
      data: { status: 'complete', plan: 'trades_pro' },
    })
    renderBilling('/app/billing?upgraded=trades_pro&session_id=cs_test_123')

    expect(await screen.findByText('Welcome to Trades Pro!')).toBeInTheDocument()
    expect(screen.getByText(/Verified with Stripe/)).toBeInTheDocument()
  })

  test('failed verification never claims payment', async () => {
    billingApi.checkoutStatus.mockRejectedValue(new Error('stripe unreachable'))
    renderBilling('/app/billing?upgraded=trades_pro&session_id=cs_test_123')

    // The query retries twice with a 2.5s delay before it settles to error.
    expect(
      await screen.findByText(/Stripe has not confirmed yet/, {}, { timeout: 15000 }),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Payment received/)).not.toBeInTheDocument()
    expect(screen.queryByText(/payment went through/i)).not.toBeInTheDocument()
  }, 25000)
})
