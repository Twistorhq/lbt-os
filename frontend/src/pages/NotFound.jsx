import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { mountHoops } from '../lib/games/tw-553-hoops.js'

export default function NotFound() {
  const { pathname } = useLocation()
  const gameRef = useRef(null)

  useEffect(() => {
    if (!gameRef.current) return
    const game = mountHoops(gameRef.current, {
      sky: '#f8fafc',
      text: '#0f172a',
      muted: '#64748b',
      ball: '#e8722a',
      ballLine: '#b34d12',
      rim: '#dc2626',
      board: '#e2e8f0',
      boardEdge: '#94a3b8',
      net: '#cbd5e1',
      accent: '#2563eb',
      panel: '#ffffff',
      panelBorder: '#e2e8f0'
    })
    return () => game.destroy()
  }, [])

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-6 py-10 text-center">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-brand-50 text-3xl font-bold text-brand-600">
        404
      </div>
      <h1 className="text-xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 max-w-sm text-sm leading-6 text-slate-500">
        <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{pathname}</code>{' '}
        doesn't exist. Shoot around while you wait, then head back.
      </p>
      <div ref={gameRef} className="mt-6 w-full max-w-2xl" />
      <div className="mt-6 flex gap-3">
        <Link to="/app" className="btn-primary">Go to dashboard</Link>
        <Link to="/" className="btn-secondary">Marketing home</Link>
      </div>
    </div>
  )
}
