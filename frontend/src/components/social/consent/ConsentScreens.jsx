import { useState } from 'react'
import SocScreen from '../SocScreen'
import SocActionBar from '../SocActionBar'
import { fill } from '../strings'
import '../social.css'

/**
 * The pre-capture consent screens (TW-652; pattern 01, step 1;
 * TW-654 Section 1.4, item 1a).
 *
 * Angela's flow has four separate moments; these screens are the
 * first: a standalone biometric consent, separate from the terms of
 * service, shown before any capture. Structure: what is captured and
 * why, the retention and destruction schedule in plain language, then
 * the grant itself as an affirmative, logged attestation. Declining
 * at any point captures nothing and returns the member to the ghost
 * town (pattern 02) with the decline screen below: no dark patterns,
 * no repeated nagging, one calm confirmation and out.
 *
 * The grant payload matches the vfy_consents contract exactly
 * (consent_type, policy_version, retention_schedule_version), so the
 * recorded release names the version of the text the member saw.
 *
 * Prepared by Twistor Holdings LLC
 */

function DoorBadgeLine({ label }) {
  return (
    <p className="soc-badge-line">
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path
          d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6l7-3z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <path
          d="M9 12l2.2 2.2L15.5 10"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {label}
    </p>
  )
}

function Fact({ label, text }) {
  return (
    <div className="soc-fact">
      {label ? <p className="soc-fact__label">{label}</p> : null}
      <p className="soc-fact__text">{text}</p>
    </div>
  )
}

export function ExplainScreen({ s, onContinue, onDecline }) {
  const e = s.explain
  return (
    <>
      <SocScreen step={s.common.stepConsent} title={e.title}>
        <DoorBadgeLine label={s.common.doorBadge} />
        <p className="soc-lede">{e.lede}</p>
        <h2 className="soc-section-title">{e.whatTitle}</h2>
        <Fact label={e.documentLabel} text={e.document} />
        <Fact label={e.selfieLabel} text={e.selfie} />
        <Fact label={e.templateLabel} text={e.template} />
        <h2 className="soc-section-title">{e.whyTitle}</h2>
        <Fact label="" text={e.purposeVerify} />
        <Fact label="" text={e.purposeDuplicates} />
        <Fact label="" text={e.purposeIntegrity} />
      </SocScreen>
      <SocActionBar
        primaryLabel={s.common.continue}
        onPrimary={onContinue}
        secondaryLabel={s.common.notNow}
        onSecondary={onDecline}
      />
    </>
  )
}

export function ScheduleScreen({ s, onContinue, onDecline, onPolicy }) {
  const r = s.schedule
  return (
    <>
      <SocScreen step={s.common.stepConsent} title={r.title}>
        <div className="soc-panel">
          <Fact label={r.photosLabel} text={r.photos} />
          <Fact label={r.templateLabel} text={r.template} />
          <Fact label={r.closureLabel} text={r.closure} />
          <Fact label={r.dormancyLabel} text={r.dormancy} />
          <Fact label={r.withdrawLabel} text={r.withdraw} />
          <Fact label={r.noSaleLabel} text={r.noSale} />
        </div>
        <p>
          <button type="button" className="soc-link soc-btn" onClick={onPolicy}>
            {r.policyLink}
          </button>
        </p>
      </SocScreen>
      <SocActionBar
        primaryLabel={s.common.continue}
        onPrimary={onContinue}
        secondaryLabel={s.common.notNow}
        onSecondary={onDecline}
      />
    </>
  )
}

export function GrantScreen({ s, policyVersion, retentionVersion, onGrant, onDecline }) {
  const [attested, setAttested] = useState(false)
  const g = s.grant
  return (
    <>
      <SocScreen step={s.common.stepConsent} title={g.title}>
        <p className="soc-lede">{g.body}</p>
        <p className="soc-body">{g.summary}</p>
        <label className="soc-attest">
          <input
            type="checkbox"
            checked={attested}
            onChange={(event) => setAttested(event.target.checked)}
          />
          <span>{g.attest}</span>
        </label>
        <p className="soc-version-line">
          {fill(g.versionLine, { policyVersion, retentionVersion })}
        </p>
      </SocScreen>
      <SocActionBar
        primaryLabel={g.agree}
        onPrimary={onGrant}
        primaryDisabled={!attested}
        secondaryLabel={s.common.notNow}
        onSecondary={onDecline}
      />
    </>
  )
}

export function DeclinedScreen({ s, onBrowse }) {
  const d = s.declined
  return (
    <>
      <SocScreen title={d.title}>
        <p className="soc-lede">{d.body}</p>
        <p className="soc-body">{d.body2}</p>
      </SocScreen>
      <SocActionBar primaryLabel={d.browse} onPrimary={onBrowse} />
    </>
  )
}
