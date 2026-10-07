export const fieldLabels = {
  purpose: '采购用途', items: '采购明细', estimated_amount: '预计金额', budget_code: '预算编号', vendor: '供应商', expected_date: '期望日期',
  quotation_attached: '报价材料解析状态', involves_sensitive_data: '涉及敏感数据', target_system: '目标系统', resource: '目标资源',
  permission_level: '权限级别', data_classification: '数据密级', duration_days: '申请天数', business_reason: '业务理由',
  approval_basis: '审批依据', blocking_issues: '阻断问题', conflict_check_note: '冲突检查说明', evidence_boundary: '证据边界',
  approval_required: '需要人工审批', requires_approval: '需要人工审批', required_changes: '执行前修订项', policy_basis: '制度依据',
  expected_result: '预期结果', record_type: '记录类型', record_id: '记录编号', status: '状态',
  draft_pending_quotation: '待补报价材料的草稿', create_procurement_record: '创建采购申请记录', create_access_record: '创建权限登记记录',
  procurement: '采购申请', access_request: '系统权限申请', pending_approval: '待审批', approved: '已通过', completed: '已完成',
  access_grant: '权限登记', true: '是', false: '否', low: '低风险', medium: '中风险', high: '高风险',
  internal: '内部', public: '公开', secret: '秘密', confidential: '机密', read: '只读', write: '读写', admin: '管理员',
  internal_registration: '内部申请登记', not_executed: '尚未实际执行', review_notes: '办理说明',
  evidence_sufficient: '依据是否充分', execution_scope: '办理范围', business_execution_status: '实际业务状态',
}

export function localizeText(value = '') {
  return String(value).replace(/\b[a-z][a-z0-9_]*\b/g, key => fieldLabels[key] || key)
}

export function conciseText(value, limit = 140) {
  if (value === null || value === undefined) return ''
  let text = String(value)
  try {
    const data = JSON.parse(text)
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      text = Object.entries(data).filter(([key]) => fieldLabels[key]).map(([key, item]) => {
        const content = typeof item === 'boolean' ? (item ? '是' : '否') : Array.isArray(item) ? item.join('；') : typeof item === 'object' ? '' : item ?? ''
        return `${fieldLabels[key]}：${content}`
      }).join('；')
    }
  } catch { /* Ordinary model prose is not JSON. */ }
  text = localizeText(text).replace(/```[\s\S]*?```/g, '').replace(/^[\s]*#{1,6}\s+/gm, '').replace(/[*`]/g, '').replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

export function explanationItems(values = [], label = '复核说明') {
  const seen = new Set()
  return values.filter(value => typeof value === 'string' && value.trim()).flatMap(value => {
    const text = localizeText(value).replace(/```(?:[a-z]+)?\n?/gi, '').replace(/\*\*([^]*?)\*\*/g, '$1').replace(/`/g, '')
      .replace(/^\s*#{1,6}\s+/gm, '').trim()
    if (seen.has(text)) return []
    seen.add(text)
    const heading = text.match(/^([^\n：:。；！？]{2,30})[：:]\s*/)
    const body = heading ? text.slice(heading[0].length) : text
    const paragraphs = body.split(/\n+|(?<=[。！？])\s*/u).map(part => part.trim()).filter(Boolean)
    return [{ title: heading?.[1] || `${label} ${seen.size}`, preview: paragraphs[0] || body,
      paragraphs, original: value }]
  })
}

export function planPresentation(task = {}) {
  const contract = task.plan?.execution_contract || {}
  const verifiedContract = contract.version === 1 && contract.scope === 'internal_registration'
  if (!verifiedContract) return { verifiedContract: false, steps: [], policyChecks: [], boundary: '这份历史计划缺少办理范围说明，不能据此认定采购或授权已完成。' }
  const noun = task.skill_id === 'access_request' ? '权限申请' : '采购申请'
  const first = needsManualReview(task)
    ? { title: '先处理复核问题', detail: '请企业管理员退回补充或拒绝。复核通过前，不能批准登记。' }
    : contract.requires_approval
      ? { title: '先完成复核和审批', detail: '方案复核通过后，由本企业另一位企业管理员审批。' }
      : { title: '先完成复核', detail: '只有规则检查和方案复核通过后，系统才会继续办理。' }
  const labels = { blocked: '需修改后重提', matched: '仅此项条件匹配', pending_business_verification: '业务审批尚待核验', unverified: '尚待核验' }
  const policyChecks = (contract.policy_assessment?.checks || []).filter(item => item.applicable === true)
    .map(item => ({ ...item, label: labels[item.status] || '尚待核验' }))
  return { verifiedContract, policyChecks, steps: [first,
    { title: '保存申请', detail: `通过检查${contract.requires_approval ? '和审批' : ''}后，系统保存本次${noun}，并记录办理依据。` },
    { title: '生成档案', detail: '核对保存结果，生成可下载的执行档案，保留制度引用与审批记录。' }],
    outcome: `一条内部${noun}记录和一份执行档案。`,
    boundary: task.skill_id === 'access_request'
      ? '这里登记的是权限申请，不会修改目标系统的权限，也不会自动回收权限。实际授权仍需按制度办理。'
      : '这里登记的是采购申请，不会下单、付款或代做供应商评估。制度要求的业务审批和检查仍需另行完成。' }
}

export function calculateAmount(items = []) {
  if (!items.length || items.some(item => item.unit_price === null || item.unit_price === undefined || item.unit_price === '' ||
    !Number.isFinite(Number(item.unit_price)) || Number(item.unit_price) < 0 || !Number.isInteger(Number(item.quantity)) || Number(item.quantity) < 1)) return null
  const cents = items.reduce((sum, item) => sum + Math.round(Number(item.unit_price) * 100) * Number(item.quantity), 0)
  return Number.isSafeInteger(cents) ? cents / 100 : null
}

export function approvalMessage(task) {
  return task?.approval_context?.reason || '等待其他企业管理员审批'
}

export function isAuditPassed(audit = {}) {
  return audit.approved === true && audit.evidence_sufficient === true && !audit.required_changes?.length
}

export function needsManualReview(task = {}) {
  return task.status === 'waiting_approval' && Boolean(Object.keys(task.audit || {}).length) && !isAuditPassed(task.audit)
}

export function canReviewAgent(user = {}) {
  return user.role === 'enterprise_admin' || (['system_admin', 'admin'].includes(user.role) && user.enterprise_code === 'system')
}

export function registrationNotice(result = {}) {
  return result.execution_scope === 'internal_registration'
    ? '内部登记完成，实际采购、付款或外部系统授权尚未执行；制度审批与业务检查仍需核验。'
    : '历史记录未区分登记与业务执行范围，不能据此认定采购或授权已实际完成。'
}

export function nodeLabel(node) {
  return ({ load_context: '加载任务上下文', route_skill: '选择业务 Skill', extract_fields: '抽取申请信息', validate_fields: '校验申请信息',
    wait_for_input: '等待核对申请与材料', input_confirmation: '申请信息已确认', materials_updated: '任务材料已更新', retrieve_policy: '检索企业制度', build_plan: '生成执行计划', deterministic_checks: '校验风险规则',
    compliance_audit: '合规复核', revise_plan: '修订执行计划', manual_review: '自动修订未通过，转人工处理', approval_gate: '检查审批要求', wait_for_approval: '等待企业管理员审批',
    execute_tools: '写入内部记录', verify_result: '核验执行结果', build_artifact: '生成执行档案', finalize: '任务完成',
    // 模型自主调用检索工具留下的轨迹（每次检索一条）
    policy_search: '自主检索制度',
    policy_search_degraded: '自主检索失败（已降级）',
    skill_doc_missing: '未加载到 Skill 规范',
    created: '任务已创建', input_updated: '申请已提交', retry_requested: '已提交恢复请求', approval_resumed: '审批已通过，等待恢复',
    approval_changes_requested: '审批退回补充', approval_rejected: '审批已拒绝', mark_cancelled: '结束任务', resume: '恢复执行' }[node] || '处理任务')
}
