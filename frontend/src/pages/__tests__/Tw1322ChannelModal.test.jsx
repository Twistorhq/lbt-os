// Prepared by Twistor Holdings LLC.
//
// TW-1322: NewChannelModal's submit catch was an empty block — a failed
// room create closed nothing, showed nothing, and the user could not tell
// whether the room existed. Pin the fix: the error renders in the modal
// and the typed name survives.
import { render, screen, fireEvent } from '@testing-library/react'
import { vi, describe, test, expect } from 'vitest'

import { NewChannelModal } from '../Messages'

describe('NewChannelModal failure (TW-1322)', () => {
  test('a rejected create shows the error and keeps the typed name', async () => {
    const onCreate = vi.fn().mockRejectedValue({
      response: { data: { detail: 'A room with that name already exists' } },
    })
    const onClose = vi.fn()
    render(<NewChannelModal onClose={onClose} onCreate={onCreate} />)

    const nameInput = screen.getByPlaceholderText('e.g. quote-room')
    fireEvent.change(nameInput, { target: { value: 'ops-room' } })
    fireEvent.click(screen.getByRole('button', { name: 'Open room' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('A room with that name already exists')
    expect(nameInput).toHaveValue('ops-room')
    expect(onClose).not.toHaveBeenCalled()
  })

  test('a successful create still closes the modal', async () => {
    const onCreate = vi.fn().mockResolvedValue({})
    const onClose = vi.fn()
    render(<NewChannelModal onClose={onClose} onCreate={onCreate} />)

    fireEvent.change(screen.getByPlaceholderText('e.g. quote-room'), {
      target: { value: 'ops-room' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Open room' }))

    await vi.waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
