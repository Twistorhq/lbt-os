/* TW-572: 404 Easter egg snake game. Vanilla JS + canvas, zero dependencies.
 * mountSnake(container, opts): opts.theme 'corporate' or 'trades'.
 * Arrow keys / WASD / swipe to steer. Eat, grow, don't bite yourself.
 * Prepared by Twistor Holdings LLC.
 */
'use strict'

const THEMES = {
  corporate: {
    sky: '#ffffff', grid: '#eef2ff', gridEdge: '#e2e8f0',
    snake: '#4f46e5', snakeHead: '#3730a3', eye: '#ffffff',
    food: '#dc2626', foodLeaf: '#16a34a',
    text: '#0f172a', muted: '#64748b',
    panel: '#ffffff', panelBorder: '#e2e8f0', button: '#4f46e5', buttonText: '#ffffff'
  },
  trades: {
    sky: '#f4efe4', grid: '#ece5d3', gridEdge: '#d8cfbb',
    snake: '#4a3220', snakeHead: '#2b1d12', eye: '#faf7ef',
    food: '#b23f0a', foodLeaf: '#3f6212',
    text: '#2b2118', muted: '#6b6252',
    panel: '#faf7ef', panelBorder: '#d8cfbb', button: '#b23f0a', buttonText: '#ffffff'
  }
}

export function mountSnake(container, opts) {
  const theme = THEMES[opts && opts.theme === 'trades' ? 'trades' : 'corporate']
  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  container.classList.add('tw572-snake')
  container.innerHTML =
    '<div class="tw572-stage" style="position:relative">' +
    '<canvas role="img" aria-label="Snake game: use arrow keys or swipe to steer, eat to grow"></canvas>' +
    '<div class="tw572-hud" aria-live="polite"></div>' +
    '<div class="tw572-over" hidden></div>' +
    '</div>'
  const stage = container.querySelector('.tw572-stage')
  const canvas = container.querySelector('canvas')
  const hud = container.querySelector('.tw572-hud')
  const over = container.querySelector('.tw572-over')
  const ctx = canvas.getContext('2d')

  const style = document.createElement('style')
  style.textContent =
    '.tw572-snake{width:100%}' +
    '.tw572-stage{width:100%;border:1px solid ' + theme.panelBorder + ';border-radius:12px;overflow:hidden;background:' + theme.sky + '}' +
    '.tw572-stage canvas{display:block;width:100%;height:auto;touch-action:none;cursor:pointer}' +
    '.tw572-hud{position:absolute;top:10px;right:14px;font:700 14px/1.4 ui-monospace,Menlo,monospace;color:' + theme.muted + ';letter-spacing:1px;pointer-events:none}' +
    '.tw572-over{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:' + theme.sky + 'cc}' +
    '.tw572-over[hidden]{display:none}' +
    '.tw572-card{background:' + theme.panel + ';border:1px solid ' + theme.panelBorder + ';border-radius:12px;padding:22px 28px;text-align:center;box-shadow:0 12px 32px rgba(0,0,0,.12)}' +
    '.tw572-card h3{margin:0 0 6px;font:800 20px/1.3 system-ui,sans-serif;color:' + theme.text + '}' +
    '.tw572-card p{margin:0 0 14px;font:500 14px/1.5 system-ui,sans-serif;color:' + theme.muted + '}' +
    '.tw572-card button{font:700 14px/1 system-ui,sans-serif;color:' + theme.buttonText + ';background:' + theme.button + ';border:0;border-radius:8px;padding:12px 22px;min-height:44px;cursor:pointer}' +
    '.tw572-card button:focus-visible{outline:3px solid ' + theme.button + ';outline-offset:3px}'
  container.appendChild(style)

  const COLS = 20, ROWS = 20
  let W = 600, dpr = 1, raf = 0, last = 0, acc = 0
  let running = true, dead = false, started = false
  let snake = [], dir = { x: 1, y: 0 }, pending = null
  let food = null, stepMs = 150, score = 0, best = 0
  const bestKey = 'tw572-snake-best'
  try { best = parseInt(localStorage.getItem(bestKey) || '0', 10) || 0 } catch (e) { best = 0 }

  function resize() {
    const w = Math.max(280, Math.min(640, stage.clientWidth))
    W = w
    dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(W * dpr)
    canvas.style.aspectRatio = '1 / 1'
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  }

  function cell() { return W / COLS }

  function reset() {
    dead = false; started = false; running = true
    const mid = Math.floor(COLS / 2)
    snake = [{ x: mid, y: mid }, { x: mid - 1, y: mid }, { x: mid - 2, y: mid }]
    dir = { x: 1, y: 0 }; pending = null
    stepMs = reduceMotion ? 220 : 150
    score = 0; acc = 0
    placeFood()
    over.hidden = true
    hud.textContent = '0'
    last = performance.now()
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(tick)
  }

  function emptyCell() {
    const taken = new Set(snake.map(s => s.x + ',' + s.y))
    const free = []
    for (let x = 0; x < COLS; x++) {
      for (let y = 0; y < ROWS; y++) {
        if (!taken.has(x + ',' + y)) free.push({ x, y })
      }
    }
    if (!free.length) return null
    return free[Math.floor(Math.random() * free.length)]
  }

  function placeFood() {
    food = emptyCell()
  }

  function steer(nx, ny) {
    if (dead) { return }
    started = true
    if (nx === -dir.x && ny === -dir.y) return
    if (nx === dir.x && ny === dir.y) return
    pending = { x: nx, y: ny }
  }

  function step() {
    if (pending) { dir = pending; pending = null }
    const head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y }
    if (head.x < 0 || head.x >= COLS || head.y < 0 || head.y >= ROWS) { die(); return }
    for (let i = 0; i < snake.length - 1; i++) {
      if (snake[i].x === head.x && snake[i].y === head.y) { die(); return }
    }
    snake.unshift(head)
    if (food && head.x === food.x && head.y === food.y) {
      score++
      hud.textContent = String(score)
      if (!reduceMotion) stepMs = Math.max(70, stepMs - 2)
      placeFood()
      if (!food) { die(); return }
    } else {
      snake.pop()
    }
  }

  function die() {
    dead = true
    running = false
    const isBest = score > 0 && score > best
    if (isBest) {
      best = score
      try { localStorage.setItem(bestKey, String(best)) } catch (e) {}
    }
    over.innerHTML =
      '<div class="tw572-card" role="alertdialog" aria-label="Game over">' +
      '<h3>Tangled up.</h3>' +
      '<p>' + score + (score === 1 ? ' bite' : ' bites') + (isBest ? ' · new best.' : ' · Best ' + best + '.') + '</p>' +
      '<button type="button">Go again</button></div>'
    over.hidden = false
    const btn = over.querySelector('button')
    btn.addEventListener('click', reset)
    btn.focus()
  }

  function tick(now) {
    if (!running) return
    let dt = Math.min(0.1, (now - last) / 1000)
    last = now
    if (started && !dead) {
      acc += dt * 1000
      while (acc >= stepMs && !dead) {
        acc -= stepMs
        step()
      }
    }
    draw()
    if (running) raf = requestAnimationFrame(tick)
  }

  function draw() {
    const c = cell()
    ctx.clearRect(0, 0, W, W)
    ctx.fillStyle = theme.sky
    ctx.fillRect(0, 0, W, W)
    ctx.strokeStyle = theme.grid
    ctx.lineWidth = 1
    ctx.beginPath()
    for (let i = 1; i < COLS; i++) {
      ctx.moveTo(i * c + 0.5, 0); ctx.lineTo(i * c + 0.5, W)
      ctx.moveTo(0, i * c + 0.5); ctx.lineTo(W, i * c + 0.5)
    }
    ctx.stroke()
    ctx.strokeStyle = theme.gridEdge
    ctx.lineWidth = 2
    ctx.strokeRect(1, 1, W - 2, W - 2)

    if (food) {
      const fx = food.x * c + c / 2, fy = food.y * c + c / 2, r = c * 0.32
      ctx.fillStyle = theme.foodLeaf
      ctx.beginPath()
      ctx.ellipse(fx + r * 0.5, fy - r * 0.8, r * 0.45, r * 0.25, 0.6, 0, 7)
      ctx.fill()
      ctx.fillStyle = theme.food
      ctx.beginPath(); ctx.arc(fx, fy, r, 0, 7); ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,.35)'
      ctx.beginPath(); ctx.arc(fx - r * 0.3, fy - r * 0.3, r * 0.28, 0, 7); ctx.fill()
    }

    for (let i = snake.length - 1; i >= 0; i--) {
      const s = snake[i]
      const x = s.x * c, y = s.y * c, pad = Math.max(1, c * 0.06)
      ctx.fillStyle = i === 0 ? theme.snakeHead : theme.snake
      roundRect(x + pad, y + pad, c - pad * 2, c - pad * 2, c * 0.28)
      ctx.fill()
      if (i === 0) {
        const ex = x + c / 2, ey = y + c / 2
        const ox = dir.x * c * 0.16, oy = dir.y * c * 0.16
        const px = -dir.y * c * 0.14, py = dir.x * c * 0.14
        ctx.fillStyle = theme.eye
        ctx.beginPath(); ctx.arc(ex + ox + px, ey + oy + py, c * 0.09, 0, 7); ctx.fill()
        ctx.beginPath(); ctx.arc(ex + ox - px, ey + oy - py, c * 0.09, 0, 7); ctx.fill()
      }
    }

    if (!started && !dead) {
      ctx.fillStyle = theme.muted
      ctx.font = '600 15px system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('Arrows, WASD, or swipe to start', W / 2, W / 2 - c * 1.6)
    }
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
  }

  const DIRS = {
    ArrowUp: [0, -1], KeyW: [0, -1],
    ArrowDown: [0, 1], KeyS: [0, 1],
    ArrowLeft: [-1, 0], KeyA: [-1, 0],
    ArrowRight: [1, 0], KeyD: [1, 0]
  }
  function onKey(e) {
    const d = DIRS[e.code]
    if (!d) return
    if (document.activeElement && document.activeElement.tagName === 'BUTTON') return
    e.preventDefault()
    steer(d[0], d[1])
  }

  let touchStart = null
  canvas.addEventListener('pointerdown', e => {
    touchStart = { x: e.clientX, y: e.clientY }
  })
  canvas.addEventListener('pointerup', e => {
    if (!touchStart) return
    const dx = e.clientX - touchStart.x, dy = e.clientY - touchStart.y
    touchStart = null
    if (Math.abs(dx) < 24 && Math.abs(dy) < 24) return
    if (Math.abs(dx) > Math.abs(dy)) steer(dx > 0 ? 1 : -1, 0)
    else steer(0, dy > 0 ? 1 : -1)
  })
  canvas.addEventListener('pointercancel', () => { touchStart = null })
  window.addEventListener('keydown', onKey)

  const ro = new ResizeObserver(resize)
  ro.observe(stage)
  function onVis() {
    if (document.hidden) { running = false; cancelAnimationFrame(raf) }
    else if (!dead) { running = true; last = performance.now(); raf = requestAnimationFrame(tick) }
  }
  document.addEventListener('visibilitychange', onVis)

  resize()
  reset()

  return {
    destroy() {
      running = false
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('visibilitychange', onVis)
      container.innerHTML = ''
    },
    restart: reset
  }
}
