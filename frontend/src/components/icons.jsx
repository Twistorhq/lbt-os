import { useId } from 'react'

/* TW-159: Twistor Trades SVG icon set. Stroke icons inherit currentColor. */

function Base({ children, className = '', ...rest }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

/** Twistor "T" mark — gradient stroke on a night-navy tile. */
export function TwistorMark({ className = 'h-8 w-8' }) {
  const id = useId()
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#8b8bf7" />
          <stop offset="1" stopColor="#f5b942" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="#14142b" />
      <path d="M9 10h14M16 10v12" stroke={`url(#${id})`} strokeWidth="3.4" strokeLinecap="round" />
      <path d="M16 14c3.2 0 3.2 4 6.4 4" stroke={`url(#${id})`} strokeWidth="3.4" strokeLinecap="round" fill="none" />
    </svg>
  )
}

export function ArrowRight(props) {
  return (
    <Base {...props}>
      <path d="M4 12h15m-6-7 7 7-7 7" />
    </Base>
  )
}

export function Lightning(props) {
  return (
    <Base {...props}>
      <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2Z" />
    </Base>
  )
}

export function Shield(props) {
  return (
    <Base {...props}>
      <path d="M12 3l7 2.8v5.4c0 4.3-2.9 7.4-7 8.8-4.1-1.4-7-4.5-7-8.8V5.8L12 3Z" />
    </Base>
  )
}

export function Sparkle(props) {
  return (
    <Base {...props}>
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
    </Base>
  )
}

export function Check(props) {
  return (
    <Base {...props}>
      <path d="M4 12.5l5 5L20 6.5" />
    </Base>
  )
}

/** Plug — "connect your tools" step. */
export function Plug(props) {
  return (
    <Base {...props}>
      <path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 0 1-10 0V7Z" />
      <path d="M12 16v5" />
    </Base>
  )
}

/** Gauge — "health score" step. */
export function Gauge(props) {
  return (
    <Base {...props}>
      <path d="M4 14a8 8 0 1 1 16 0" />
      <path d="M12 14l4.2-4.2" />
      <path d="M4 18h16" />
    </Base>
  )
}

/** List — "work the list" step. */
export function ListChecks(props) {
  return (
    <Base {...props}>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <path d="M4 6h.01M4 12h.01M4 18h.01" />
    </Base>
  )
}

export function Download(props) {
  return (
    <Base {...props}>
      <path d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
    </Base>
  )
}
