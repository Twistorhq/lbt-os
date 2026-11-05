import { useCallback, useEffect, useState } from 'react'
import SocScreen from './SocScreen'
import SocStanding from './SocStanding'
import { getStrings } from './strings'
import { fetchMyStanding } from '../../lib/socialStandingClient'
import './social.css'

/**
 * SocStandingScreen (TW-941, pattern 06): the member-profile standing
 * surface. Mobile first (Social is the one mobile-first product),
 * day scheme, content column inherited from SocScreen; the same
 * component is the desktop layer's content at wider viewports.
 *
 * Data arrives through the TW-941 client contract
 * (lib/socialStandingClient.fetchMyStanding). Callers may inject a
 * different fetcher, which is how tests and previews use the
 * fixture-backed client; the default is always the live contract,
 * and a failed or off-contract response renders the error state
 * with a retry, never fixture data.
 *
 * Prepared by Twistor Holdings LLC
 */
export default function SocStandingScreen({ locale = 'en', fetchStanding = fetchMyStanding }) {
  const s = getStrings(locale)
  const [state, setState] = useState({ status: 'loading', standing: null })

  const load = useCallback(async () => {
    setState({ status: 'loading', standing: null })
    try {
      const standing = await fetchStanding()
      setState({ status: 'ready', standing })
    } catch {
      setState({ status: 'error', standing: null })
    }
  }, [fetchStanding])

  useEffect(() => {
    let live = true
    fetchStanding()
      .then((standing) => {
        if (live) setState({ status: 'ready', standing })
      })
      .catch(() => {
        if (live) setState({ status: 'error', standing: null })
      })
    return () => {
      live = false
    }
  }, [fetchStanding])

  return (
    <SocScreen title={s.standing.title}>
      {state.status === 'loading' ? (
        <p className="soc-body" role="status">
          {s.standing.loading}
        </p>
      ) : null}
      {state.status === 'error' ? (
        <section aria-labelledby="soc-standing-error-title">
          <h2 id="soc-standing-error-title" className="soc-section-title">
            {s.standing.errorTitle}
          </h2>
          <p className="soc-body">{s.standing.errorBody}</p>
          <button type="button" className="soc-btn soc-btn--primary" onClick={load}>
            {s.standing.retry}
          </button>
        </section>
      ) : null}
      {state.status === 'ready' ? (
        <SocStanding standing={state.standing} locale={locale} />
      ) : null}
    </SocScreen>
  )
}
