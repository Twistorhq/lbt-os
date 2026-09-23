#!/usr/bin/env node
/**
 * Smoke tests for the TW-181 agent-UI renderer (no test runner in this repo,
 * so this hermetic node script is the evidence gate).
 *
 * Bundles TwistorRenderer + catalog with esbuild (self-contained, deps inlined),
 * renders hostile specs with react-dom/server, and asserts graceful behavior.
 * Exits non-zero on any failure. Run: node scripts/test-agent-ui.mjs
 * (or: timeout 120 node scripts/test-agent-ui.mjs)
 */
import { buildSync } from 'esbuild'
import { writeFileSync, unlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const frontendDir = join(dirname(fileURLToPath(import.meta.url)), '..')

const testSrc = `
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import TestRenderer, { act } from 'react-test-renderer'
import TwistorRenderer from './src/agent-ui/TwistorRenderer.jsx'
import { validateAgentSpec } from './src/agent-ui/catalog.js'
import sampleSpec from './src/agent-ui/sampleSpec.json'

const results = []
function check(name, fn) {
  try {
    fn()
    results.push(['PASS', name])
  } catch (e) {
    results.push(['FAIL', name + ' — ' + (e && e.message)])
  }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed') }
const render = (spec, onAction) =>
  renderToStaticMarkup(React.createElement(TwistorRenderer, { spec, onAction }))

// 1. Valid sample spec renders real content (Rosa Blocker 1 regression: const reassignment crashed here)
check('valid spec renders without crashing', () => {
  const html = render(sampleSpec)
  assert(html.includes('Monday morning'), 'missing headline')
  assert(html.includes('$12,480'), 'missing metric value')
  assert(html.includes('Money walking out the door'), 'missing leak card')
})

// 2. Unknown component type -> safe fallback, no crash
check('unknown type renders fallback', () => {
  const html = render({ root: 'a', elements: { a: { type: 'Nope', props: {}, children: [] } } })
  assert(html.includes('could not be rendered'), 'missing fallback card')
})

// 3. Invalid props (zod) -> safe fallback, no crash
check('invalid props render fallback', () => {
  const html = render({ root: 'm', elements: { m: { type: 'MetricCard', props: { label: 'X', value: 12345 }, children: [] } } })
  assert(html.includes('could not be rendered'), 'missing fallback card')
})

// 4. Cycle a->b->a terminates
check('cyclic spec terminates', () => {
  const html = render({ root: 'a', elements: {
    a: { type: 'DashboardSection', props: { title: 'A' }, children: ['b'] },
    b: { type: 'DashboardSection', props: { title: 'B' }, children: ['a'] },
  } })
  assert(typeof html === 'string', 'no html returned')
})

// 5. Missing child key skipped
check('dangling child key skipped', () => {
  const html = render({ root: 'a', elements: {
    a: { type: 'DashboardSection', props: { title: 'A' }, children: ['ghost'] },
  } })
  assert(html.includes('A'), 'parent missing')
})

// 6. Deep non-cyclic chain capped by MAX_DEPTH
check('deep chain capped', () => {
  const elements = {}
  for (let i = 0; i < 60; i++) {
    elements['n' + i] = { type: 'DashboardSection', props: { title: 'L' + i }, children: i < 59 ? ['n' + (i + 1)] : [] }
  }
  const html = render({ root: 'n0', elements })
  assert(typeof html === 'string' && html.length > 0, 'no html returned')
})

// 7. Action emission reaches the host with (actionId, elementKey)
check('action button emits to host', () => {
  let seen = null
  const spec = { root: 'b', elements: { b: { type: 'ActionButton', props: { label: 'Go', action: 'do_thing' }, children: [] } } }
  let comp = null
  act(() => {
    comp = TestRenderer.create(
      React.createElement(TwistorRenderer, { spec, onAction: (a, k) => { seen = [a, k] } })
    )
  })
  try {
    const button = comp.root.findByType('button')
    act(() => { button.props.onClick() })
    assert(
      seen !== null && seen[0] === 'do_thing' && seen[1] === 'b',
      'expected onAction("do_thing", "b"), got ' + JSON.stringify(seen)
    )
  } finally {
    comp.unmount()
  }
})

// 7b. Non-array children (string / object / number) cannot crash the render
check('non-array children are ignored safely', () => {
  for (const bad of ['c1', { 0: 'c1' }, 42]) {
    const html = render({ root: 'a', elements: {
      a: { type: 'DashboardSection', props: { title: 'A' }, children: bad },
    } })
    assert(html.includes('A'), 'parent missing for children=' + JSON.stringify(bad))
  }
})

// 8. Validator: sample spec ok
check('validateAgentSpec accepts sample spec', () => {
  const v = validateAgentSpec(sampleSpec)
  assert(v.ok === true, 'expected ok, got issues: ' + JSON.stringify(v.issues).slice(0, 200))
})

// 9. Validator: dangling child rejected (documented contract)
check('validateAgentSpec rejects dangling child', () => {
  const v = validateAgentSpec({ root: 'a', elements: { a: { type: 'DashboardSection', props: { title: 'A' }, children: ['ghost'] } } })
  assert(v.ok === false, 'expected rejection')
  assert(v.issues.some(i => i.code === 'dangling_child'), 'expected dangling_child issue')
})

// 10. Validator: unknown type rejected
check('validateAgentSpec rejects unknown type', () => {
  const v = validateAgentSpec({ root: 'a', elements: { a: { type: 'Nope', props: {}, children: [] } } })
  assert(v.ok === false, 'expected rejection')
})

for (const [s, n] of results) console.log(s + ': ' + n)
const fails = results.filter(([s]) => s === 'FAIL')
if (fails.length) { console.error(fails.length + ' FAILURES'); process.exit(1) }
console.log('All ' + results.length + ' agent-UI smoke tests passed.')
`

const outFile = join(tmpdir(), `agent-ui-test-${process.pid}.cjs`)
buildSync({
  stdin: { contents: testSrc, loader: 'jsx', resolveDir: frontendDir },
  absWorkingDir: frontendDir,
  bundle: true,
  platform: 'node',
  format: 'cjs',
  jsx: 'automatic',
  outfile: outFile,
  logLevel: 'error',
})

try {
  require(outFile)
} catch (e) {
  console.error('TEST HARNESS ERROR:', e)
  process.exit(2)
} finally {
  try { unlinkSync(outFile) } catch {}
}
