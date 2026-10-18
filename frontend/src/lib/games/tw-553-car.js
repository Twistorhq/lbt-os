/* TW-572: 404 Easter egg car lane-dodger game. Vanilla JS + canvas, zero dependencies.
 * mountCar(container, opts): opts.theme 'corporate' or 'trades'.
 * Arrow keys / A D / tap left or right half to change lanes. Dodge traffic.
 * Prepared by Twistor Holdings LLC.
 */
'use strict'

const THEMES = {
  corporate: {
    sky: '#ffffff', road: '#eef2ff', edge: '#c7d2fe', dash: '#a5b4fc',
    car: '#4f46e5', carDark: '#3730a3', glass: '#eef2ff',
    enemy: '#0f172a', enemyGlass: '#94a3b8',
    cone: '#f59e0b', coneStripe: '#ffffff',
    text: '#0f172a', muted: '#64748b',
    panel: '#ffffff', panelBorder: '#e2e8f0', button: '#4f46e5', buttonText: '#ffffff'
  },
  trades: {
    sky: '#f4efe4', road: '#e9e2d2', edge: '#b9ad93', dash: '#d8cfbb',
    car: '#b23f0a', carDark: '#7c2d08', glass: '#faf7ef',
    enemy: '#33445c', enemyGlass: '#baf4cf',
    cone: '#ff5c1a', coneStripe: '#ffffff',
    text: '#2b2118', muted: '#6b6252',
    panel: '#faf7ef', panelBorder: '#d8cfbb', button: '#b23f0a', buttonText: '#ffffff'
  }
}

export function mountCar(container, opts) {
  const theme = THEMES[opts && opts.theme === 'trades' ? 'trades' : 'corporate']
  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  container.classList.add('tw572-car')
  container.innerHTML =
    '<div class="tw572-stage" style="position:relative">' +
    '<canvas role="img" aria-label="Car dodging game: arrows or tap left and right to change lanes"></canvas>' +
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
    '.tw572-car{width:100%}' +
    '.tw572-stage{width:100%;border:1px solid ' + theme.panelBorder + ';border-radius:12px;overflow:hidden;background:' + theme.sky + '}' +
    '.tw572-stage canvas{display:block;width:100%;height:auto;touch-action:manipulation;cursor:pointer}' +
    '.tw572-hud{position:absolute;top:10px;right:14px;font:700 14px/1.4 ui-monospace,Menlo,monospace;color:' + theme.muted + ';letter-spacing:1px;pointer-events:none}' +
    '.tw572-over{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:' + theme.sky + 'cc}' +
    '.tw572-over[hidden]{display:none}' +
    '.tw572-card{background:' + theme.panel + ';border:1px solid ' + theme.panelBorder + ';border-radius:12px;padding:22px 28px;text-align:center;box-shadow:0 12px 32px rgba(0,0,0,.12)}' +
    '.tw572-card h3{margin:0 0 6px;font:800 20px/1.3 system-ui,sans-serif;color:' + theme.text + '}' +
    '.tw572-card p{margin:0 0 14px;font:500 14px/1.5 system-ui,sans-serif;color:' + theme.muted + '}' +
    '.tw572-card button{font:700 14px/1 system-ui,sans-serif;color:' + theme.buttonText + ';background:' + theme.button + ';border:0;border-radius:8px;padding:12px 22px;min-height:44px;cursor:pointer}' +
    '.tw572-card button:focus-visible{outline:3px solid ' + theme.button + ';outline-offset:3px}'
  container.appendChild(style)

  const LANES = 3
  let W = 480, H = 640, dpr = 1, raf = 0, last = 0
  let running = true, dead = false, started = false
  let speed = 300, dist = 0, score = 0, best = 0
  let obstacles = [], spawnIn = 0, dashOff = 0
  const bestKey = 'tw572-car-best'
  try { best = parseInt(localStorage.getItem(bestKey) || '0', 10) || 0 } catch (e) { best = 0 }

  const road = { x: 0, w: 0 }
  const player = { lane: 1, x: 0, y: 0, w: 44, h: 76, tilt: 0 }

  function laneX(lane) {
    return road.x + (road.w / LANES) * (lane + 0.5)
  }

  function resize() {
    W = Math.max(280, Math.min(520, stage.clientWidth))
    H = Math.max(380, Math.min(640, Math.round(W * 1.25)))
    dpr = Math.min(2, window.devicePixelRatio || 1)
    canvas.width = Math.round(W * dpr)
    canvas.height = Math.round(H * dpr)
    canvas.style.aspectRatio = W + ' / ' + H
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    road.x = W * 0.12
    road.w = W * 0.76
    player.y = H - 130
    player.x = laneX(player.lane)
  }

  function reset() {
    dead = false; started = false; running = true
    speed = reduceMotion ? 220 : 300
    dist = 0; score = 0; spawnIn = 400; dashOff = 0
    obstacles = []
    player.lane = 1; player.x = laneX(1); player.tilt = 0
    over.hidden = true
    hud.textContent = '0 m'
    last = performance.now()
    cancelAnimationFrame(raf)
    raf = requestAnimationFrame(tick)
  }

  function move(d) {
    if (dead) { return }
    started = true
    player.lane = Math.max(0, Math.min(LANES - 1, player.lane + d))
  }

  function spawnObstacle() {
    const r = Math.random()
    const lane = Math.floor(Math.random() * LANES)
    const kind = r < 0.62 ? 'car' : 'cone'
    const w = kind === 'car' ? 44 : 30
    const h = kind === 'car' ? 76 : 40
    const o = { kind, lane, x: laneX(lane), y: -h - 10, w, h, v: 0.55 + Math.random() * 0.25 }
    obstacles.push(o)
  }

  function hit(a, b) {
    const pad = 6
    return a.x - a.w / 2 + pad < b.x + b.w / 2 - pad &&
      a.x + a.w / 2 - pad > b.x - b.w / 2 + pad &&
      a.y - a.h / 2 + pad < b.y + b.h / 2 - pad &&
      a.y + a.h / 2 - pad > b.y - b.h / 2 + pad
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
      '<h3>Pile-up.</h3>' +
      '<p>' + score + ' m' + (isBest ? ' · new best.' : ' · Best ' + best + ' m.') + '</p>' +
      '<button type="button">Drive again</button></div>'
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
      if (!reduceMotion) speed = Math.min(720, speed + 11 * dt)
      dist += speed * dt
      const s = Math.floor(dist / 10)
      if (s !== score) { score = s; hud.textContent = score + ' m' }
      dashOff = (dashOff + speed * dt) % 60

      const target = laneX(player.lane)
      const dx = target - player.x
      player.x += dx * Math.min(1, dt * 14)
      player.tilt = Math.max(-0.16, Math.min(0.16, dx * 0.002))

      spawnIn -= speed * dt
      if (spawnIn <= 0) {
        spawnObstacle()
        spawnIn = 260 + Math.random() * 380 + speed * 0.3
      }
      for (const o of obstacles) {
        o.y += speed * o.v * dt
      }
      obstacles = obstacles.filter(o => o.y - o.h / 2 < H + 40)

      const pb = { x: player.x, y: player.y, w: player.w, h: player.h }
      for (const o of obstacles) {
        if (hit(pb, o)) { die(); break }
      }
    }
    draw()
    if (running) raf = requestAnimationFrame(tick)
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

  function drawVehicle(x, y, w, h, body, dark, glass) {
    ctx.save()
    ctx.translate(x, y)
    ctx.fillStyle = 'rgba(0,0,0,.18)'
    roundRect(-w / 2 + 3, -h / 2 + 5, w, h, 10)
    ctx.fill()
    ctx.fillStyle = body
    roundRect(-w / 2, -h / 2, w, h, 10)
    ctx.fill()
    ctx.fillStyle = dark
    ctx.fillRect(-w / 2 + 4, -h / 2 + h * 0.62, w - 8, 5)
    ctx.fillStyle = glass
    roundRect(-w / 2 + 7, -h / 2 + h * 0.2, w - 14, h * 0.24, 4)
    ctx.fill()
    ctx.fillStyle = dark
    ctx.fillRect(-w / 2 - 2, -h / 2 + 6, 5, 14)
    ctx.fillRect(w / 2 - 3, -h / 2 + 6, 5, 14)
    ctx.fillRect(-w / 2 - 2, h / 2 - 20, 5, 14)
    ctx.fillRect(w / 2 - 3, h / 2 - 20, 5, 14)
    ctx.restore()
  }

  function drawCone(o) {
    ctx.fillStyle = theme.cone
    ctx.beginPath()
    ctx.moveTo(o.x - o.w / 2, o.y + o.h / 2)
    ctx.lineTo(o.x, o.y - o.h / 2)
    ctx.lineTo(o.x + o.w / 2, o.y + o.h / 2)
    ctx.closePath()
    ctx.fill()
    ctx.fillStyle = theme.coneStripe
    ctx.fillRect(o.x - o.w * 0.28, o.y - o.h * 0.08, o.w * 0.56, o.h * 0.16)
    ctx.fillStyle = 'rgba(0,0,0,.25)'
    ctx.fillRect(o.x - o.w / 2 - 3, o.y + o.h / 2 - 4, o.w + 6, 4)
  }

  function draw() {
    ctx.clearRect(0, 0, W, H)
    ctx.fillStyle = theme.sky
    ctx.fillRect(0, 0, W, H)
    ctx.fillStyle = theme.road
    ctx.fillRect(road.x, 0, road.w, H)
    ctx.fillStyle = theme.edge
    ctx.fillRect(road.x, 0, 4, H)
    ctx.fillRect(road.x + road.w - 4, 0, 4, H)
    ctx.fillStyle = theme.dash
    for (let l = 1; l < LANES; l++) {
      const x = road.x + (road.w / LANES) * l
      for (let y = -60 + dashOff; y < H + 60; y += 60) {
        ctx.fillRect(x - 2, y, 4, 30)
      }
    }

    for (const o of obstacles) {
      if (o.kind === 'car') drawVehicle(o.x, o.y, o.w, o.h, theme.enemy, '#00000033', theme.enemyGlass)
      else drawCone(o)
    }

    ctx.save()
    ctx.translate(player.x, player.y)
    ctx.rotate(player.tilt)
    ctx.translate(-player.x, -player.y)
    drawVehicle(player.x, player.y, player.w, player.h, theme.car, theme.carDark, theme.glass)
    ctx.restore()

    if (!started && !dead) {
      ctx.fillStyle = theme.muted
      ctx.font = '600 15px system-ui, sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText('Arrows or tap a side to steer', W / 2, 60)
    }
  }

  const KEYMAP = { ArrowLeft: -1, KeyA: -1, ArrowRight: 1, KeyD: 1 }
  function onKey(e) {
    const d = KEYMAP[e.code]
    if (d === undefined) return
    if (document.activeElement && document.activeElement.tagName === 'BUTTON') return
    e.preventDefault()
    move(d)
  }
  function onTap(e) {
    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    if (dead) { return }
    started = true
    player.lane = x < rect.width / 2
      ? Math.max(0, player.lane - 1)
      : Math.min(LANES - 1, player.lane + 1)
  }
  canvas.addEventListener('pointerdown', e => { e.preventDefault(); onTap(e) })
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
