// Prepared by Twistor Holdings LLC.
//
// TW-1322: a failed save in the CRUD modals used to be silent — the button
// flipped back from "Saving..." with no message and the user could not tell
// whether the record existed. These tests pin the fix: the backend's reason
// renders in an alert, and the typed entries survive the failure.
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { vi, describe, test, expect, beforeEach } from 'vitest'

vi.mock('../../lib/api', () => ({
  leadsApi: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    convert: vi.fn(),
    remove: vi.fn(),
  },
}))

import { leadsApi } from '../../lib/api'
import Leads from '../Leads'

function renderLeads() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Leads />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Leads modal save failure (TW-1322)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    leadsApi.list.mockResolvedValue({ data: [] })
  })

  test('a rejected create shows the backend detail and keeps the typed name', async () => {
    leadsApi.create.mockRejectedValue({
      response: { data: { detail: 'A lead with this phone already exists' } },
    })
    renderLeads()

    fireEvent.click(screen.getByRole('button', { name: '+ Add Lead' }))
    const nameLabel = await screen.findByText('Name *')
    const nameInput = nameLabel.closest('div').querySelector('input')
    fireEvent.change(nameInput, { target: { value: 'Jamie Rivera' } })

    fireEvent.click(screen.getByRole('button', { name: 'Add Lead' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A lead with this phone already exists')
    // The whole point: the entry is still here after the failure.
    expect(nameInput).toHaveValue('Jamie Rivera')
  })

  test('a rejected create without a backend detail shows the fallback copy', async () => {
    leadsApi.create.mockRejectedValue(new Error('network down'))
    renderLeads()

    fireEvent.click(screen.getByRole('button', { name: '+ Add Lead' }))
    const nameLabel = await screen.findByText('Name *')
    const nameInput = nameLabel.closest('div').querySelector('input')
    fireEvent.change(nameInput, { target: { value: 'Jamie Rivera' } })

    fireEvent.click(screen.getByRole('button', { name: 'Add Lead' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not save. Your entries are still here.')
    expect(nameInput).toHaveValue('Jamie Rivera')
  })
})
