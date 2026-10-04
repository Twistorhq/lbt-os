// Prepared by Twistor Holdings LLC.
import { render, screen, fireEvent } from '@testing-library/react'
import ClientTradeView from './ClientTradeView'

describe('ClientTradeView — client edition rules', () => {
  test('renders hero, client badge, and sample badge', () => {
    render(<ClientTradeView />)
    expect(screen.getByText(/See your market/)).toBeInTheDocument()
    expect(screen.getByText('Client edition')).toBeInTheDocument()
    expect(screen.getByText('Sample data')).toBeInTheDocument()
  })

  test('contains zero SQL or code anywhere on the surface', () => {
    const { container } = render(<ClientTradeView />)
    expect(container.textContent).not.toMatch(/SELECT/i)
    expect(container.textContent).not.toMatch(/\bSQL\b/)
    expect(container.textContent).not.toMatch(/sandbox/i)
  })

  test('talk-or-type answers a plain-language question', () => {
    render(<ClientTradeView />)
    const input = screen.getByPlaceholderText(/Ask in plain words/)
    fireEvent.change(input, { target: { value: 'How many prospects are nearby?' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByRole('status')).toHaveTextContent(/2 prospects/)
  })

  test('suggestion chips ask on click', () => {
    render(<ClientTradeView />)
    fireEvent.click(screen.getByText('What does the free report cover?'))
    expect(screen.getByRole('status')).toHaveTextContent(/free trade report/i)
  })

  test('one button opens the marketing report; close dismisses it', () => {
    render(<ClientTradeView />)
    fireEvent.click(screen.getByText('Get my free trade report'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Your market, at a glance')).toBeInTheDocument()
    expect(screen.getByText(/Where the money is walking out/)).toBeInTheDocument()
    // The report is marketing copy — no SQL leaks in the modal either.
    expect(screen.getByRole('dialog').textContent).not.toMatch(/SELECT/i)
    fireEvent.click(screen.getByText('Close', { selector: '.cv-modal-actions .tv-btn' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  test('SiteView tour opens, shows chapters, and closes', () => {
    render(<ClientTradeView />)
    fireEvent.click(screen.getByText(/Take the SiteView tour/))
    const dialog = screen.getByRole('dialog', { name: /SiteView tour/ })
    expect(dialog).toBeInTheDocument()
    expect(screen.getByText(/Before — the territory at dawn/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Next chapter'))
    expect(screen.getByText(/During — the crew on site/)).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('End tour'))
    expect(screen.queryByRole('dialog', { name: /SiteView tour/ })).not.toBeInTheDocument()
  })

  test('storm alert card renders with client-friendly copy', () => {
    render(<ClientTradeView />)
    expect(screen.getByText(/Storm opportunity alert/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Notify me about storm work'))
    expect(screen.getByRole('status')).toHaveTextContent(/demo only/i)
  })

  test('provenance footer is visible', () => {
    render(<ClientTradeView />)
    expect(screen.getByText(/fictional companies, not real businesses/)).toBeInTheDocument()
  })
})
