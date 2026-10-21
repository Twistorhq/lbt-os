import { useState } from 'react'
import {
  getStrings,
  BIOMETRIC_POLICY_VERSION,
  RETENTION_SCHEDULE_VERSION,
} from '../strings'
import {
  ExplainScreen,
  ScheduleScreen,
  GrantScreen,
  DeclinedScreen,
} from './ConsentScreens'
import {
  CaptureHandoff,
  PendingState,
  ApprovedState,
  RejectedState,
  DormantState,
} from './VerificationStates'

/**
 * BiometricConsentFlow (TW-652; pattern 01; TW-654 Section 1.4).
 *
 * The consent-first verification entry for Twistor Social. Phases:
 *
 *   explain   what is captured and why (door-check framing)
 *   schedule  the retention and destruction schedule, plain language
 *   grant     the standalone, versioned, unbundled consent act
 *   declined  nothing captured; the member returns to the ghost town
 *   capture   handoff frame into the engine's guided capture surface
 *   pending   routed to human review
 *   approved  passed; the band is stated, with the band chip
 *   rejected  not passed; retake guidance and human review both named
 *   dormant   tier dropped on the 24-month dormancy rule, with notice
 *
 * The pre-capture phases navigate internally. The grant emits the
 * exact vfy_consents payload (consentType biometric_capture,
 * policyVersion, retentionScheduleVersion, locale, grantedAt) through
 * onConsentGrant, then hands off to capture. Post-capture phases are
 * driven by the caller through the phase prop once the engine owns
 * the session; every exit is a callback, and this component never
 * invents a backend call.
 *
 * The band prop carries the verified band from the engine verdict and
 * deliberately has no default: an absent or unrecognized band renders
 * no band claim on the approved screen (fail closed, TW-652 review
 * M1).
 *
 * Prepared by Twistor Holdings LLC
 */
export const CONSENT_PHASES = [
  'explain',
  'schedule',
  'grant',
  'declined',
  'capture',
  'pending',
  'approved',
  'rejected',
  'dormant',
]

export default function BiometricConsentFlow({
  locale = 'en',
  band = null,
  destroyedOn = null,
  policyVersion = BIOMETRIC_POLICY_VERSION,
  retentionVersion = RETENTION_SCHEDULE_VERSION,
  phase: phaseProp,
  initialPhase = 'explain',
  onConsentGrant,
  onDecline,
  onBrowse,
  onPolicy,
  onStartCapture,
  onAssistedReview,
  onRetake,
  onRequestReview,
  onReverify,
  onStart,
}) {
  const [internalPhase, setInternalPhase] = useState(initialPhase)
  const phase = phaseProp !== undefined ? phaseProp : internalPhase
  const s = getStrings(locale)

  const go = (next) => {
    if (phaseProp === undefined) setInternalPhase(next)
  }

  const handleDecline = () => {
    if (onDecline) onDecline()
    go('declined')
  }

  const handleGrant = () => {
    if (onConsentGrant) {
      onConsentGrant({
        consentType: 'biometric_capture',
        policyVersion,
        retentionScheduleVersion: retentionVersion,
        locale,
        grantedAt: new Date().toISOString(),
      })
    }
    go('capture')
  }

  switch (phase) {
    case 'schedule':
      return (
        <ScheduleScreen
          s={s}
          onContinue={() => go('grant')}
          onDecline={handleDecline}
          onPolicy={onPolicy}
        />
      )
    case 'grant':
      return (
        <GrantScreen
          s={s}
          policyVersion={policyVersion}
          retentionVersion={retentionVersion}
          onGrant={handleGrant}
          onDecline={handleDecline}
        />
      )
    case 'declined':
      return <DeclinedScreen s={s} onBrowse={onBrowse} />
    case 'capture':
      return (
        <CaptureHandoff
          s={s}
          onStartCapture={onStartCapture}
          onAssistedReview={onAssistedReview}
          onDecline={handleDecline}
        />
      )
    case 'pending':
      return <PendingState s={s} onBrowse={onBrowse} />
    case 'approved':
      return <ApprovedState s={s} locale={locale} band={band} onStart={onStart} />
    case 'rejected':
      return (
        <RejectedState s={s} onRetake={onRetake} onRequestReview={onRequestReview} />
      )
    case 'dormant':
      return (
        <DormantState
          s={s}
          destroyedOn={destroyedOn}
          onReverify={onReverify}
          onDecline={onBrowse}
        />
      )
    case 'explain':
    default:
      return (
        <ExplainScreen
          s={s}
          onContinue={() => go('schedule')}
          onDecline={handleDecline}
        />
      )
  }
}
