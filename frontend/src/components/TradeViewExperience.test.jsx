import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TradeViewExperience from './TradeViewExperience'

test('Lite needs no network and lets visitors filter, progress and reset a sample', () => {
  const load = vi.fn()
  render(<TradeViewExperience loadConnected={load} />)
  expect(load).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('Trade'), { target: { value: 'HVAC' } })
  expect(screen.getByRole('heading', { name: 'Uneven heating' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Try: Manager approves' }))
  expect(screen.getByRole('status')).toHaveTextContent('Manager approves')
  fireEvent.click(screen.getByRole('button', { name: 'Reset sample' }))
  expect(screen.getByRole('status')).toHaveTextContent('Request received')
  expect(screen.getByLabelText('Trade')).toHaveValue('All trades')
})

test('map pins support keyboard selection and layer toggle', () => {
  render(<TradeViewExperience />)
  fireEvent.keyDown(screen.getByRole('button', { name: 'Open Cedar House sample request' }), { key: 'Enter' })
  expect(screen.getByRole('heading', { name: 'Routine lighting inspection' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Hide request pins' }))
  expect(screen.queryByRole('button', { name: 'Open Cedar House sample request' })).not.toBeInTheDocument()
})

test.each([403, 502])('Pro failure %s never substitutes fictional data', async (status) => {
  const load = vi.fn().mockRejectedValue({ response: { status } })
  render(<TradeViewExperience loadConnected={load} />)
  fireEvent.click(screen.getByRole('button', { name: 'Pro · Connected data' }))
  await screen.findByRole('button', { name: 'Retry access check' })
  expect(screen.queryByText('Juniper Hall')).not.toBeInTheDocument()
  expect(load).toHaveBeenCalledTimes(1)
})

test('Pro renders organization records and rejects non-live payloads', async () => {
  const load = vi.fn().mockResolvedValue({ data: { source: 'live', pins: [{ id: '1', kind: 'customer', name: 'Authorized property' }], unlocated_count: 2 } })
  render(<TradeViewExperience loadConnected={load} />)
  fireEvent.click(screen.getByRole('button', { name: 'Pro · Connected data' }))
  await screen.findByText('Authorized property')
  expect(screen.getByRole('status')).toHaveTextContent('2 records without')
  fireEvent.click(screen.getByRole('button', { name: 'Lite · Free sample' }))
  load.mockResolvedValue({ data: { source: 'sample', pins: [] } })
  fireEvent.click(screen.getByRole('button', { name: 'Pro · Connected data' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('unavailable'))
  expect(screen.queryByText('Authorized property')).not.toBeInTheDocument()
})

test('offline Pro has no fake checkout or network activity', () => {
  render(<TradeViewExperience />)
  fireEvent.click(screen.getByRole('button', { name: 'Pro · Connected data' }))
  expect(screen.getByRole('status')).toHaveTextContent('not activated')
  fireEvent.click(screen.getByRole('button', { name: 'Explore the free sample' }))
  expect(screen.getByRole('heading', { name: 'Trade View Lite' })).toBeInTheDocument()
})
