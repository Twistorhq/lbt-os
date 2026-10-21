import SocScreen from '../SocScreen'
import SocActionBar from '../SocActionBar'
import SocBandChip from '../SocBandChip'
import { fill } from '../strings'
import '../social.css'

/**
 * The post-consent verification states (TW-652; pattern 01, steps 2
 * to 5 and the verdict renderings; TW-646 Sections 5.2, 5.7, 6.7).
 *
 * Honesty rules from the spec, held in every state: the copy never
 * claims the check is perfect, never uses the words fraud or fake
 * about the member, states the band and never a birthdate, and always
 * names the human remedy. A refusal on a banned identity anchor
 * renders through the same RejectedState as any other not-passed
 * result (TW-646 Section 6.2): generic, with no signal about which
 * check matched.
 *
 * The capture screen is a handoff frame only: the guided document and
 * liveness capture itself is engine-surface work (TW-646 Section 5.2
 * stages 2 to 4). This frame carries the consent context into it and
 * the exits out of it, including the accessible alternative: a member
 * who cannot perform the live challenge routes to human review with
 * assisted capture, never to a fail (pattern 01, launch requirement).
 *
 * Prepared by Twistor Holdings LLC
 */

function CheckIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M8.5 12.2l2.4 2.4 4.6-5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function ClockIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 7.5V12l3 2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

export function CaptureHandoff({ s, onStartCapture, onAssistedReview, onDecline }) {
  const c = s.capture
  return (
    <>
      <SocScreen title={c.title}>
        <p className="soc-lede">{c.body}</p>
        <p className="soc-body">{c.guidance}</p>
        <p className="soc-body soc-body--dim">{c.assistedNote}</p>
        <p>
          <button type="button" className="soc-link soc-btn" onClick={onAssistedReview}>
            {c.assisted}
          </button>
        </p>
      </SocScreen>
      <SocActionBar
        primaryLabel={c.start}
        onPrimary={onStartCapture}
        secondaryLabel={s.common.notNow}
        onSecondary={onDecline}
      />
    </>
  )
}

export function PendingState({ s, onBrowse }) {
  const p = s.pending
  return (
    <>
      <SocScreen title={p.title}>
        <div className="soc-banner soc-banner--caution">
          <ClockIcon />
          <p className="soc-banner__text">{p.body}</p>
        </div>
        <p className="soc-body">{p.body2}</p>
      </SocScreen>
      <SocActionBar primaryLabel={p.browse} onPrimary={onBrowse} />
    </>
  )
}

const APPROVED_TITLES = {
  '21+': 'title21',
  '18-20': 'title18',
  teen: 'titleTeen',
}

export function ApprovedState({ s, locale, band = '21+', onStart }) {
  const a = s.approved
  const title = a[APPROVED_TITLES[band] || 'title21']
  return (
    <>
      <SocScreen title={title}>
        <div className="soc-banner soc-banner--valid">
          <CheckIcon />
          <p className="soc-banner__text">{s.common.doorBadge}</p>
        </div>
        <div className="soc-approved-head">
          <SocBandChip band={band} size="glance" locale={locale} />
        </div>
        <p className="soc-body">{a.body}</p>
      </SocScreen>
      <SocActionBar primaryLabel={a.start} onPrimary={onStart} />
    </>
  )
}

export function RejectedState({ s, onRetake, onRequestReview }) {
  const r = s.rejected
  return (
    <>
      <SocScreen title={r.title}>
        <p className="soc-lede">{r.body}</p>
        <h2 className="soc-section-title">{r.retakeLabel}</h2>
        <p className="soc-body">{r.retake}</p>
        <h2 className="soc-section-title">{r.reviewLabel}</h2>
        <p className="soc-body">{r.review}</p>
      </SocScreen>
      <SocActionBar
        primaryLabel={r.reviewAction}
        onPrimary={onRequestReview}
        secondaryLabel={r.retakeAction}
        onSecondary={onRetake}
      />
    </>
  )
}

export function DormantState({ s, destroyedOn, onReverify, onDecline }) {
  const d = s.dormant
  return (
    <>
      <SocScreen title={d.title}>
        {destroyedOn ? (
          <div className="soc-banner soc-banner--caution">
            <ClockIcon />
            <p className="soc-banner__text">{fill(d.dateLine, { date: destroyedOn })}</p>
          </div>
        ) : null}
        <p className="soc-body">{d.body}</p>
        <p className="soc-body">{d.body2}</p>
      </SocScreen>
      <SocActionBar
        primaryLabel={d.reverify}
        onPrimary={onReverify}
        secondaryLabel={s.common.notNow}
        onSecondary={onDecline}
      />
    </>
  )
}
