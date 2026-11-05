// Prepared by Twistor Holdings LLC.
//
// TW-941 (display half): render the member standing display to a
// static HTML preview (every fixture state, English and Spanish)
// from the real components, so the surface can be eyeballed without
// standing up the app. Run with:
//   npx vite-node scripts/render-standing-preview.mjs
// Output: ../docs/evidence/TW-941-standing-preview.html
//
// The fixtures are the documented TW-941 contract stand-in until
// Zeke's backend branch merges; the preview is labeled as such.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import SocStanding from '../src/components/social/SocStanding.jsx'
import SocScreen from '../src/components/social/SocScreen.jsx'
import { getStrings } from '../src/components/social/strings.js'
import { FIXTURE_STANDING, normalizeStanding } from '../src/lib/socialStanding.js'

const here = dirname(fileURLToPath(import.meta.url))
const tokensCss = readFileSync(
  resolve(here, '../design-system/social/tokens.css'),
  'utf8',
)
const socialCss = readFileSync(
  resolve(here, '../src/components/social/social.css'),
  'utf8',
).replace(/@import[^;]+;\n/, '')

const CASES = ['newMember', 'regularClimbing', 'keyholder', 'suspendedFixture']

function frame(locale, fixtureKey) {
  const standing = normalizeStanding(FIXTURE_STANDING[fixtureKey])
  const html = renderToStaticMarkup(
    React.createElement(
      SocScreen,
      { title: getStrings(locale).standing.title },
      React.createElement(SocStanding, { standing, locale }),
    ),
  )
  const label = `${locale.toUpperCase()} · ${fixtureKey} (fixture data)`
  return `<figure class="frame-wrap"><figcaption>${label}</figcaption><div class="phone">${html}</div></figure>`
}

const frames = ['en', 'es']
  .flatMap((locale) => CASES.map((fixtureKey) => frame(locale, fixtureKey)))
  .join('\n')

const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TW-941 Standing Display: Preview (fixture data)</title>
<style>
${tokensCss}
${socialCss}
body { margin: 0; background: #ddd6c4; font-family: Inter, system-ui, sans-serif; }
header { padding: 20px; }
.frame-wrap { display: inline-block; vertical-align: top; margin: 12px; }
.frame-wrap figcaption { font-weight: 700; padding: 6px 2px; }
.phone { width: 390px; border: 1px solid rgba(26,23,18,.25); border-radius: 12px; overflow: hidden; }
.phone .soc-screen { min-height: auto; }
</style></head>
<body>
<header><h1>TW-941 standing display preview</h1>
<p>Fixture data mirroring the documented tier and progress contract. Prepared by Twistor Holdings LLC.</p></header>
${frames}
</body></html>
`

const out = resolve(here, '../docs/evidence/TW-941-standing-preview.html')
mkdirSync(dirname(out), { recursive: true })
writeFileSync(out, page)
console.log(`wrote ${out}`)
