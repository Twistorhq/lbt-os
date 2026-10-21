import './social.css'

/**
 * SocActionBar (TW-652, pattern 00).
 *
 * The fixed bottom action bar: one filled primary action at thumb
 * height, plus one optional text-weight secondary below it. Secondary
 * actions are always rendered and always labeled; the consent flow's
 * decline path depends on that rule (no dark patterns, ever).
 *
 * Prepared by Twistor Holdings LLC
 */
export default function SocActionBar({
  primaryLabel,
  onPrimary,
  primaryDisabled = false,
  secondaryLabel,
  onSecondary,
}) {
  return (
    <div className="soc-action-bar">
      <div className="soc-action-bar__inner">
        <button
          type="button"
          className="soc-btn soc-btn--primary"
          onClick={onPrimary}
          disabled={primaryDisabled}
        >
          {primaryLabel}
        </button>
        {secondaryLabel ? (
          <button type="button" className="soc-btn soc-btn--text" onClick={onSecondary}>
            {secondaryLabel}
          </button>
        ) : null}
      </div>
    </div>
  )
}
