import SocBandChip from './SocBandChip'
import { fill, getStrings } from './strings'
import { ACTIVITY_TIERS, SHOW_RATE_MIN_RSVPS } from '../../lib/socialStanding'
import './social.css'

/**
 * SocStanding (TW-941, pattern 06): the member-profile display of
 * current tier and progress toward the next rung.
 *
 * Display rules this component holds:
 * - Verified renders only as a future-phase rung in the ladder
 *   (parked verification phase): marked as coming later, never as
 *   earned, current, or earnable. A member with no activity tier
 *   sees progress toward Regular, not a Verified title.
 * - Activity rungs (Regular, Fixture, Pillar, Keyholder) show real
 *   progress as facts from the payload (nights, show rate, hosted
 *   rooms, reviews, streaks, vouches, tenure). No percentage bar,
 *   no estimate, no raw trust score (pattern 06).
 * - The show rate appears only at 5 or more decided RSVPs; below
 *   that the profile reads as still building a record (catalog S.5).
 * - While the safety band is watch or restricted, no tier is
 *   displayed anywhere in this component and no ladder or progress
 *   renders; the member still sees their own record and the path
 *   back (pattern 06).
 * - Teen accounts cap at Pillar: no Keyholder rung renders.
 * - No purchase, boost, gift, or transfer affordance exists here.
 *
 * The component renders a normalized standing object (see
 * lib/socialStanding.js); it never computes standing from raw data
 * and never invents a number the payload did not carry.
 *
 * Prepared by Twistor Holdings LLC
 */

const TIER_NAME_KEY = {
  verified: 'tierNameVerified',
  regular: 'tierNameRegular',
  fixture: 'tierNameFixture',
  pillar: 'tierNamePillar',
  keyholder: 'tierNameKeyholder',
}

const TIER_MEANING_KEY = {
  regular: 'regularMeaning',
  fixture: 'fixtureMeaning',
  pillar: 'pillarMeaning',
  keyholder: 'keyholderMeaning',
}

const COND_LABEL_KEY = {
  nights: 'condNights',
  showRate: 'condShowRate',
  corroboration: 'condCorroboration',
  streak: 'condStreak',
  vouches: 'condVouches',
  wmaRate: 'condWmaRate',
  wmaReviews: 'condWmaReviews',
  hostedClean: 'condHostedClean',
  tenureDays: 'condTenureDays',
}

function percent(rate) {
  return Math.round(rate * 100)
}

function tierName(s, tier) {
  return s.standing[TIER_NAME_KEY[tier]]
}

function ConditionFact({ condition, s }) {
  if (condition.kind === 'either') {
    const parts = condition.parts.map((part) => (
      <span key={part.key}>
        {s.standing[COND_LABEL_KEY[part.key]]}:{' '}
        {fill(s.standing.condFact, { current: part.current, target: part.target })}
      </span>
    ))
    return (
      <span>
        {parts[0]} {s.standing.condOr} {parts[1]}
      </span>
    )
  }
  if (condition.kind === 'rate') {
    if (condition.current === null || condition.current === undefined) {
      return <span>{s.standing.condNoRate}</span>
    }
    return (
      <span>
        {fill(s.standing.condFactRate, {
          current: percent(condition.current),
          target: percent(condition.target),
        })}
      </span>
    )
  }
  return (
    <span>
      {fill(s.standing.condFact, {
        current: condition.current,
        target: condition.target,
      })}
    </span>
  )
}

function ConditionStatus({ condition, s }) {
  if (condition.met) return <span className="soc-cond__status">{s.standing.condMet}</span>
  if (condition.kind === 'count') {
    return (
      <span className="soc-cond__status">
        {fill(s.standing.condRemaining, {
          remaining: condition.target - condition.current,
        })}
      </span>
    )
  }
  return null
}

export default function SocStanding({ standing, locale = 'en' }) {
  if (!standing) return null
  const s = getStrings(locale)
  const { stats } = standing
  const showRateVisible = stats.decidedRsvps >= SHOW_RATE_MIN_RSVPS && stats.showRate !== null
  const isTeen = standing.ageBand === 'teen'
  const ladderTiers = isTeen
    ? ACTIVITY_TIERS.filter((tier) => tier !== 'keyholder')
    : ACTIVITY_TIERS

  return (
    <div className="soc-standing">
      <div className="soc-standing__head">
        <SocBandChip band={standing.ageBand} locale={locale} />
        {standing.suspended ? null : standing.activityTier ? (
          <>
            <h2 className="soc-standing__tier">{tierName(s, standing.activityTier)}</h2>
            <p className="soc-standing__meaning">
              {s.standing[TIER_MEANING_KEY[standing.activityTier]]}
            </p>
          </>
        ) : (
          <>
            <h2 className="soc-standing__tier">{s.standing.noTierYet}</h2>
            <p className="soc-standing__meaning">{s.standing.noTierBody}</p>
          </>
        )}
        {standing.suspended ? null : showRateVisible ? (
          <p className="soc-standing__showrate">
            {fill(s.standing.showsUp, { percent: percent(stats.showRate) })}
          </p>
        ) : (
          <p className="soc-standing__showrate">{s.standing.buildingRecord}</p>
        )}
      </div>

      {standing.suspended ? (
        <section aria-labelledby="soc-suspended-title">
          <h2 id="soc-suspended-title" className="soc-section-title">
            {s.standing.suspendedTitle}
          </h2>
          <p className="soc-body">
            {fill(s.standing.suspendedBody, {
              band:
                standing.band === 'restricted'
                  ? s.standing.bandRestricted
                  : s.standing.bandWatch,
            })}
          </p>
        </section>
      ) : (
        <>
          <section aria-labelledby="soc-ladder-title">
            <h3 id="soc-ladder-title" className="soc-section-title">
              {s.standing.ladderTitle}
            </h3>
            <ol className="soc-ladder">
              <li className="soc-rung" data-rung="verified" data-state="future">
                <span className="soc-rung__mark" aria-hidden="true" />
                <span className="soc-rung__body">
                  <span className="soc-rung__name">{tierName(s, 'verified')}</span>
                  <span className="soc-rung__state">{s.standing.futurePhase}</span>
                  <span className="soc-rung__note">{s.standing.verifiedFutureBody}</span>
                </span>
              </li>
              {ladderTiers.map((tier) => {
                const state = !standing.activityTier
                  ? tier === standing.nextTier
                    ? 'next'
                    : 'upcoming'
                  : tier === standing.activityTier
                    ? 'current'
                    : ACTIVITY_TIERS.indexOf(tier) < ACTIVITY_TIERS.indexOf(standing.activityTier)
                      ? 'earned'
                      : tier === standing.nextTier
                        ? 'next'
                        : 'upcoming'
                const stateLabel =
                  state === 'earned'
                    ? s.standing.stateEarned
                    : state === 'current'
                      ? s.standing.stateCurrent
                      : state === 'next'
                        ? s.standing.stateNext
                        : s.standing.stateUpcoming
                return (
                  <li key={tier} className="soc-rung" data-rung={tier} data-state={state}>
                    <span className="soc-rung__mark" aria-hidden="true" />
                    <span className="soc-rung__body">
                      <span className="soc-rung__name">{tierName(s, tier)}</span>
                      <span className="soc-rung__state">{stateLabel}</span>
                    </span>
                  </li>
                )
              })}
            </ol>
            {isTeen ? <p className="soc-body soc-body--dim">{s.standing.teenCapNote}</p> : null}
          </section>

          {standing.nextTier ? (
            <section aria-labelledby="soc-next-title">
              <h3 id="soc-next-title" className="soc-section-title">
                {fill(s.standing.nextTitle, { tier: tierName(s, standing.nextTier) })}
              </h3>
              <ul className="soc-conds">
                {standing.progress.map((condition) => (
                  <li
                    key={condition.key}
                    className="soc-cond"
                    data-condition={condition.key}
                    data-met={condition.met ? 'true' : 'false'}
                  >
                    <span className="soc-cond__label">
                      {s.standing[COND_LABEL_KEY[condition.key]]}
                    </span>
                    <span className="soc-cond__fact">
                      <ConditionFact condition={condition} s={s} />
                    </span>
                    <ConditionStatus condition={condition} s={s} />
                  </li>
                ))}
              </ul>
            </section>
          ) : (
            <p className="soc-body">{s.standing.topTier}</p>
          )}
        </>
      )}

      <section aria-labelledby="soc-detail-title">
        <h3 id="soc-detail-title" className="soc-section-title">
          {s.standing.detailTitle}
        </h3>
        <ul className="soc-detail">
          <li>{fill(s.standing.detailNights, { count: stats.completedNights })}</li>
          <li>
            {showRateVisible
              ? fill(s.standing.detailShowRate, {
                  percent: percent(stats.showRate),
                  completed: stats.completedNights,
                  decided: stats.decidedRsvps,
                })
              : s.standing.buildingRecord}
          </li>
          <li>
            {stats.wouldMeetAgainRate !== null && stats.reviewsReceived > 0
              ? fill(s.standing.detailWma, {
                  percent: percent(stats.wouldMeetAgainRate),
                  count: stats.reviewsReceived,
                })
              : s.standing.detailWmaNone}
          </li>
          <li>{fill(s.standing.detailHosted, { count: stats.hostedClean })}</li>
          <li>{fill(s.standing.detailStreak, { count: stats.bestSeriesStreak })}</li>
          <li>{fill(s.standing.detailVouches, { count: stats.distinctVouches })}</li>
          <li>{fill(s.standing.detailTenure, { count: stats.tenureDays })}</li>
        </ul>
        {standing.lastMovement === 'dormancy' ? (
          <p className="soc-body soc-body--dim">{s.standing.movementDormancy}</p>
        ) : null}
        {standing.lastMovement === 'conduct' ? (
          <p className="soc-body soc-body--dim">{s.standing.movementConduct}</p>
        ) : null}
      </section>
    </div>
  )
}
