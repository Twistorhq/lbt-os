/* TW-165: unit tests for the StoryArc scroll-math (storyArcMath.js).
 * Run: node frontend/src/components/__tests__/storyArcMath.test.mjs
 * Green run output is committed alongside in storyArcMath.test.evidence.txt.
 */
import { strict as assert } from 'node:assert'
import { clamp, stageStyles, bgStyles } from '../storyArcMath.js'

let passed = 0
function check(name, fn) {
  fn()
  passed += 1
  console.log(`ok - ${name}`)
}

// --- clamp ---
check('clamp clamps low', () => assert.equal(clamp(-5, 0, 10), 0))
check('clamp clamps high', () => assert.equal(clamp(99, 0, 10), 10))
check('clamp passes through', () => assert.equal(clamp(4, 0, 10), 4))

// --- stageStyles: centered stage (stagePos = index + 0.5) ---
check('centered panel: opacity 1, no offset, full scale, interactive', () => {
  const st = stageStyles(0.5, 0, false)
  assert.equal(st.opacity, 1)
  assert.equal(st.transform, 'translate3d(0, 0.0px, 0) scale(1.0000)')
  assert.equal(st.pointerEvents, true)
})

// --- stageStyles: fade band 0.3 -> 0.5 off-center ---
check('panel holds opacity 1 at 0.3 off-center', () => {
  assert.ok(Math.abs(stageStyles(0.5 + 0.3, 0, false).opacity - 1) < 1e-9)
})
check('panel fully faded at 0.5 off-center', () => {
  assert.equal(stageStyles(0.5 + 0.5, 0, false).opacity, 0)
})
check('panel midpoint fade at 0.4 off-center', () => {
  assert.ok(Math.abs(stageStyles(0.5 + 0.4, 0, false).opacity - 0.5) < 1e-9)
})

// --- stageStyles: pointerEvents mirrors the keyboard-trap contract ---
check('panel non-interactive when opacity <= 0.4 (inert follows this)', () => {
  // abs = 0.45 -> opacity = 1 - (0.45 - 0.3) / 0.2 = 0.25
  const st = stageStyles(0.5 + 0.45, 0, false)
  assert.ok(st.opacity <= 0.4)
  assert.equal(st.pointerEvents, false)
  // boundary contract holds exactly at sample points
  for (const off of [0.1, 0.3, 0.35, 0.45, 0.5, 1.2]) {
    const s = stageStyles(0.5 + off, 0, false)
    assert.equal(s.pointerEvents, s.opacity > 0.4, `off=${off}`)
  }
})
check('panel interactive at opacity 0.5', () => {
  assert.equal(stageStyles(0.5 + 0.4, 0, false).pointerEvents, true)
})

// --- stageStyles: rise direction and coarse choreography ---
check('panel above center rises negatively (fine pointer)', () => {
  const st = stageStyles(0.1, 0, false) // rel = -0.4
  assert.equal(st.transform, 'translate3d(0, -36.0px, 0) scale(0.9800)')
})
check('coarse pointers get the gentle 40px rise, no scale', () => {
  const st = stageStyles(0.1, 0, true) // rel = -0.4
  assert.equal(st.transform, 'translate3d(0, -16.0px, 0) scale(1.0000)')
})
check('rise clamps at 0.6 * rise for far stages', () => {
  const st = stageStyles(3.0, 0, false)
  assert.ok(st.transform.includes('54.0px'))
})

// --- stageStyles: far stage is fully gone ---
check('far stage: opacity 0, non-interactive', () => {
  const st = stageStyles(2.5, 0, false)
  assert.equal(st.opacity, 0)
  assert.equal(st.pointerEvents, false)
})

// --- stageStyles: dead `active` field is gone ---
check('stageStyles returns no dead active field', () => {
  assert.ok(!('active' in stageStyles(0.5, 0, false)))
})

// --- bgStyles: wider overlap than panels ---
check('background fully visible at center', () => {
  assert.equal(bgStyles(1.5, 1).opacity, 1)
})
check('background still half-visible where panels are gone (rel 0.5)', () => {
  assert.equal(stageStyles(1.0, 0, false).opacity, 0)
  assert.ok(Math.abs(bgStyles(1.0, 0).opacity - 0.5) < 1e-9)
})
check('background fades 0.4 -> 0.6 off-center', () => {
  assert.equal(bgStyles(0.5 + 0.4, 0).opacity, 1)
  assert.equal(bgStyles(0.5 + 0.6, 0).opacity, 0)
})

console.log(`\n${passed} assertions passed`)
