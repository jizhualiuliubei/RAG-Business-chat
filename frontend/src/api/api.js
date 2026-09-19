// 后端接口封装：把所有 /api 接口封装成函数，组件里调用即可
import http from './index.js'


// ============ 认证 ============
export const getCaptcha = () => http.get('/auth/captcha')
export const login = (payload) => http.post('/auth/login', payload)
export const registerEnterpriseAdmin = (payload) => http.post('/auth/enterprise-register', payload)
// 体验账号注册：免审核，注册完即可登录，但功能受限（不能建子员工、不能用评测）
export const registerEnterpriseTrial = (payload) => http.post('/auth/enterprise-trial-register', payload)
export const getMe = () => http.get('/auth/me')
export const logout = () => http.post('/auth/logout')
// ============ 知识库 ============
export const getKnowledgeBases = () => http.get('/knowledge-bases')
export const createKnowledgeBase = (name) => http.post('/knowledge-bases', { name })
export const updateKnowledgeBase = (id, name) => http.patch(`/admin/knowledge-bases/${id}`, { name })
export const deleteKnowledgeBase = (id) => http.delete(`/knowledge-bases/${id}`)

// ============ 概览统计 ============
export const getStatsSummary = () => http.get('/stats/summary')

// ============ RAG 评测 ============
export const listEvaluationDatasets = () => http.get('/evaluation-datasets')
export const getEvaluationDataset = (version) => http.get(`/evaluation-datasets/${version}`)
export const uploadEvaluationDataset = (file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post('/evaluation-datasets/upload', form)
}
export const downloadDatasetTemplate = () =>
  http.get('/evaluation-datasets/upload-template', { responseType: 'blob' })
export const approveEvaluationDataset = (version) => http.post(`/evaluation-datasets/${version}/approve`)
export const deleteEvaluationDataset = (version) => http.delete(`/evaluation-datasets/${version}`)
export const updateEvaluationDatasetCase = (version, caseId, payload) =>
  http.patch(`/evaluation-datasets/${version}/cases/${caseId}`, payload)
export const downloadEvaluationDataset = (version, format = 'csv') =>
  http.get(`/evaluation-datasets/${version}/download`, { params: { format }, responseType: 'blob' })
export const createEvaluationRun = (payload) => http.post('/evaluation-runs', payload)
export const listEvaluationRuns = () => http.get('/evaluation-runs')
export const getEvaluationRun = (id) => http.get(`/evaluation-runs/${id}`)
export const cancelEvaluationRun = (id) => http.post(`/evaluation-runs/${id}/cancel`)
export const deleteEvaluationRun = (id) => http.delete(`/evaluation-runs/${id}`)
export const deleteEvaluationRuns = (ids) => http.delete('/evaluation-runs', { data: { run_ids: ids } })
export const listEvaluationRunCases = (id, params = {}) => http.get(`/evaluation-runs/${id}/cases`, { params })
export const generateEvaluationReport = (id) => http.post(`/evaluation-runs/${id}/report`)
export const downloadEvaluationReport = async (id) => {
  const report = await generateEvaluationReport(id)
  return new Blob([report.markdown || ''], { type: 'text/markdown;charset=utf-8' })
}

// ============ 管理员后台 ============
export const getAdminOverview = () => http.get('/admin/overview')
export const getAdminUsers = () => http.get('/admin/users')
export const createAdminUser = (payload) => http.post('/admin/users', payload)
export const updateAdminUser = (id, payload) => http.patch(`/admin/users/${id}`, payload)
export const updateAdminUserStatus = (id, status) => http.patch(`/admin/users/${id}/status`, { status })
export const deleteAdminUser = (id) => http.delete(`/admin/users/${id}`)
export const getAdminKnowledgeOverview = () => http.get('/admin/knowledge-overview')
export const getAdminAuditLogs = () => http.get('/admin/audit-logs')

// ============ SaaS 企业管理 ============
export const listSystemEnterprises = (params = {}) => http.get('/system/enterprises', { params })
export const getSystemEnterprise = (id) => http.get(`/system/enterprises/${id}`)
export const createSystemEnterprise = (payload) => http.post('/system/enterprises', payload)
export const updateSystemEnterprise = (id, payload) => http.patch(`/system/enterprises/${id}`, payload)
export const deleteSystemEnterprise = (id) => http.delete(`/system/enterprises/${id}`)
export const approveEnterprise = (id) => http.post(`/system/enterprises/${id}/approve`)
// 体验企业转正式：解除「不能建子员工、不能用评测」两个限制
export const promoteEnterprise = (id) => http.post(`/system/enterprises/${id}/promote`)
export const rejectEnterprise = (id, reason) => http.post(`/system/enterprises/${id}/reject`, { reason })
export const updateEnterpriseStatus = (id, status) => http.patch(`/system/enterprises/${id}/status`, { status })
export const getEnterpriseSettings = () => http.get('/enterprise/settings')
export const createEnterpriseEmployee = (payload) => http.post('/enterprise/users', payload)
export const saveEnterpriseModelKeys = (payload) => http.put('/enterprise/model-keys', payload)
export const testEnterpriseModelKeys = (payload) => http.post('/enterprise/model-keys/test', payload)

// ============ 文档 ============
// 上传文档（FormData），kb_id 指定上传到哪个知识库
// onProgress：上传进度回调（0-100 的 percent），用于进度条实时显示
export const uploadDocument = (file, kbId, onProgress) => {
  const form = new FormData()
  form.append('file', file)
  return http.post(`/documents/upload?kb_id=${kbId}`, form, {
    onUploadProgress: (evt) => {
      if (onProgress && evt.total) {
        onProgress(Math.round((evt.loaded / evt.total) * 100))
      }
    },
  })
}
export const listDocuments = (kbId) => http.get(`/documents?kb_id=${kbId}`)
export const deleteDocument = (id) => http.delete(`/documents/${id}`)
export const deleteDocuments = (ids) => http.delete('/documents/batch', { data: { document_ids: ids } })
// 查询某文档的全部 chunk（概览页文档下钻）
export const getDocChunks = (docId) => http.get(`/documents/${docId}/chunks`)

// ============ 问答 ============
// 手动检索问答（返回 answer + sources + hit）
export const askQuestion = (question, kbId, conversationId) =>
  http.post('/qa/ask', { question, kb_id: kbId, conversation_id: conversationId })

// 流式问答（SSE）：用 fetch 直接读流，逐个回调事件
// onSources: 收到引用来源时回调；onText: 每段文本回调
// signal: AbortController 的 signal，用于"取消生成"（思考中点击取消按钮）
// conversationId: 会话 id，后端用它读历史消息做上下文记忆
// kbId: 知识库 id 或 id 数组（多库联合检索）
// 返回 Promise，流结束时 resolve
export const askStream = (question, kbId, { onSources, onText, signal, conversationId }) => {
  return fetch('/api/qa/ask-stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(localStorage.getItem('kb_auth_token') ? { Authorization: `Bearer ${localStorage.getItem('kb_auth_token')}` } : {}),
    },
    body: JSON.stringify({ question, kb_id: kbId, conversation_id: conversationId ?? null }),
    signal,   // 传入后可中断
  }).then(async (response) => {
    if (!response.ok || !response.body) {
      throw new Error('流式请求失败')
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    while (true) {
      let value
      try {
        const { done, value: v } = await reader.read()
        if (done) break
        value = v
      } catch (err) {
        // 中断（abort）时 reader.read 抛异常，静默退出循环
        if (err.name === 'AbortError') break
        throw err
      }
      buffer += decoder.decode(value, { stream: true })
      // SSE 事件用 \n\n 分隔，按此切分处理
      const events = buffer.split('\n\n')
      buffer = events.pop()   // 最后一段可能不完整，留到下次
      for (const evt of events) {
        const line = evt.replace(/^data: /, '').trim()
        if (!line) continue
        const data = JSON.parse(line)
        if (data.type === 'sources' && onSources) onSources(data)
        if (data.type === 'text' && onText) onText(data.content)
      }
    }
  })
}

// ============ 会话 ============
export const listConversations = () => http.get('/conversations')
export const createConversation = () => http.post('/conversations', { title: '新会话' })
export const getConversationMessages = (id) => http.get(`/conversations/${id}/messages`)
// 会话内提问（带多轮记忆，返回更新后的完整会话）
export const askInConversation = (id, question, kbId) =>
  http.post(`/conversations/${id}/ask`, { question, kb_id: kbId })
// 仅保存一轮问答（不重新生成）：前端流式已生成好回答，这里只持久化
export const saveConversationTurn = (id, { question, answer, kbId, sources, modelName, usage }) =>
  http.post(`/conversations/${id}/save-turn`, {
    question, answer, kb_id: kbId,
    sources: sources || [],
    model_name: modelName || '',
    usage: usage || {},
  })
export const deleteConversation = (id) => http.delete(`/conversations/${id}`)

// ============ 会话附件（对话中上传，会话级临时上下文） ============
export const uploadAttachment = (conversationId, file) => {
  const form = new FormData()
  form.append('file', file)
  return http.post(`/conversations/${conversationId}/attachments`, form)
}
export const listAttachments = (conversationId) => http.get(`/conversations/${conversationId}/attachments`)
export const deleteAttachment = (conversationId, attachmentId) =>
  http.delete(`/conversations/${conversationId}/attachments/${attachmentId}`)
