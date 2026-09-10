import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..')
const app = readFileSync(join(root, 'src', 'App.vue'), 'utf8')
const sidebar = readFileSync(join(root, 'src', 'components', 'Sidebar.vue'), 'utf8')
const chat = readFileSync(join(root, 'src', 'components', 'ChatArea.vue'), 'utf8')

assert.ok(app.includes("['admin', 'system_admin', 'enterprise_admin'].includes(authUser.value?.role)"), 'App should derive an admin role flag from system and enterprise administrator roles.')
assert.ok(app.includes(':is-admin="isAdmin"'), 'App should pass isAdmin into role-aware child components.')
assert.ok(app.includes("isAdmin.value && route.name === 'dashboard' ? 'dashboard' : 'chat'"), 'Employees should not render dashboard even if they visit /dashboard directly.')
assert.ok(app.includes('v-if="isAdmin"') && app.includes('<DocManager'), 'Document manager drawer should only mount for admins.')

assert.ok(sidebar.includes('isAdmin: { type: Boolean, default: false }'), 'Sidebar should accept an isAdmin prop.')
assert.ok(sidebar.includes('v-if="isAdmin"') && sidebar.includes("@click=\"$emit('show-dashboard')\""), 'Dashboard navigation should be admin-only.')
assert.ok(sidebar.includes('v-if="isAdmin"') && sidebar.includes('class="kb-action primary"'), 'Knowledge-base create action should be admin-only.')
assert.ok(sidebar.includes('v-if="isAdmin"') && sidebar.includes('class="kb-action danger"'), 'Knowledge-base delete action should be admin-only.')
assert.ok(sidebar.includes('v-if="isAdmin"') && sidebar.includes("class=\"conv-del\""), 'Conversation delete button should be admin-only.')
assert.ok(sidebar.includes('v-if="isAdmin"') && sidebar.includes('open-doc-manager'), 'Document manager entry should be admin-only.')
assert.ok(sidebar.includes('class="sidebar user-sidebar"') || sidebar.includes("isAdmin ? 'admin-sidebar' : 'user-sidebar'"), 'Employee sidebar should have a compact role-specific layout class.')

assert.ok(chat.includes('isAdmin: { type: Boolean, default: false }'), 'Chat area should accept an isAdmin prop.')
assert.ok(!chat.includes('interface-popover-trigger'), 'Chat header should not expose interface details.')
assert.ok(!chat.includes('企业级 RAG 工作台'), 'Home empty state should remove the enterprise-level wording.')
assert.ok(chat.includes('RAG 工作台'), 'Home empty state should keep the RAG workbench label.')

console.log('Role UI contract checks passed')
