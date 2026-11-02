/**
 * TW-899 — mobile shell regression tests.
 *
 * The demo workspace used to render the desktop sidebar at phone widths:
 * a fixed w-64 column crushed main content into a narrow strip. These
 * tests pin the fixed behavior: the sidebar is a closed off-canvas drawer
 * by default on mobile classes, the top-bar toggle opens it, and the
 * sample banner still renders for signed-out visitors.
 *
 * Prepared by Twistor Holdings LLC.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

vi.mock('@clerk/clerk-react', () => ({
  useAuth: () => ({ isLoaded: true, isSignedIn: false }),
  useOrganization: () => ({ organization: null }),
  UserButton: () => null,
}))

vi.mock('../../lib/api', () => ({
  orgApi: {
    workspaceStatus: vi.fn(() => Promise.resolve({ data: { workspace_mode: 'demo' } })),
  },
}))

import Layout from './Layout'

function renderShell() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/app/customers']}>
        <Routes>
          <Route path="/app" element={<Layout />}>
            <Route path="customers" element={<div>Customers page body</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('TW-899 mobile app shell', () => {
  it('renders page content and the sample banner for signed-out visitors', async () => {
    renderShell()
    expect(await screen.findByText('Customers page body')).toBeInTheDocument()
    expect(screen.getByText(/Sample data/)).toBeInTheDocument()
  })

  it('starts with the sidebar drawer closed (off-canvas classes)', () => {
    renderShell()
    const toggle = screen.getByRole('button', { name: 'Open navigation' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveAttribute('aria-controls', 'app-sidebar')
    const sidebar = document.getElementById('app-sidebar')
    expect(sidebar.className).toContain('-translate-x-full')
    expect(sidebar.className).toContain('invisible')
    // Desktop keeps the sticky column: md overrides stay in place.
    expect(sidebar.className).toContain('md:translate-x-0')
    expect(sidebar.className).toContain('md:visible')
  })

  it('opens the drawer from the top-bar toggle and closes it again', () => {
    renderShell()
    const toggle = screen.getByRole('button', { name: 'Open navigation' })
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const sidebar = document.getElementById('app-sidebar')
    expect(sidebar.className).toContain('translate-x-0')
    expect(sidebar.className).not.toContain('-translate-x-full')

    fireEvent.click(screen.getByRole('button', { name: 'Close navigation' }))
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(document.getElementById('app-sidebar').className).toContain('-translate-x-full')
  })
})
