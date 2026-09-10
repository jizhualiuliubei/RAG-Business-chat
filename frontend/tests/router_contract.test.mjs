import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..')
const src = join(root, 'src')
const main = readFileSync(join(src, 'main.js'), 'utf8')
const app = readFileSync(join(src, 'App.vue'), 'utf8')
const chat = readFileSync(join(src, 'components', 'ChatArea.vue'), 'utf8')
const api = readFileSync(join(src, 'api', 'api.js'), 'utf8')
const routerPath = join(src, 'router', 'index.js')

assert.ok(existsSync(routerPath), 'Frontend should define a vue-router route table.')
const router = existsSync(routerPath) ? readFileSync(routerPath, 'utf8') : ''

assert.ok(main.includes('app.use(router)'), 'Vue app should install vue-router.')
assert.ok(router.includes("path: '/login'"), 'Router should expose /login.')
assert.ok(router.includes("path: '/home'"), 'Router should expose /home.')
assert.ok(router.includes("path: '/dashboard'"), 'Router should expose /dashboard.')
assert.ok(app.includes("router.replace('/home')"), 'Login success should navigate to /home.')
assert.ok(app.includes("router.replace('/login')"), 'Unauthenticated users should navigate to /login.')
assert.ok(app.includes("route.name === 'dashboard' ? 'dashboard' : 'chat'"), 'Route should drive dashboard/chat view state.')
assert.ok(app.includes('goHome') && app.includes('goDashboard'), 'Sidebar navigation should update routes.')
const selectConversationMatch = app.match(/async function handleSelectConversation\(conv\) \{[\s\S]*?\n\}/)
assert.ok(selectConversationMatch, 'App should define handleSelectConversation.')
assert.ok(selectConversationMatch[0].includes("router.push('/home')"), 'Selecting a recent conversation should navigate back to /home from dashboard.')
assert.ok(!chat.includes('接口状态中心'), 'Home page should not show a permanent interface status center.')
assert.ok(!chat.includes('已认证访问'), 'Home page should not show authenticated-access status copy in the main canvas.')
assert.ok(!chat.includes('interface-popover-trigger'), 'Home page should not expose internal interface details.')
assert.ok(!chat.includes('window.location.origin'), 'Chat header should not compute frontend origin for interface diagnostics.')
assert.ok(!chat.includes('http://127.0.0.1:8001'), 'Chat header should not expose backend target.')
assert.ok(!chat.includes('/api/health'), 'Chat header should not expose health endpoint.')
assert.ok(!chat.includes('/api/qa/ask-stream'), 'Chat header should not expose streaming QA endpoint.')
assert.ok(!chat.includes('/api/knowledge-bases'), 'Chat header should not expose knowledge base endpoint.')
assert.ok(!chat.includes('home-light-beam'), 'Home page should avoid distracting continuous beam animations.')
assert.ok(!chat.includes('chat-input-panel::before'), 'Input panel should not use a moving scan-line decoration.')
assert.ok(api.includes("'/auth/login'") && api.includes("'/qa/ask'"), 'API client should keep the /api proxy prefix convention through axios baseURL.')
assert.ok(!chat.includes('localhost:5174'), 'Home page should not hard-code the old frontend port.')

console.log('Router contract checks passed')
