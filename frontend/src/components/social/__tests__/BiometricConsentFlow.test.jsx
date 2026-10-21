// Prepared by Twistor Holdings LLC.
//
// TW-652: behavior tests for the biometric consent flow (pattern 01;
// TW-654 Section 1.4, item 1). The load-bearing assertions are legal,
// not cosmetic: the grant stays disabled until the attestation is
// checked, the payload carries the exact vfy_consents fields, the
// decline captures nothing and never nags, the rejected state
// surfaces the human review path, and the band display fails closed:
// a missing or unrecognized band renders no band claim, never the
// 21+ title and chip (TW-652 review M1).
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import BiometricConsentFlow from '../consent/BiometricConsentFlow'
import SocBandChip from '../SocBandChip'

describe('BiometricConsentFlow: pre-capture consent (TW-652)', () => {
  it('opens on the explanation with the door-check framing', () => {
    render(<BiometricConsentFlow />)
    expect(
      screen.getByRole('heading', { name: 'The door check, before your first night out' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Checked at the door')).toBeInTheDocument()
    expect(screen.getByText('Step 1 of 4: Consent')).toBeInTheDocument()
    expect(screen.getByText(/face template: a mathematical map/)).toBeInTheDocument()
  })

  it('renders the full flow in Spanish at parity', () => {
    render(<BiometricConsentFlow locale="es" />)
    expect(
      screen.getByRole('heading', { name: 'El control de la puerta, antes de tu primera noche' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Verificado en la puerta')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continuar' }))
    expect(
      screen.getByRole('heading', { name: 'Qué conservamos y cuándo se destruye' }),
    ).toBeInTheDocument()
  })

  it('walks explain, schedule, grant in order', () => {
    render(<BiometricConsentFlow />)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(
      screen.getByRole('heading', { name: 'What we keep, and when it is destroyed' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/never kept longer than 72 hours/)).toBeInTheDocument()
    expect(screen.getByText(/destroyed 24 months after your last activity/)).toBeInTheDocument()
    expect(screen.getByText('Never sold')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(
      screen.getByRole('heading', { name: 'This consent stands on its own' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/separate from the Terms of Service/)).toBeInTheDocument()
  })

  it('keeps the grant disabled until the attestation is checked', () => {
    render(<BiometricConsentFlow initialPhase="grant" />)
    const agree = screen.getByRole('button', { name: 'I agree. Start the check' })
    expect(agree).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox'))
    expect(agree).toBeEnabled()
  })

  it('emits the exact vfy_consents payload on grant, then hands off to capture', () => {
    const onConsentGrant = vi.fn()
    render(<BiometricConsentFlow initialPhase="grant" onConsentGrant={onConsentGrant} />)
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: 'I agree. Start the check' }))
    expect(onConsentGrant).toHaveBeenCalledTimes(1)
    expect(onConsentGrant.mock.calls[0][0]).toMatchObject({
      consentType: 'biometric_capture',
      policyVersion: '1.0',
      retentionScheduleVersion: '1.0',
      locale: 'en',
    })
    expect(
      screen.getByRole('heading', { name: 'Now the check itself' }),
    ).toBeInTheDocument()
  })

  it('shows the version line the member is consenting under', () => {
    render(<BiometricConsentFlow initialPhase="grant" />)
    expect(screen.getByText(/policy version 1\.0/)).toBeInTheDocument()
    expect(screen.getByText(/Retention schedule version 1\.0/)).toBeInTheDocument()
  })

  it('declining captures nothing and lands on the dignified exit', () => {
    const onDecline = vi.fn()
    const onConsentGrant = vi.fn()
    render(<BiometricConsentFlow onDecline={onDecline} onConsentGrant={onConsentGrant} />)
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(onDecline).toHaveBeenCalledTimes(1)
    expect(onConsentGrant).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'No problem at all' })).toBeInTheDocument()
    expect(screen.getByText(/Nothing was captured/)).toBeInTheDocument()
    expect(screen.getByText(/we will not keep asking/)).toBeInTheDocument()
  })

  it('declining from the grant screen also captures nothing', () => {
    const onConsentGrant = vi.fn()
    render(<BiometricConsentFlow initialPhase="grant" onConsentGrant={onConsentGrant} />)
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(onConsentGrant).not.toHaveBeenCalled()
    expect(screen.getByRole('heading', { name: 'No problem at all' })).toBeInTheDocument()
  })
})

describe('BiometricConsentFlow: capture handoff and outcomes (TW-652)', () => {
  it('frames the capture and keeps the assisted human path visible', () => {
    const onAssistedReview = vi.fn()
    render(<BiometricConsentFlow phase="capture" onAssistedReview={onAssistedReview} />)
    expect(screen.getByText(/Retakes are free and unpenalized/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use human review instead' }))
    expect(onAssistedReview).toHaveBeenCalledTimes(1)
  })

  it('pending names the human reviewer and promises no invented timeline', () => {
    const { container } = render(<BiometricConsentFlow phase="pending" />)
    expect(
      screen.getByRole('heading', { name: 'A person is taking a look' }),
    ).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/\d+\s*(hours?|days?|minutes?)/)
  })

  it('approved states the band with the chip, never a birthdate', () => {
    render(<BiometricConsentFlow phase="approved" band="21+" />)
    expect(screen.getByRole('heading', { name: 'You are verified 21+' })).toBeInTheDocument()
    expect(screen.getByText('Checked at the door')).toBeInTheDocument()
    const chip = screen.getByRole('img', { name: 'Age band: 21 plus' })
    expect(chip).toHaveTextContent('21+')
  })

  it('approved renders the black chip for the 18 to 20 band', () => {
    render(<BiometricConsentFlow phase="approved" band="18-20" />)
    expect(screen.getByRole('heading', { name: 'You are verified 18+' })).toBeInTheDocument()
    const chip = screen.getByRole('img', { name: 'Age band: 18 to 20' })
    expect(chip).toHaveTextContent('18+')
    expect(chip.className).toContain('soc-chip--18')
  })

  it('approved renders the TEEN chip and the band-free title for the teen band', () => {
    render(<BiometricConsentFlow phase="approved" band="teen" />)
    expect(screen.getByRole('heading', { name: 'You are verified' })).toBeInTheDocument()
    const chip = screen.getByRole('img', { name: 'Teen account' })
    expect(chip).toHaveTextContent('TEEN')
    expect(chip.className).toContain('soc-chip--teen')
  })

  it('approved with no band renders no band claim (fail closed, M1)', () => {
    const { container } = render(<BiometricConsentFlow phase="approved" />)
    expect(screen.getByRole('heading', { name: 'You are verified' })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'You are verified 21+' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /Age band/ })).not.toBeInTheDocument()
    expect(container.textContent).not.toContain('21+')
  })

  it('approved with an unrecognized band renders no band claim (fail closed, M1)', () => {
    const { container } = render(<BiometricConsentFlow phase="approved" band="25-plus" />)
    expect(screen.getByRole('heading', { name: 'You are verified' })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'You are verified 21+' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /Age band/ })).not.toBeInTheDocument()
    expect(container.textContent).not.toContain('21+')
  })

  it('approved with an unrecognized band fails closed in Spanish too (M1)', () => {
    render(<BiometricConsentFlow locale="es" phase="approved" band="desconocida" />)
    expect(
      screen.getByRole('heading', { name: 'Verificación completa' }),
    ).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { name: 'Verificación completa: 21+' }),
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('img', { name: /Banda de edad/ })).not.toBeInTheDocument()
  })

  it('rejected surfaces retake and human review, and never blames the member', () => {
    const onRequestReview = vi.fn()
    const onRetake = vi.fn()
    const { container } = render(
      <BiometricConsentFlow phase="rejected" onRequestReview={onRequestReview} onRetake={onRetake} />,
    )
    expect(screen.getByText(/not a verdict on you/)).toBeInTheDocument()
    expect(container.textContent.toLowerCase()).not.toMatch(/fraud|fake/)
    fireEvent.click(screen.getByRole('button', { name: 'Request human review' }))
    expect(onRequestReview).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Retake the check' }))
    expect(onRetake).toHaveBeenCalledTimes(1)
  })

  it('dormant notice states the destruction and offers re-verification', () => {
    const onReverify = vi.fn()
    render(
      <BiometricConsentFlow phase="dormant" destroyedOn="March 4, 2028" onReverify={onReverify} />,
    )
    expect(
      screen.getByRole('heading', { name: 'Your verification has lapsed' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/destroyed on March 4, 2028/)).toBeInTheDocument()
    expect(screen.getByText(/24 months since your last activity/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Verify again' }))
    expect(onReverify).toHaveBeenCalledTimes(1)
  })
})

describe('SocBandChip: the chip states only a verified fact (TW-652 M1)', () => {
  it('renders the stated band for each known band', () => {
    const { rerender } = render(<SocBandChip band="21+" />)
    expect(screen.getByRole('img', { name: 'Age band: 21 plus' })).toHaveTextContent('21+')
    rerender(<SocBandChip band="18-20" />)
    expect(screen.getByRole('img', { name: 'Age band: 18 to 20' })).toHaveTextContent('18+')
    rerender(<SocBandChip band="teen" />)
    expect(screen.getByRole('img', { name: 'Teen account' })).toHaveTextContent('TEEN')
  })

  it('renders nothing for a missing band', () => {
    const { container } = render(<SocBandChip />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing for an unrecognized band, never the 21+ chip', () => {
    const { container } = render(<SocBandChip band="25-plus" />)
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole('img', { name: 'Age band: 21 plus' })).not.toBeInTheDocument()
  })
})
