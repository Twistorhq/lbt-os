import { useEffect, useRef, useState } from 'react'

/* TW-159: cinematic scroll-motion primitives.
 * Architecture: ONE passive scroll listener + ONE rAF-throttled dispatcher for
 * the whole page. Components subscribe callbacks via useScrollEffect.
 * Everything animates transform/opacity only (compositor-friendly, no layout).
 * When the OS asks for reduced motion, scroll effects are skipped entirely —
 * content renders in its final state.
 */

/** Live region-free reduced-motion query with change listener. */
export function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

// ---- global scroll bus (single listener, rAF-throttled) ----
const subscribers = new Set()
let rafId = 0

function dispatch() {
  rafId = 0
  const y = window.scrollY
  const vh = window.innerHeight
  subscribers.forEach((cb) => {
    try { cb(y, vh) } catch { /* a failing subscriber must not break the bus */ }
  })
}

function schedule() {
  if (!rafId) rafId = requestAnimationFrame(dispatch)
}

if (typeof window !== 'undefined') {
  window.addEventListener('scroll', schedule, { passive: true })
  window.addEventListener('resize', schedule, { passive: true })
}

/**
 * Subscribe a callback to scroll position. callback(scrollY, viewportHeight).
 * Automatically unsubscribes; no-op when reduced motion is preferred.
 */
export function useScrollEffect(callback, deps = []) {
  const reduced = usePrefersReducedMotion()
  const ref = useRef(callback)
  ref.current = callback
  useEffect(() => {
    if (reduced) return
    const fn = (y, vh) => ref.current(y, vh)
    subscribers.add(fn)
    fn(window.scrollY, window.innerHeight)
    return () => { subscribers.delete(fn) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reduced, ...deps])
}

/** Element's vertical center as a fraction of the viewport (0 = top, 1 = bottom). */
function viewportPosition(el, vh) {
  const r = el.getBoundingClientRect()
  return (r.top + r.height / 2) / vh
}

/**
 * ScrollScale — element grows from `from` toward full size as the page scrolls,
 * with an ease-out cubic. Optional fade from 35% opacity.
 */
export function ScrollScale({ from = 0.94, fade = false, className = '', children, style, ...rest }) {
  const ref = useRef(null)
  useScrollEffect(() => {
    const el = ref.current
    if (!el) return
    const progress = Math.min(1, window.scrollY / (window.innerHeight * 0.55))
    const eased = 1 - Math.pow(1 - progress, 3)
    const scale = from + (1 - from) * eased
    el.style.transform = `scale(${scale.toFixed(4)})`
    if (fade) el.style.opacity = (0.35 + 0.65 * eased).toFixed(3)
  })
  return (
    <div ref={ref} data-motion className={className} style={{ willChange: 'transform, opacity', ...style }} {...rest}>
      {children}
    </div>
  )
}

/**
 * Parallax — drifts content against the scroll.
 * mode="copy": text floats up and fades (hero copy).
 * mode="visual": media sinks slightly, shrinks, and soft-fades (hero visual).
 */
export function Parallax({ mode = 'copy', className = '', children, style, ...rest }) {
  const ref = useRef(null)
  useScrollEffect(() => {
    const el = ref.current
    if (!el) return
    const u = Math.min(1, window.scrollY / (window.innerHeight * 0.85))
    if (mode === 'copy') {
      el.style.transform = `translate3d(0, ${(-90 * u).toFixed(1)}px, 0)`
      el.style.opacity = (1 - 0.85 * u).toFixed(3)
    } else {
      const s = 1 - 0.07 * u
      el.style.transform = `translate3d(0, ${(60 * u).toFixed(1)}px, 0) scale(${s.toFixed(4)})`
      el.style.opacity = (1 - 0.35 * u).toFixed(3)
    }
  })
  return (
    <div ref={ref} data-motion className={className} style={{ willChange: 'transform, opacity', ...style }} {...rest}>
      {children}
    </div>
  )
}

/**
 * DepthStage — children carrying data-depth="N" push toward/away from the
 * viewer (translateZ + compensating scale) as the stage crosses the viewport.
 */
export function DepthStage({ className = '', children, style, ...rest }) {
  const ref = useRef(null)
  useScrollEffect(() => {
    const stage = ref.current
    if (!stage) return
    const pos = viewportPosition(stage, window.innerHeight)
    stage.querySelectorAll('[data-depth]').forEach((child) => {
      const depth = parseFloat(child.dataset.depth || '0')
      const focus = 1 - Math.abs(pos - 0.5) * 2
      const z = depth * focus
      const scale = 1 + z / 1400
      child.style.transform = `translateZ(${z.toFixed(1)}px) scale(${scale.toFixed(4)})`
    })
  })
  return (
    <div ref={ref} className={`depth-stage ${className}`} style={style} {...rest}>
      {children}
    </div>
  )
}

/**
 * Reveal — IntersectionObserver-driven entrance. Adds .is-visible once the
 * element crosses the threshold, then unobserves. Renders without the reveal
 * class entirely under reduced motion (content just shows).
 */
export function Reveal({ delay = 0, y = 30, className = '', children, as: Tag = 'div', style, id }) {
  const ref = useRef(null)
  const reduced = usePrefersReducedMotion()
  useEffect(() => {
    const el = ref.current
    if (!el || reduced) return
    el.style.setProperty('--reveal-delay', `${delay}ms`)
    if (y !== 30) el.style.transform = `translateY(${y}px)`
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible')
            io.unobserve(entry.target)
          }
        })
      },
      { threshold: 0.12, rootMargin: '0px 0px -8% 0px' }
    )
    io.observe(el)
    return () => io.disconnect()
  }, [reduced, delay, y])
  return (
    <Tag ref={ref} id={id} className={reduced ? className : `reveal ${className}`} style={style}>
      {children}
    </Tag>
  )
}

/**
 * DriftImage — image drifts inside an overflow-hidden frame as it crosses the
 * viewport (subtle parallax, 1.18 base scale so edges never show).
 */
export function DriftImage({ src, alt, className = '', imgClassName = '', drift = 0.1, ...rest }) {
  const ref = useRef(null)
  useScrollEffect(() => {
    const frame = ref.current
    if (!frame) return
    const r = frame.getBoundingClientRect()
    const offset = r.top + r.height / 2 - window.innerHeight / 2
    const img = frame.firstElementChild
    if (img) img.style.transform = `translate3d(0, ${(-offset * drift).toFixed(1)}px, 0) scale(1.18)`
  })
  return (
    <div ref={ref} data-motion className={`overflow-hidden ${className}`} {...rest}>
      <img
        src={src}
        alt={alt}
        loading="lazy"
        className={`h-full w-full object-cover ${imgClassName}`}
        style={{ transform: 'scale(1.18)', willChange: 'transform' }}
      />
    </div>
  )
}
