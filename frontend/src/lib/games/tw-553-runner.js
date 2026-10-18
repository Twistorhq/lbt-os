/* TW-553: 404 Easter egg runner game. Vanilla JS + canvas, zero dependencies.
 * mountRunner(container, opts): opts.theme 'corporate' or 'trades'.
 * Tap / click / Space / ArrowUp to jump. Score = distance. Best in localStorage.
 * Prepared by Twistor Holdings LLC.
 */
'use strict'

const THEMES = {
  corporate: {
    sky: '#ffffff', ground: '#0f172a', groundLine: '#0f172a',
    player: '#4f46e5', playerDark: '#3730a3', eye: '#ffffff',
    obstacle: '#0f172a', obstacleAccent: '#4f46e5',
    cloud: '#eef2ff', text: '#0f172a', muted: '#64748b',
    panel: '#ffffff', panelBorder: '#e2e8f0', button: '#4f46e5', buttonText: '#ffffff'
  },
  trades: {
    sky: '#f4efe4', ground: '#8a7f6a', groundLine: '#5c5344',
    player: '#4a3220', playerDark: '#2b1d12', lace: '#ff5c1a',
    obstacle: '#ff5c1a', obstacleAccent: '#ffffff',
    toolbox: '#33445c', toolboxAccent: '#baf4cf',
    cloud: '#e9e2d2', text: '#2b2118', muted: '#6b6252',
    panel: '#faf7ef', panelBorder: '#d8cfbb', button: '#b23f0a', buttonText: '#ffffff'
  }
}

export function mountRunner(container, opts) {
  const theme = THEMES[opts && opts.theme === 'trades' ? 'trades' : 'corporate']
  const isTrades = opts && opts.theme === 'trades'
  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  container.classList.add('tw553-runner')
  container.innerHTML =
    '<div class="tw553-stage" style="position:relative">' +
    '<canvas role="img" aria-label="Runner game: press Space or tap to jump over obstacles"></canvas>' +
    '<div class="tw553-hud" aria-live="polite"></div>' +
    '<div class="tw553-over" hidden></div>' +
    '</div>'
  const stage = container.querySelector('.tw553-stage')
  const canvas = container.querySelector('canvas')
  const hud = container.querySelector('.tw553-hud')
  const over = container.querySelector('.tw553-over')
  const ctx = canvas.getContext('2d')

  const style = document.createElement('style')
  style.textContent =
    '.tw553-runner{width:100%}' +
    '.tw553-stage{width:100%;border:1px solid ' + theme.panelBorder + ';border-radius:12px;overflow:hidden;background:' + theme.sky + '}' +
    '.tw553-stage canvas{display:block;width:100%;height:auto;touch-action:manipulation;cursor:pointer}' +
    '.tw553-hud{position:absolute;top:10px;right:14px;font:700 14px/1.4 ui-monospace,Menlo,monospace;color:' + theme.muted + ';letter-spacing:1px;pointer-events:none}' +
    '.tw553-over{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:' + theme.sky + 'cc}' +
    '.tw553-over[hidden]{display:none}' +
    '.tw553-card{background:' + theme.panel + ';border:1px solid ' + theme.panelBorder + ';border-radius:12px;padding:22px 28px;text-align:center;box-shadow:0 12px 32px rgba(0,0,0,.12)}' +
    '.tw553-card h3{margin:0 0 6px;font:800 20px/1.3 system-ui,sans-serif;color:' + theme.text + '}' +
    '.tw553-card p{margin:0 0 14px;font:500 14px/1.5 system-ui,sans-serif;color:' + theme.muted + '}' +
    '.tw553-card button{font:700 14px/1 system-ui,sans-serif;color:' + theme.buttonText + ';background:' + theme.button + ';border:0;border-radius:8px;padding:12px 22px;min-height:44px;cursor:pointer}' +
    '.tw553-card button:focus-visible{outline:3px solid ' + theme.button + ';outline-offset:3px}'
  container.appendChild(style)

  let W = 800, H = 400, dpr = 1, raf = 0, last = 0
  let running = true, dead = false, started = false
  let speed = 320, dist = 0, score = 0, best = 0
  let obstacles = [], parts = [], spawnIn = 0, clouds = []
  const GROUND = () => H - 64
  const GRAV = 2600, JUMP_V = 920
  const bestKey = 'tw553-runner-best-' + (isTrades ? 'trades' : 'corp')
  try { best = parseInt(localStorage.getItem(bestKey) || '0', 10) || 0 } catch (e) { best = 0 }

  const player = { x: 90, w: 34, h: isTrades ? 46 : 34, y: 0, vy: 0, onGround: true, coyote: 0, buffer: 0, leg: 0 }

  function resize() {
    const w = Math.max(280, stage.clientWidth)
    W = w
    H = Math.max(260, Math.min(400, Math.round(w * 0.5)))
    dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(H * dpr)
    canvas.style.aspectRatio = W + ' / ' + H
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    if (player.onGround) player.y = GROUND() - player.h
  }

  function reset() {
    dead = false; started = false; running = true
    speed = 320; dist = 0; score = 0
    obstacles = []; parts = []; spawnIn = 500
    player.y = GROUND() - player.h; player.vy = 0; player.onGround = true
    over.hidden = true
    hud.textContent = '0 m'
    last = performance.now()
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(tick)
  }

  function jump() {
    if (dead) { reset(); return }
    started = true
    player.buffer = 0.12
  }

  function doJump() {
    player.vy = -JUMP_V
    player.onGround = false
    player.coyote = 0
    player.buffer = 0
    puff(player.x + 8, GROUND(), 5)
  }

  function puff(x, y, n) {
    for (let i = 0; i < n; i++) {
      parts.push({ x: x + (Math.random() - 0.5) * 20, y: y - Math.random() * 6, vx: (Math.random() - 0.5) * 120, vy: -Math.random() * 120, life: 0.4, max: 0.4 })
    }
  }

  function spawnObstacle() {
    const r = Math.random()
    if (isTrades) {
      obstacles.push(r < 0.55
        ? { kind: 'cone', x: W + 20, w: 30, h: 52 }
        : { kind: 'box', x: W + 20, w: 44, h: 36 })
    } else {
      obstacles.push(r < 0.5
        ? { kind: 'tri', x: W + 20, w: 34, h: 48 }
        : { kind: 'sq', x: W + 20, w: 30, h: 58 })
    }
  }

  function hit(a, b) {
    const pad = 5
    return a.x + pad < b.x + b.w - pad && a.x + a.w - pad > b.x + pad &&
      a.y + pad < b.y + b.h && a.y + a.h > b.y
  }

  function die() {
    dead = true
    running = false
    if (score > best) {
      best = score
      try { localStorage.setItem(bestKey, String(best)) } catch (e) {}
    }
    over.innerHTML =
      '<div class="tw553-card" role="alertdialog" aria-label="Game over">' +
      '<h3>Wiped out.</h3>' +
      '<p>' + score + ' m' + (score >= best && score > 0 ? ' · new best.' : ' · Best ' + best + ' m.') + '</p>' +
      '<button type="button">Run it back</button></div>'
    over.hidden = false
    const btn = over.querySelector('button')
    btn.addEventListener('click', reset)
    btn.focus()
  }

  function tick(now) {
    if (!running) return
    let dt = Math.min(0.05, (now - last) / 1000)
    last = now
    if (started && !dead) {
      speed = Math.min(660, speed + 9 * dt)
      dist += speed * dt
      const s = Math.floor(dist / 10)
      if (s !== score) { score = s; hud.textContent = score + ' m' }

      player.coyote -= dt; player.buffer -= dt
      if (player.buffer > 0 && (player.onGround || player.coyote > 0)) doJump()
      if (!player.onGround) {
        player.vy += GRAV * dt
        player.y += player.vy * dt
        if (player.y >= GROUND() - player.h) {
          player.y = GROUND() - player.h; player.vy = 0; player.onGround = true
          puff(player.x + 8, GROUND(), 4)
        }
      } else {
        player.leg += dt * 10
      }

      spawnIn -= speed * dt
      if (spawnIn <= 0) {
        spawnObstacle()
        spawnIn = 300 + Math.random() * 420 + speed * 0.35
      }
      for (const o of obstacles) {
        o.x -= speed * dt
        if (!o.ySet) { o.y = GROUND() - o.h; o.ySet = true }
      }
      obstacles = obstacles.filter(o => o.x + o.w > -40)

      const pb = { x: player.x, y: player.y, w: player.w, h: player.h }
      for (const o of obstacles) {
        if (hit(pb, { x: o.x, y: o.y, w: o.w, h: o.h })) { die(); break }
      }

      for (const p of parts) {
        p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 500 * dt; p.life -= dt
      }
      parts = parts.filter(p => p.life > 0)

      if (!reduceMotion) {
        for (const c of clouds) c.x -= speed * 0.18 * dt
        if (clouds.length < 5 && Math.random() < 0.01) {
          clouds.push({ x: W + 60, y: 30 + Math.random() * 120, s: 0.6 + Math.random() * 0.9 })
        }
        clouds = clouds.filter(c => c.x > -160)
      }
    }
    draw()
    if (running) raf = requestAnimationFrame(tick)
  }

  function drawBoot(x, y, w, h) {
    ctx.fillStyle = theme.player
    const sw = w * 0.52, sh = h * 0.62
    roundRect(x + (w - sw) / 2, y, sw, sh, 6); ctx.fill()
    ctx.fillRect(x + 2, y + sh - 6, w - 4, h - sh + 6)
    ctx.fillStyle = theme.playerDark
    ctx.fillRect(x, y + h - 7, w, 7)
    ctx.fillStyle = theme.lace
    const bounce = player.onGround ? Math.sin(player.leg) * 2 : 0
    for (let i = 0; i < 3; i++) {
      ctx.beginPath()
      ctx.arc(x + w / 2, y + 12 + i * 10 + bounce, 2.4, 0, 7)
      ctx.fill()
    }
  }

  function drawGeo(x, y, w, h) {
    ctx.fillStyle = theme.player
    roundRect(x, y, w, h, 9); ctx.fill()
    const bounce = player.onGround ? Math.sin(player.leg) * 1.5 : 0
    ctx.fillStyle = theme.eye
    ctx.beginPath(); ctx.arc(x + w * 0.68, y + h * 0.38 + bounce, 4.5, 0, 7); ctx.fill()
    ctx.fillStyle = theme.playerDark
    ctx.beginPath(); ctx.arc(x + w * 0.7, y + h * 0.38 + bounce, 2, 0, 7); ctx.fill()
  }

  function drawCone(o) {
    ctx.fillStyle = theme.obstacle
    ctx.beginPath()
    ctx.moveTo(o.x, o.y + o.h); ctx.lineTo(o.x + o.w / 2, o.y); ctx.lineTo(o.x + o.w, o.y + o.h)
    ctx.closePath(); ctx.fill()
    ctx.fillStyle = theme.obstacleAccent
    ctx.fillRect(o.x + o.w * 0.22, o.y + o.h * 0.42, o.w * 0.56, o.h * 0.18)
    ctx.fillStyle = theme.playerDark
    ctx.fillRect(o.x - 4, o.y + o.h - 5, o.w + 8, 5)
  }

  function drawBox(o) {
    ctx.fillStyle = theme.toolbox
    roundRect(o.x, o.y + 8, o.w, o.h - 8, 4); ctx.fill()
    ctx.strokeStyle = theme.toolboxAccent; ctx.lineWidth = 3
    ctx.beginPath(); ctx.arc(o.x + o.w / 2, o.y + 10, 9, Math.PI, 0); ctx.stroke()
    ctx.fillStyle = theme.toolboxAccent
    ctx.fillRect(o.x + 6, o.y + o.h * 0.55, o.w - 12, 3)
  }

  function drawTri(o) {
    ctx.fillStyle = theme.obstacle
    ctx.beginPath()
    ctx.moveTo(o.x, o.y + o.h); ctx.lineTo(o.x + o.w / 2, o.y); ctx.lineTo(o.x + o.w, o.y + o.h)
    ctx.closePath(); ctx.fill()
    ctx.fillStyle = theme.obstacleAccent
    ctx.beginPath()
    ctx.moveTo(o.x + o.w / 2, o.y + 8); ctx.lineTo(o.x + o.w - 8, o.y + o.h - 6); ctx.lineTo(o.x + 8, o.y + o.h - 6)
    ctx.closePath(); ctx.fill()
  }

  function drawSq(o) {
    ctx.fillStyle = theme.obstacle
    roundRect(o.x, o.y, o.w, o.h, 5); ctx.fill()
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

  function draw() {
    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = theme.sky
    ctx.fillRect(0, 0, W, H)
    if (!reduceMotion) {
      ctx.fillStyle = theme.cloud
      for (const c of clouds) {
        ctx.beginPath()
        ctx.arc(c.x, c.y, 22 * c.s, 0, 7)
        ctx.arc(c.x + 24 * c.s, c.y + 4 * c.s, 16 * c.s, 0, 7)
        ctx.arc(c.x - 24 * c.s, c.y + 5 * c.s, 14 * c.s, 0, 7)
        ctx.fill()
      }
    }
    const g = GROUND()
    ctx.fillStyle = theme.ground
    ctx.fillRect(0, g, W, H - g)
    ctx.fillStyle = theme.groundLine
    ctx.fillRect(0, g, W, 3)

    for (const o of obstacles) {
      const oy = o.ySet ? o.y : GROUND() - o.h
      const oo = { x: o.x, y: oy, w: o.w, h: o.h }
      if (o.kind === 'cone') drawCone(oo)
      else if (o.kind === 'box') drawBox(oo)
      else if (o.kind === 'tri') drawTri(oo)
      else drawSq(oo)
    }

    ctx.fillStyle = theme.muted
    for (const p of parts) {
      ctx.globalAlpha = Math.max(0, p.life / p.max) * 0.5
      ctx.beginPath(); ctx.arc(p.x, p.y, 3, 0, 7); ctx.fill()
    }
    ctx.globalAlpha = 1

    if (isTrades) drawBoot(player.x, player.y, player.w, player.h)
    else drawGeo(player.x, player.y, player.w, player.h)

    if (!started && !dead) {
      ctx.fillStyle = theme.muted
      ctx.font = '600 15px system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('Press Space or tap to start', W / 2, H / 2 - 20)
    }
  }

  function onKey(e) {
    if (e.code === 'Space' || e.code === 'ArrowUp') {
      if (document.activeElement && document.activeElement.tagName === 'BUTTON') return
      e.preventDefault()
      jump()
    }
  }
  canvas.addEventListener('pointerdown', e => { e.preventDefault(); jump() })
  window.addEventListener('keydown', onKey)

  const ro = new ResizeObserver(resize)
  ro.observe(stage)
  function onVis() {
    if (document.hidden) { running = false; cancelAnimationFrame(raf) }
    else if (!dead) { running = true; last = performance.now(); raf = requestAnimationFrame(tick) }
  }
  document.addEventListener('visibilitychange', onVis)

  resize()
  hud.textContent = '0 m'
  for (let i = 0; i < 4; i++) clouds.push({ x: Math.random() * W, y: 30 + Math.random() * 120, s: 0.6 + Math.random() * 0.9 })
  raf = requestAnimationFrame(tick)

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
