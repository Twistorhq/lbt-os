import { useEffect, useRef, useState } from 'react'
import { usePrefersReducedMotion } from '../motion'

/**
 * TW-207: CountUp — a number that counts to its target with an ease-out
 * cubic curve (cinematic reveal for money moments). Under
 * prefers-reduced-motion the final value renders instantly.
 *
 * @param {number} target - the value to count to
 * @param {object} opts - { duration } duration in ms, default 1400
 * @returns {number} the current displayed value
 */
export function useCountUp(target, { duration = 1400 } = {}) {
  const reduced = usePrefersReducedMotion()
  const [value, setValue] = useState(0)
  const raf = useRef(0)
  const start = useRef(0)

  useEffect(() => {
    const t = Number(target) || 0
    if (reduced || duration <= 0) {
      setValue(t)
      return
    }
    setValue(0)
    start.current = performance.now()
    const tick = (now) => {
      const p = Math.min(1, (now - start.current) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setValue(t * eased)
      if (p < 1) raf.current = requestAnimationFrame(tick)
      else setValue(t)
    }
    raf.current = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf.current)
  }, [target, duration, reduced])

  return value
}

/** Display-ready money string, always. */
export function fmtMoney(n) {
  return `$${Math.round(Number(n) || 0).toLocaleString('en-US')}`
}
