// Prepared by Twistor Holdings LLC.
//
// TW-652: render the biometric consent flow to a static HTML preview
// (every phase, English and Spanish) from the real components, so the
// screens can be eyeballed without standing up the app. Run with:
//   npx vite-node scripts/render-consent-preview.mjs
// Output: ../docs/evidence/TW-652-consent-preview.html
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import BiometricConsentFlow from '../src/components/social/consent/BiometricConsentFlow.jsx'

const here = dirname(fileURLToPath(import.meta.url))
const tokensCss = readFileSync(
  resolve(here, '../design-system/social/tokens.css'),
  'utf8',
)
const socialCss = readFileSync(
  resolve(here, '../src/components/social/social.css'),
  'utf8',
).replace(/@import[^;]+;\n/, '')

const PHASES = [
  ['explain', {}],
  ['schedule', {}],
  ['grant', {}],
  ['declined', {}],
  ['capture', {}],
  ['pending', {}],
  ['approved', { band: '21+' }],
  ['approved', { band: '18-20' }],
  ['rejected', {}],
  ['dormant', { destroyedOn: 'March 4, 2028' }],
]

function frame(locale, phase, extra) {
  const html = renderToStaticMarkup(
    React.createElement(BiometricConsentFlow, {
      locale,
      phase,
      destroyedOn: extra.destroyedOn || null,
      band: extra.band || '21+',
    }),
  )
  const label = `${locale.toUpperCase()} · ${phase}${extra.band ? ` · ${extra.band}` : ''}`
  return `<figure class="frame-wrap"><figcaption>${label}</figcaption><div class="phone">${html}</div></figure>`
}

const frames = ['en', 'es']
  .flatMap((locale) => PHASES.map(([phase, extra]) => frame(locale, phase, extra)))
  .join('\n')

const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TW-652 Biometric Consent Screens: Preview</title>
<style>
${tokensCss}
${socialCss}
body { margin: 0; background: #26241f; font-family: Inter, system-ui, sans-serif; }
header { color: #f5f2ea; padding: 24px 32px 8px; }
header h1 { font-size: 22px; margin: 0 0 6px; }
header p { margin: 0; opacity: 0.75; max-width: 70ch; }
.grid { display: flex; flex-wrap: wrap; gap: 28px; padding: 24px 32px 48px; }
.frame-wrap { margin: 0; }
.frame-wrap figcaption { color: #f5f2ea; font-size: 13px; font-weight: 600;
  letter-spacing: 0.04em; margin-bottom: 8px; text-transform: uppercase; }
.phone { position: relative; width: 375px; height: 780px; overflow: hidden;
  border-radius: 28px; transform: translateZ(0); background: var(--soc-canvas); }
.phone .soc-screen { height: 100%; overflow-y: auto; }
</style></head>
<body>
<header>
<h1>TW-652: Twistor Social biometric consent screens</h1>
<p>Server-rendered from the production components (BiometricConsentFlow), both
languages, all nine states. The grant screen renders with the attestation
unchecked, so the agree button shows its disabled state. Prepared by Twistor
Holdings LLC.</p>
</header>
<div class="grid">
${frames}
</div>
</body></html>`

const outDir = resolve(here, '../../docs/evidence')
mkdirSync(outDir, { recursive: true })
const out = resolve(outDir, 'TW-652-consent-preview.html')
writeFileSync(out, page)
console.log(`Wrote ${out} (${page.length} bytes)`)
