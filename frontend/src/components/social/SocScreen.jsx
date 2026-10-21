import './social.css'

/**
 * SocScreen (TW-652, pattern 00).
 *
 * The member-surface shell: day scheme by default, 16px thumb padding,
 * content capped at 560px, screen-reader order following thumb order
 * (step, title, content, then the fixed action bar rendered by the
 * caller after this element). The venue scheme is context-switched by
 * the surfaces that live in dark rooms; the consent flow never uses it
 * (pattern 01).
 *
 * Prepared by Twistor Holdings LLC
 */
export default function SocScreen({ step, title, children, scheme = 'day' }) {
  return (
    <main className="soc-screen" data-social-scheme={scheme}>
      <div className="soc-screen__inner">
        {step ? <p className="soc-step">{step}</p> : null}
        {title ? <h1 className="soc-title">{title}</h1> : null}
        {children}
      </div>
    </main>
  )
}
