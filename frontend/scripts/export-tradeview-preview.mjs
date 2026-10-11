// Bundle the same React component for keyless, offline review. No app auth bundle.
import { build } from 'vite'
import { writeFile, mkdir, mkdtemp, rm } from 'node:fs/promises'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { build as bundleServer } from 'esbuild'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
const root = resolve(new URL('..', import.meta.url).pathname)
const out = resolve(root, '../docs/previews')
const result = await build({
  root, configFile: false,
  define: { 'process.env.NODE_ENV': '"production"' },
  build: { write: false, minify: true, lib: { entry: resolve(root, 'src/tradeview-preview.jsx'), name: 'TradeViewLite', formats: ['iife'] } },
  esbuild: { jsx: 'automatic' },
})
const chunks = result.output || result[0].output
const js = chunks.filter((item) => item.type === 'chunk').map((item) => item.code).join('\n')
const css = chunks.filter((item) => item.fileName.endsWith('.css')).map((item) => item.source).join('\n')
if (!js || !css) throw new Error('Missing standalone preview assets')
// Pre-render actual UI: attachment viewers may suppress every script, including
// noscript handling. Visible markup must not depend on JavaScript execution.
const temporary = await mkdtemp(resolve(root, '.tradeview-render-'))
let markup
try {
  const entry = resolve(temporary, 'component.mjs')
  await bundleServer({ entryPoints: [resolve(root, 'src/components/TradeViewExperience.jsx')], outfile: entry, bundle: true, platform: 'node', format: 'esm', jsx: 'automatic', packages: 'external' })
  const { default: Component } = await import(pathToFileURL(entry).href)
  markup = renderToString(createElement(Component))
} finally {
  await rm(temporary, { recursive: true, force: true })
}
await mkdir(out, { recursive: true })
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>Trade View Lite — Interactive sample</title><style>${css}</style></head><body><p id="preview-mode-note" class="bg-[#09111f] p-4 text-white">Trade View Lite visual preview. Interactive controls require opening this file in a browser with JavaScript enabled.</p><div id="root">${markup}</div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`
await writeFile(resolve(out, 'tradeview-lite-v1.html'), html)
await writeFile(resolve(out, 'tradeview-lite-v2.html'), html)
console.log('Exported v1 and v2 previews with visible pre-rendered UI (no network access)')
