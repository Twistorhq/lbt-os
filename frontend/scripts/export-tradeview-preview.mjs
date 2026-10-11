// Bundle the same React component for keyless, offline review. No app auth bundle.
import { build } from 'vite'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
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
await mkdir(out, { recursive: true })
const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>Trade View Lite — Interactive sample</title><style>${css}</style></head><body><div id="root"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`
await writeFile(resolve(out, 'tradeview-lite-v1.html'), html)
console.log('Exported docs/previews/tradeview-lite-v1.html (no network access)')
