/* TW-553: 404 Easter egg hoops shooter. Vanilla JS + canvas, zero dependencies.
 * mountHoops(container, opts): flick or drag to shoot. Simple gravity and bounce physics.
 * Pointer events (touch + mouse) and keyboard (arrows + Space). Streaks tracked.
 * Prepared by Twistor Holdings LLC.
 */
'use strict'

export function mountHoops(container, opts) {
  const o = opts || {}
  const C = {
    sky: o.sky || '#f8fafc', text: o.text || '#0f172a', muted: o.muted || '#64748b',
    ball: o.ball || '#e8722a', ballLine: o.ballLine || '#b34d12',
    rim: o.rim || '#dc2626', board: o.board || '#e2e8f0', boardEdge: o.boardEdge || '#94a3b8',
    net: o.net || '#cbd5e1', accent: o.accent || '#2563eb',
    panel: o.panel || '#ffffff', panelBorder: o.panelBorder || '#e2e8f0'
  }

  container.classList.add('tw553-hoops')
  container.innerHTML =
    '<div class="tw553-stage" style="position:relative">' +
    '<canvas role="img" aria-label="Basketball shooting game: drag back from the ball and release to shoot"></canvas>' +
    '<div class="tw553-hud" aria-live="polite"></div>' +
    '<p class="tw553-keys">Drag back and release to shoot. Keyboard: arrows aim, Space shoots.</p>' +
    '</div>'
  const stage = container.querySelector('.tw553-stage')
  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const canvas = stage.querySelector('canvas')
  const hud = stage.querySelector('.tw553-hud')
  const ctx = canvas.getContext('2d')

  const style = document.createElement('style')
  style.textContent =
    '.tw553-hoops{width:100%}' +
    '.tw553-stage{width:100%;border:1px solid ' + C.panelBorder + ';border-radius:12px;overflow:hidden;background:' + C.sky + '}' +
    '.tw553-stage canvas{display:block;width:100%;height:auto;touch-action:none;cursor:crosshair}' +
    '.tw553-hud{position:absolute;top:10px;left:14px;font:700 14px/1.4 ui-monospace,Menlo,monospace;color:' + C.muted + ';letter-spacing:1px;pointer-events:none}' +
    '.tw553-keys{margin:8px 4px 0;font:500 12px/1.5 system-ui,sans-serif;color:' + C.muted + ';text-align:center}'
  container.appendChild(style)

  let W = 800, H = 500, dpr = 1, raf = 0, last = 0, running = true
  const GRAV = 1500
  const GROUND = () => H - 36
  const BALL_R = 16

  const hoop = { boardX: 0, rimY: 0, rimL: 0, rimR: 0 }
  function layoutHoop() {
    hoop.boardX = W - 84
    hoop.rimY = 190
    hoop.rimL = W - 84
    hoop.rimR = W - 34
  }

  const home = { x: 130, y: 0 }
  const ball = { x: 130, y: 0, vx: 0, vy: 0, rot: 0, flying: false, touched: false, wasAbove: true }
  let aiming = false, aimX = 0, aimY = 0
  let angle = -52, power = 62
  let score = 0, streak = 0, best = 0, attempts = 0
  let floats = [], resetTimer = 0
  const bestKey = 'tw553-hoops-best'
  try { best = parseInt(localStorage.getItem(bestKey) || '0', 10) || 0 } catch (e) { best = 0 }

  function resize() {
    const w = Math.max(280, stage.clientWidth)
    W = w
    H = Math.max(320, Math.min(500, Math.round(w * 0.62)))
    dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(H * dpr)
    canvas.style.aspectRatio = W + ' / ' + H
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    layoutHoop()
    home.y = GROUND() - BALL_R
    if (!ball.flying) { ball.x = home.x; ball.y = home.y }
  }

  function updateHud() {
    hud.textContent = score + ' pts · streak ' + streak + ' · best ' + best
  }

  function shoot() {
    if (ball.flying) return
    const rad = angle * Math.PI / 180
    const sp = 320 + power * 9
    ball.vx = Math.cos(rad) * sp
    ball.vy = Math.sin(rad) * sp
    ball.flying = true
    ball.touched = false
    ball.wasAbove = true
    attempts++
    resetTimer = 0
  }

  function dragShoot(dx, dy) {
    if (ball.flying) return
    const sp = Math.min(430, Math.hypot(dx, dy)) * 4.4
    const a = Math.atan2(dy, dx)
    ball.vx = Math.cos(a) * sp
    ball.vy = Math.sin(a) * sp
    ball.flying = true
    ball.touched = false
    ball.wasAbove = true
    attempts++
    resetTimer = 0
  }

  function swishCheck(prevY) {
    if (!ball.flying) return
    const cx = (hoop.rimL + hoop.rimR) / 2
    const above = prevY < hoop.rimY - BALL_R * 0.4
    const nowIn = ball.y >= hoop.rimY - BALL_R * 0.4 &&
      Math.abs(ball.x - cx) < (hoop.rimR - hoop.rimL) / 2 - 4 &&
      ball.vy > 0
    if (above && nowIn && ball.wasAbove) {
      const pts = ball.touched ? 1 : 2
      score += pts
      streak++
      if (streak > best) {
        best = streak
        try { localStorage.setItem(bestKey, String(best)) } catch (e) {}
      }
      floats.push({ x: cx, y: hoop.rimY - 30, txt: '+' + pts + (pts === 2 ? ' swish' : ''), life: 1 })
      updateHud()
      ball.wasAbove = false
    }
    if (prevY < hoop.rimY && ball.y >= hoop.rimY) ball.wasAbove = false
  }

  function circleHit(px, py, pr, bx, by, br) {
    const dx = bx - px, dy = by - py
    const d = Math.hypot(dx, dy)
    return d < pr + br ? { dx: dx / (d || 1), dy: dy / (d || 1), overlap: pr + br - d } : null
  }

  function bounce(nx, ny, rest) {
    const dot = ball.vx * nx + ball.vy * ny
    ball.vx = (ball.vx - 2 * dot * nx) * rest
    ball.vy = (ball.vy - 2 * dot * ny) * rest
    ball.touched = true
  }

  function step(dt) {
    if (!ball.flying) return
    const prevY = ball.y
    ball.vy += GRAV * dt
    ball.x += ball.vx * dt
    ball.y += ball.vy * dt
    ball.rot += reduceMotion ? 0 : ball.vx * dt * 0.02

    const g = GROUND()
    if (ball.y + BALL_R > g) {
      ball.y = g - BALL_R
      ball.vy *= -0.55
      ball.vx *= 0.96
      ball.touched = true
      if (Math.abs(ball.vy) < 60) ball.vy = 0
    }
    if (ball.x - BALL_R < 0) { ball.x = BALL_R; ball.vx *= -0.6; ball.touched = true }
    if (ball.x + BALL_R > W) { ball.x = W - BALL_R; ball.vx *= -0.6; ball.touched = true }

    const rimPts = [
      { x: hoop.rimL, y: hoop.rimY },
      { x: hoop.rimR, y: hoop.rimY }
    ]
    for (const rp of rimPts) {
      const h = circleHit(rp.x, rp.y, 5, ball.x, ball.y, BALL_R)
      if (h) {
        ball.x += h.dx * h.overlap
        ball.y += h.dy * h.overlap
        bounce(h.dx, h.dy, 0.55)
      }
    }
    if (ball.x + BALL_R > hoop.boardX - 4 && ball.x - BALL_R < hoop.boardX + 4 &&
      ball.y > hoop.rimY - 82 && ball.y < hoop.rimY + 10 && ball.vx > 0) {
      ball.x = hoop.boardX - 4 - BALL_R
      bounce(-1, 0, 0.6)
    }

    swishCheck(prevY)

    resetTimer += dt
    const settled = Math.abs(ball.vx) < 25 && Math.abs(ball.vy) < 25 && ball.y + BALL_R >= g - 2
    if (resetTimer > 1.1 && (settled || ball.y > H + 80 || resetTimer > 4)) {
      ball.flying = false
      ball.x = home.x; ball.y = home.y
      ball.vx = 0; ball.vy = 0
      if (ball.wasAbove) streak = 0
      updateHud()
    }
  }

  function trajPoints() {
    const rad = angle * Math.PI / 180
    const sp = 320 + power * 9
    let x = home.x, y = home.y, vx = Math.cos(rad) * sp, vy = Math.sin(rad) * sp
    const pts = []
    for (let i = 0; i < 26; i++) {
      vy += GRAV * 0.035
      x += vx * 0.035
      y += vy * 0.035
      pts.push({ x, y })
      if (y > GROUND()) break
    }
    return pts
  }

  function draw() {
    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = C.sky
    ctx.fillRect(0, 0, W, H)

    const g = GROUND()
    ctx.strokeStyle = C.boardEdge
    ctx.lineWidth = 3
    ctx.beginPath(); ctx.moveTo(0, g); ctx.lineTo(W, g); ctx.stroke()

    ctx.strokeStyle = C.net
    ctx.lineWidth = 2
    const cx = (hoop.rimL + hoop.rimR) / 2
    for (let i = -1; i <= 1; i++) {
      ctx.beginPath()
      ctx.moveTo(cx + i * 14, hoop.rimY + 4)
      ctx.lineTo(cx + i * 9, hoop.rimY + 44)
      ctx.stroke()
    }
    ctx.beginPath()
    ctx.moveTo(hoop.rimL - 6, hoop.rimY + 44); ctx.lineTo(hoop.rimR + 6, hoop.rimY + 44)
    ctx.stroke()

    ctx.fillStyle = C.board
    ctx.strokeStyle = C.boardEdge
    ctx.lineWidth = 3
    roundRect(hoop.boardX - 4, hoop.rimY - 82, 10, 92, 3)
    ctx.fill(); ctx.stroke()

    ctx.strokeStyle = C.rim
    ctx.lineWidth = 6
    ctx.beginPath()
    ctx.moveTo(hoop.rimL, hoop.rimY)
    ctx.lineTo(hoop.rimR, hoop.rimY)
    ctx.stroke()
    ctx.fillStyle = C.rim
    for (const px of [hoop.rimL, hoop.rimR]) {
      ctx.beginPath(); ctx.arc(px, hoop.rimY, 5, 0, 7); ctx.fill()
    }

    ctx.fillStyle = 'rgba(0,0,0,0.08)'
    ctx.beginPath()
    ctx.ellipse(home.x, g + 4, BALL_R + 6, 5, 0, 0, 7)
    ctx.fill()

    if (aiming) {
      const dx = home.x - aimX, dy = home.y - aimY
      const pts = aimTraj(dx, dy)
      ctx.fillStyle = C.accent
      for (const p of pts) {
        ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, 7); ctx.fill()
      }
    } else if (!ball.flying) {
      const pts = trajPoints()
      ctx.fillStyle = C.muted
      ctx.globalAlpha = 0.45
      for (let i = 0; i < pts.length; i += 3) {
        ctx.beginPath(); ctx.arc(pts[i].x, pts[i].y, 2.5, 0, 7); ctx.fill()
      }
      ctx.globalAlpha = 1
      ctx.fillStyle = C.muted
      ctx.font = '600 12px system-ui, sans-serif'
      ctx.textAlign = 'left'
      ctx.fillText('angle ' + angle + '° · power ' + power, 14, H - 14)
    }

    drawBall(ball.x, ball.y, ball.rot)

    ctx.textAlign = 'center'
    for (const f of floats) {
      ctx.globalAlpha = Math.max(0, f.life)
      ctx.fillStyle = C.accent
      ctx.font = '800 18px system-ui, sans-serif'
      ctx.fillText(f.txt, f.x, reduceMotion ? f.y : f.y - (1 - f.life) * 24)
    }
    ctx.globalAlpha = 1
  }

  function aimTraj(dx, dy) {
    const sp = Math.min(430, Math.hypot(dx, dy)) * 4.4
    const a = Math.atan2(dy, dx)
    let x = home.x, y = home.y
    const vx = Math.cos(a) * sp, vy = Math.sin(a) * sp
    let vy2 = vy
    const pts = []
    for (let i = 0; i < 22; i++) {
      vy2 += GRAV * 0.035
      x += vx * 0.035
      y += vy2 * 0.035
      pts.push({ x, y })
      if (y > GROUND()) break
    }
    return pts
  }

  function drawBall(x, y, rot) {
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate(rot)
    ctx.fillStyle = C.ball
    ctx.beginPath(); ctx.arc(0, 0, BALL_R, 0, 7); ctx.fill()
    ctx.strokeStyle = C.ballLine
    ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(0, 0, BALL_R, 0, 7); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(-BALL_R, 0); ctx.lineTo(BALL_R, 0); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(0, -BALL_R); ctx.lineTo(0, BALL_R); ctx.stroke()
    ctx.beginPath(); ctx.arc(-BALL_R * 1.7, 0, BALL_R * 1.35, -0.7, 0.7); ctx.stroke()
    ctx.beginPath(); ctx.arc(BALL_R * 1.7, 0, BALL_R * 1.35, Math.PI - 0.7, Math.PI + 0.7); ctx.stroke()
    ctx.restore()
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

  function tick(now) {
    if (!running) return
    const dt = Math.min(0.05, (now - last) / 1000)
    last = now
    step(dt)
    for (const f of floats) f.life -= dt * 0.9
    floats = floats.filter(f => f.life > 0)
    draw()
    raf = requestAnimationFrame(tick)
  }

  function pos(e) {
    const r = canvas.getBoundingClientRect()
    return { x: (e.clientX - r.left) * (W / r.width), y: (e.clientY - r.top) * (H / r.height) }
  }

  canvas.addEventListener('pointerdown', e => {
    if (ball.flying) return
    aiming = true
    const p = pos(e)
    aimX = p.x; aimY = p.y
    canvas.setPointerCapture(e.pointerId)
  })
  canvas.addEventListener('pointermove', e => {
    if (!aiming) return
    const p = pos(e)
    aimX = p.x; aimY = p.y
  })
  canvas.addEventListener('pointerup', e => {
    if (!aiming) return
    aiming = false
    const p = pos(e)
    const dx = home.x - p.x, dy = home.y - p.y
    if (Math.hypot(dx, dy) > 24) dragShoot(dx, dy)
  })
  canvas.addEventListener('pointercancel', () => { aiming = false })

  function onKey(e) {
    if (e.code === 'ArrowLeft') { angle = Math.max(-80, angle - 2); e.preventDefault() }
    else if (e.code === 'ArrowRight') { angle = Math.min(-10, angle + 2); e.preventDefault() }
    else if (e.code === 'ArrowUp') { power = Math.min(100, power + 2); e.preventDefault() }
    else if (e.code === 'ArrowDown') { power = Math.max(10, power - 2); e.preventDefault() }
    else if (e.code === 'Space') {
      if (document.activeElement && document.activeElement.tagName === 'BUTTON') return
      e.preventDefault()
      shoot()
    }
  }
  window.addEventListener('keydown', onKey)

  const ro = new ResizeObserver(resize)
  ro.observe(stage)
  function onVis() {
    if (document.hidden) { running = false; cancelAnimationFrame(raf) }
    else { running = true; last = performance.now(); raf = requestAnimationFrame(tick) }
  }
  document.addEventListener('visibilitychange', onVis)

  resize()
  updateHud()
  last = performance.now()
  raf = requestAnimationFrame(tick)

  return {
    destroy() {
      running = false
      cancelAnimationFrame(raf)
      ro.disconnect()
      window.removeEventListener('keydown', onKey)
      document.removeEventListener('visibilitychange', onVis)
      container.innerHTML = ''
    }
  }
}
