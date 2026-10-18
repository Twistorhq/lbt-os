import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { mountCar } from '../lib/games/tw-553-car.js'
import { mountHoops } from '../lib/games/tw-553-hoops.js'
import { mountRunner } from '../lib/games/tw-553-runner.js'
import { mountSnake } from '../lib/games/tw-553-snake.js'

const HOOPS_COLORS = {
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
}

const GAMES = [
  { id: 'car', label: 'Car dodger', mount: el => mountCar(el, { theme: 'corporate' }) },
  { id: 'snake', label: 'Snake', mount: el => mountSnake(el, { theme: 'corporate' }) },
  { id: 'hoops', label: 'Hoops', mount: el => mountHoops(el, HOOPS_COLORS) },
  { id: 'runner-trades', label: 'Runner (trades)', mount: el => mountRunner(el, { theme: 'trades' }) }
]

function rollIndex(except) {
  let i = Math.floor(Math.random() * GAMES.length)
  while (GAMES.length > 1 && i === except) i = Math.floor(Math.random() * GAMES.length)
  return i
}

export default function NotFound() {
  const { pathname } = useLocation()
  const gameRef = useRef(null)
  const [gameIdx, setGameIdx] = useState(() => rollIndex(-1))

  useEffect(() => {
    const el = gameRef.current
    if (!el) return undefined
    const game = GAMES[gameIdx].mount(el)
    return () => game.destroy()
  }, [gameIdx])

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-slate-50 px-6 py-10 text-center">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-brand-50 text-3xl font-bold text-brand-600">
        404
      </div>
      <h1 className="text-xl font-semibold text-slate-900">Page not found</h1>
      <p className="mt-2 max-w-sm text-sm leading-6 text-slate-500">
        <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">{pathname}</code>{' '}
        doesn't exist. A random arcade game loads below. Hit shuffle for another one.
      </p>
      <div ref={gameRef} className="mt-6 w-full max-w-2xl" />
      <button
        type="button"
        className="btn-secondary mt-3"
        onClick={() => setGameIdx(prev => rollIndex(prev))}
      >
        Another game
      </button>
      <div className="mt-6 flex gap-3">
        <Link to="/app" className="btn-primary">Go to dashboard</Link>
        <Link to="/" className="btn-secondary">Marketing home</Link>
      </div>
    </div>
  )
}
