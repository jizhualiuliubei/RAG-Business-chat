import { readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = process.cwd()
const src = join(root, 'src')
const router = readFileSync(join(src, 'router/index.js'), 'utf8')
const app = readFileSync(join(src, 'App.vue'), 'utf8')
const api = readFileSync(join(src, 'api/api.js'), 'utf8')
const login = readFileSync(join(src, 'components/LoginView.vue'), 'utf8')
const adminPath = join(src, 'components/AdminView.vue')

assert.ok(router.includes("path: '/admin'"), 'Router should expose an independent /admin page.')
assert.ok(router.includes("name: 'admin'"), 'Admin route should have the admin route name.')
assert.ok(app.includes('AdminView'), 'App should mount the standalone admin console component.')
assert.ok(app.includes("route.name === 'admin'"), 'App should render admin layout from the admin route.')
assert.ok(app.includes('@refresh-kbs="handleAdminKnowledgeRefresh"'), 'Admin knowledge changes should refresh the workspace knowledge-base list without a page reload.')
assert.ok(app.includes('async function handleAdminKnowledgeRefresh'), 'App should handle knowledge-base refresh events from AdminView.')
assert.ok(app.includes('管理中心') && app.includes('v-if="isAdmin"'), 'Only admins should see the management center entry.')
assert.ok(app.includes("router.push('/admin')") || app.includes("router.replace('/admin')"), 'Admin entry should navigate to /admin.')
assert.ok(existsSync(adminPath), 'AdminView.vue should exist.')

const admin = readFileSync(adminPath, 'utf8')

for (const label of ['企业知识库后台管理台', '总览', '用户管理', '知识库治理', 'RAG 评测', '操作审计']) {
  assert.ok(admin.includes(label), `Admin console should include ${label}.`)
}

for (const label of ['企业管理', '新建企业', '编辑企业', '删除企业', '平台模型配置', '企业代码']) {
  assert.ok(admin.includes(label), `SaaS admin console should include ${label}.`)
}

for (const label of ['搜索企业', '状态筛选', '检验连接', '企业名称为必填项', '企业代码为必填项']) {
  assert.ok(admin.includes(label), `Enterprise governance should include ${label}.`)
}

for (const label of ['筛选状态', 'DeepSeek 对话模型', 'SiliconFlow 嵌入模型', 'Base URL', '模型名称', '当前企业代码', '当前企业名称']) {
  assert.ok(admin.includes(label), `Enterprise admin forms should include ${label}.`)
}

for (const label of ['员工归属企业', '企业名称', '企业代码', '编辑员工', '删除员工', '保存员工']) {
  assert.ok(admin.includes(label), `Create employee form should include ${label}.`)
}

for (const label of ['企业管理员账号', '管理员账号', '管理员姓名', '初始密码']) {
  assert.ok(admin.includes(label), `System enterprise creation should include ${label}.`)
}

for (const label of ['展开', '收起', '编辑名称', '保存名称']) {
  assert.ok(admin.includes(label), `Knowledge governance should include ${label}.`)
}

for (const label of ['创建员工', '启用', '禁用', '返回工作台']) {
  assert.ok(admin.includes(label), `Admin user workflows should include ${label}.`)
}

assert.ok(!admin.includes('注册'), 'Admin console must not expose self-registration wording.')
assert.ok(admin.includes('runEvaluation'), 'Admin console should reuse the RAG evaluation runner.')
assert.ok(admin.includes('全部知识库'), 'Admin evaluation should allow running across all knowledge bases.')
assert.ok(admin.includes('selectedEvaluationKbId'), 'Admin evaluation should track selected evaluation knowledge base.')
assert.ok(admin.includes('evaluationKbPayload'), 'Admin evaluation should send either one kb id or all kb ids.')
assert.ok(admin.includes('未运行'), 'Admin evaluation should not show skipped refusal cases as a fake 100% pass rate.')
assert.ok(admin.includes('selectedEvaluationRun.value?.summary'), 'Admin evaluation should read metrics from persistent run summaries.')
assert.ok(!admin.includes('evaluationResult?.metrics?.retrieval_pass_rate'), 'Admin evaluation should not read a non-existent nested metrics object.')
assert.ok(admin.includes('top1_doc_hit_rate') && admin.includes('top5_doc_hit_rate'), 'Admin evaluation should show Top-K hit rates after running.')
assert.ok(admin.includes('selectedEvaluationRun.summary.categories'), 'Admin evaluation should render category-level results from run summaries.')
assert.ok(admin.includes('RAG 评测中心'), 'Admin evaluation should use the persistent evaluation center title.')
assert.ok(admin.includes('selectedEvaluationRun'), 'Admin evaluation should track the selected persistent run.')
assert.ok(admin.includes('evaluationMetrics'), 'Admin evaluation should show reusable metric cards.')
assert.ok(admin.includes('caseFilters'), 'Admin evaluation should support case filters.')
assert.ok(admin.includes('handleGenerateReport'), 'Admin evaluation should expose Markdown report generation.')
assert.ok(admin.includes('getAdminOverview'), 'Admin console should load admin overview data.')
assert.ok(admin.includes('createAdminUser'), 'Admin console should create employee accounts through API.')
assert.ok(admin.includes('updateAdminUserStatus'), 'Admin console should update account status through API.')
assert.ok(admin.includes('getAdminAuditLogs'), 'Admin console should render audit logs.')
assert.ok(admin.includes('expandedKbIds'), 'Knowledge bases should support collapsible document details.')
assert.ok(admin.includes('toggleKbExpanded'), 'Knowledge governance should expose a collapse toggle handler.')
assert.ok(admin.includes('editingKbId'), 'Knowledge governance should support editing a knowledge base name.')
assert.ok(admin.includes('updateKnowledgeBase'), 'Knowledge governance should call the rename API.')
assert.ok(admin.includes('multiple'), 'Knowledge governance upload should allow selecting multiple files.')
assert.ok(admin.includes('deleteDocuments'), 'Knowledge governance should call the batch document delete API.')
assert.ok(admin.includes('selectedAdminDocIds'), 'Knowledge governance should track selected documents for batch delete.')
assert.ok(admin.includes('批量删除'), 'Knowledge governance should expose a batch delete action.')
assert.ok(admin.includes('startKnowledgePolling'), 'Knowledge governance should poll while documents are processing.')
assert.ok(admin.includes("defineEmits(['back-to-home', 'refresh-kbs'])"), 'AdminView should emit refresh-kbs after knowledge-base changes.')
assert.ok(admin.includes("emit('refresh-kbs'"), 'AdminView should notify App when knowledge bases are created, renamed, or deleted.')
assert.ok(admin.includes('kbProgressPercent'), 'Knowledge governance should calculate a knowledge-base parsing progress percentage.')
assert.ok(admin.includes('kb-progress-bar'), 'Knowledge governance should render a status progress bar for each knowledge base.')
assert.ok(admin.includes('docStatusPercent'), 'Knowledge governance should render document-level progress by status.')
assert.ok(admin.includes('解析进度'), 'Knowledge governance progress UI should be clearly labeled in Chinese.')
assert.ok(admin.includes('failure_reason'), 'Knowledge governance should render backend document failure reasons.')
assert.ok(admin.includes('失败原因'), 'Knowledge governance should label document failure reasons in Chinese.')
assert.ok(admin.includes('grid-template-columns: minmax(116px, 146px) minmax(0, 1fr) 142px'), 'Recent compact audit rows should reserve flexible target width.')
assert.ok(admin.includes('.audit-item time'), 'Recent audit timestamps should be isolated to avoid text overlap.')
assert.ok(admin.includes('auditActionLabel'), 'Audit action names should have a Chinese frontend fallback label.')
assert.ok(admin.includes('enterpriseSearch'), 'Enterprise governance should track a search keyword.')
assert.ok(admin.includes('validateEnterpriseForm'), 'Enterprise form should validate before submit.')
assert.ok(admin.includes('testModelKeys'), 'Model key settings should expose a connection test handler.')
assert.ok(admin.includes('key-test-result'), 'Model key settings should render connection test results.')
assert.ok(admin.includes('modelKeyDefaults'), 'Model key settings should expose saved provider defaults.')
assert.ok(admin.includes('native-type="button"') && admin.includes(':loading="testingModelKeys"'), 'Testing model keys should not submit or save the form.')
assert.ok(admin.includes('enterprise-toolbar-search'), 'Enterprise search should have a compact fixed-width area.')
assert.ok(admin.includes('enterprise-row-actions'), 'Enterprise row actions should use a single aligned action group.')
assert.ok(admin.includes('user-row-actions'), 'User row actions should use a single aligned action group.')
assert.ok(admin.includes('activeEnterpriseOptions'), 'System admins should choose an active enterprise when creating employees.')
assert.ok(admin.includes('newUser.enterprise_code'), 'Employee creation should send the selected enterprise code.')
assert.ok(admin.includes('updateAdminUser'), 'Admin console should update employee accounts through API.')
assert.ok(admin.includes('deleteAdminUser'), 'Admin console should delete employee accounts through API.')
assert.ok(admin.includes('normalizeProviderBaseUrl'), 'Model key forms should normalize known provider Base URLs before save and test.')
assert.ok(!admin.includes('label="主题色"'), 'Enterprise management should not expose the unused theme color field.')

for (const label of ['选填', '联系手机号', '管理员用户名', '管理员显示名']) {
  assert.ok(login.includes(label), `Enterprise registration should clearly mark ${label}.`)
}
assert.ok(login.includes('<b class="required-mark">*</b>'), 'Enterprise registration should use * for required fields.')
assert.ok(login.includes('validateEnterpriseRegisterForm'), 'Enterprise registration should validate before submit.')
assert.ok(login.includes('密码至少需要 6 位'), 'Enterprise registration should reject short passwords on the client.')

for (const fn of [
  'getAdminOverview',
  'getAdminUsers',
  'createAdminUser',
  'updateAdminUser',
  'updateAdminUserStatus',
  'deleteAdminUser',
  'getAdminKnowledgeOverview',
  'getAdminAuditLogs',
  'updateKnowledgeBase',
  'deleteDocuments',
  'deleteEvaluationDataset',
  'createSystemEnterprise',
  'updateSystemEnterprise',
  'deleteSystemEnterprise',
  'getSystemEnterprise',
  'testEnterpriseModelKeys',
]) {
  assert.ok(api.includes(`export const ${fn}`), `api.js should export ${fn}.`)
}

console.log('admin ui contract ok')
