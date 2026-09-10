import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const viteConfig = readFileSync(join(root, 'vite.config.js'), 'utf8')

assert.match(
  pkg.scripts.dev,
  /\.\.\/scripts\/dev\.ps1/,
  'npm run dev should use the project dev launcher so the backend is checked before Vite starts.',
)

assert.match(
  viteConfig,
  /target:\s*'http:\/\/127\.0\.0\.1:8001'/,
  'Vite proxy should target the backend on 127.0.0.1:8001.',
)

console.log('Dev start contract checks passed')
