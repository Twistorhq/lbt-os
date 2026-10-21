import { getStrings } from './strings'
import './social.css'

/**
 * SocBandChip (TW-652, pattern 00).
 *
 * The age band chip states a verified fact about the member, the way a
 * door states it. Exactly three variants exist: 21+, 18+ (the black
 * chip, carrying the 18 to 20 band), and TEEN (outlined, phase 2).
 * There is deliberately no variant for restriction, sanction, or watch
 * state: punishment is never displayed anywhere in the product.
 *
 * The chip is text, announced as text; color is never the only signal.
 *
 * Fail closed (TW-652 review M1): a missing or unrecognized band is
 * not a verified fact, so the chip renders nothing for it. There is
 * no default band anywhere in the display chain; an unknown band is
 * treated as no claim, never as the most privileged band.
 *
 * Prepared by Twistor Holdings LLC
 */
const VARIANTS = {
  '21+': { className: 'soc-chip--21', text: '21+', ariaKey: 'aria21' },
  '18-20': { className: 'soc-chip--18', text: '18+', ariaKey: 'aria18' },
  teen: { className: 'soc-chip--teen', text: 'TEEN', ariaKey: 'ariaTeen' },
}

/** The only bands the display chain may state as fact. */
export function isKnownBand(band) {
  return Object.prototype.hasOwnProperty.call(VARIANTS, band)
}

export default function SocBandChip({ band, size = 'standard', locale = 'en' }) {
  if (!isKnownBand(band)) return null
  const variant = VARIANTS[band]
  const s = getStrings(locale)
  const sizeClass = size === 'glance' ? ' soc-chip--glance' : ''
  return (
    <span
      className={`soc-chip ${variant.className}${sizeClass}`}
      role="img"
      aria-label={s.chip[variant.ariaKey]}
    >
      {variant.text}
    </span>
  )
}
