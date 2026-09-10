import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..')
const src = join(root, 'src')

const app = readFileSync(join(src, 'App.vue'), 'utf8')
const sidebar = readFileSync(join(src, 'components', 'Sidebar.vue'), 'utf8')
const chatArea = readFileSync(join(src, 'components', 'ChatArea.vue'), 'utf8')
const messageBubble = readFileSync(join(src, 'components', 'MessageBubble.vue'), 'utf8')
const dashboard = readFileSync(join(src, 'components', 'Dashboard.vue'), 'utf8')
const styles = readFileSync(join(src, 'assets', 'styles.css'), 'utf8')

for (const asset of [
  'kb-hero-image2.png',
]) {
  assert.ok(
    existsSync(join(src, 'assets', 'visuals', asset)),
    `Image2 asset should be bundled in src/assets/visuals/${asset}`,
  )
}

assert.ok(app.includes('enterprise-shell'), 'App shell should use the enterprise SaaS layout.')
assert.ok(app.includes('AI 企业知识库工作台'), 'Primary product wording should be Simplified Chinese.')
assert.ok(app.includes('brand-letter">W'), 'Top shell should use a minimal W mark.')
assert.ok(app.includes('grid-template-columns: minmax(190px, 1fr) auto'), 'Top bar should keep a compact two-zone SaaS layout.')
assert.ok(app.includes('padding: 0 22px'), 'Top bar should use compact horizontal padding.')
assert.ok(!app.includes('command-center'), 'Top shell should not show an unavailable search box.')
assert.ok(!app.includes('account-button'), 'Top shell should not show the old interview-demo account button.')
assert.ok(!app.includes('workbench-mark-image2'), 'Top shell should not use the rejected Image2 logo mark.')

assert.ok(chatArea.includes('kb-hero-image2.png'), 'Chat empty state should use the Image2 hero visual.')
assert.ok(chatArea.includes('多知识库联合检索'), 'Chat surface should emphasize multi-KB retrieval in Chinese.')
assert.ok(chatArea.includes('知识库隔离、引用溯源、长期记忆和流式问答'), 'Empty copy should describe real product value.')
assert.ok(!chatArea.includes('suggest-grid'), 'Empty state should not use large preset prompt cards.')

assert.ok(messageBubble.includes('evidence-dock'), 'Assistant messages should expose a premium evidence dock.')
assert.ok(messageBubble.includes('引用证据链'), 'Evidence panel title should be Simplified Chinese.')
assert.ok(messageBubble.includes('max-width: 74%'), 'Message bubbles should stay compact instead of oversized.')
assert.ok(!messageBubble.includes('evidence-visual'), 'Evidence dock should not include a left-side image block.')
assert.ok(!messageBubble.includes('evidence-graph-image2.png'), 'Evidence dock should not load the bulky graph image.')
assert.ok(messageBubble.includes('evidence-metric'), 'Evidence dock should present compact text metrics.')

assert.ok(sidebar.includes('create-kb-popover'), 'Create knowledge-base form should fit inside the sidebar.')
assert.ok(sidebar.includes('kb-action-row'), 'Knowledge-base controls should use a compact two-row layout.')
assert.ok(sidebar.includes('kb-multi-overlay'), 'Multi knowledge-base selection should use a compact custom summary.')
assert.ok(sidebar.includes('selectedKbDetail'), 'Sidebar should show selected knowledge-base names outside the cramped select control.')
assert.ok(sidebar.includes('width: 264px'), 'Sidebar should use the current breathable workbench width.')
assert.ok(!sidebar.includes('capability-strip'), 'Sidebar should not show non-actionable capability chips.')

assert.ok(chatArea.includes('min-height: 68px'), 'Chat header should keep the current breathable compact height.')
assert.ok(chatArea.includes('min-height: 76px'), 'Question input should stay comfortable without dominating the viewport.')
assert.ok(dashboard.includes('知识治理驾驶舱'), 'Dashboard should use a higher-end Chinese SaaS title.')
assert.ok(styles.includes('--kb-graphite'), 'Global design system should define graphite token.')
assert.ok(styles.includes('--kb-accent-teal'), 'Global design system should define teal accent token.')
assert.ok(styles.includes('@keyframes kb-float-in'), 'Global design system should include refined motion.')

const productFiles = [app, sidebar, chatArea, messageBubble, dashboard].join('\n')
assert.ok(!/面试|演示/.test(productFiles), 'Product UI should not contain interview/demo wording.')

console.log('Visual contract checks passed')
