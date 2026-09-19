<!--
  ChatArea.vue：中间对话区
  ┌──────────────────────────────┐
  │  顶部：当前会话标题            │
  │  ─────────────────────────  │
  │  消息列表（可滚动）            │
  │  ├ 用户消息（右对齐深蓝气泡）   │
  │  └ AI 消息（左对齐白卡片）     │
  │  ─────────────────────────  │
  │  底部：输入框 + 发送按钮       │
  └──────────────────────────────┘
-->
<template>
  <main class="chat-area">
    <!-- 顶部标题栏 -->
    <header class="chat-header">
      <div>
        <h2 class="chat-title">{{ currentConversation?.title || '新对话' }}</h2>
      </div>
      <div class="chat-header-meta">
        <span class="meta-pill">结构化混合检索</span>
        <span class="meta-pill evidence">引用溯源开启</span>
      </div>
    </header>

    <!-- 消息列表 -->
    <div ref="listRef" class="message-list" :class="{ 'is-empty': messages.length === 0 }">
      <!-- 空状态：还没消息时提示 -->
      <div v-if="messages.length === 0" class="empty-hint">
        <div class="empty-visual-card">
          <img src="../assets/visuals/kb-hero-image2.png" alt="AI 企业知识库工作台视觉" />
          <div class="empty-visual-badge">
            <span class="live-dot"></span>
            <span>{{ selectedKbNames }}</span>
          </div>
        </div>
        <div class="empty-copy">
          <span class="empty-kicker">RAG 工作台</span>
          <h3>基于结构化混合检索与精排，输出可溯源的引用答案。</h3>
          <p>将知识库隔离、引用溯源、长期记忆和流式问答整合到一个统一工作台，帮助团队从企业文档中获得可靠答案。</p>
          <div class="capability-row">
            <span>混合检索</span>
            <span>引用证据链</span>
            <span>长期记忆</span>
          </div>
          <div class="ai-signal-grid">
            <div>
              <strong>召回排序</strong>
              <span>多路召回与来源排序</span>
            </div>
            <div>
              <strong>引用证据</strong>
              <span>回答绑定文档片段</span>
            </div>
            <div>
              <strong>租户隔离</strong>
              <span>企业知识隔离</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 逐条渲染消息 -->
      <MessageBubble
        v-for="(msg, idx) in messages"
        :key="msg.id || idx"
        :message="msg"
        :is-generating="loading && idx === messages.length - 1 && msg.role === 'assistant'"
      />

      <!-- 正在等待 AI 回答的加载提示 -->
      <div v-if="loading" class="loading-bubble">
        <el-icon class="is-loading"><Loading /></el-icon>&nbsp;正在思考...
      </div>
    </div>

    <!-- 底部输入区：升级为真正的 AI 主操作区 -->
    <footer class="chat-input-shell">
      <div class="chat-input-panel">
        <!-- 顶部状态条：当前知识库 / 当前会话 / AI状态 -->
        <div class="input-topbar">
          <div class="input-badges">
            <span class="input-badge primary">知识库：{{ selectedKbIds.length ? currentKb?.name : '请选择知识库' }}</span>
            <span class="input-badge">会话：{{ currentConversation?.title || '新对话' }}</span>
          </div>
          <div class="input-status" :class="{ busy: loading }">
            <el-icon v-if="loading" class="is-loading"><Loading /></el-icon>
            <span>{{ inputStatusText }}</span>
          </div>
        </div>

        <div class="composer-body">
          <!-- 已挂载附件：当前会话临时 RAG 资料 -->
          <div v-if="attachments.length" class="attachment-strip">
            <div class="attach-list">
              <div
                v-for="att in attachments"
                :key="att.id"
                class="attach-chip"
                :class="`is-${att.status || 'done'}`"
              >
                <div class="attach-icon">
                  <el-icon><Document /></el-icon>
                </div>
                <div class="attach-content">
                  <div class="attach-main">
                    <span class="attach-name">{{ att.filename }}</span>
                    <span class="attach-state">{{ attachmentStatusText(att) }}</span>
                  </div>
                  <div v-if="att.summary || att.chunk_count" class="attach-summary">
                    <span v-if="att.summary">{{ att.summary }}</span>
                    <span v-if="att.chunk_count" class="chunk-count">{{ att.chunk_count }} 个切片</span>
                  </div>
                </div>
                <button class="attach-remove" type="button" title="移除附件" @click="handleRemoveAttachment(att)">
                  <el-icon><Close /></el-icon>
                </button>
              </div>
            </div>
          </div>

          <!-- 中部输入主体 -->
          <div class="chat-input-bar">
            <el-input
              v-model="inputText"
              type="textarea"
              :rows="3"
              :placeholder="hasSelectedKb ? '输入你的问题…' : '请先选择知识库再提问'"
              resize="none"
              @keydown.enter.exact.prevent="handleSend"
            >
            </el-input>
          </div>

          <div class="composer-toolbar">
            <div class="toolbar-left">
              <el-upload
                :show-file-list="false"
                :before-upload="handleUploadAttachment"
                accept=".txt,.pdf,.docx,.csv,.xlsx,.xls"
              >
                <el-button class="attach-btn" title="上传附件">
                  <el-icon><Paperclip /></el-icon>
                  上传附件
                </el-button>
              </el-upload>
              <span class="attach-hint">支持 PDF、Word、Excel、CSV、TXT，作为本会话临时上下文</span>
            </div>
            <div class="input-actions">
              <el-button
                v-if="!loading"
                type="primary"
                :disabled="!hasSelectedKb || loading"
                @click="handleSend"
                class="send-btn"
              >
                发送问题
              </el-button>
              <el-button
                v-else
                type="danger"
                plain
                @click="$emit('cancel')"
                class="send-btn"
              >
                <el-icon><Close /></el-icon>&nbsp;取消
              </el-button>
            </div>
          </div>
        </div>

        <!-- 底部键盘提示 -->
        <div class="input-footer-hint">
          <span>Enter 发送</span>
          <span class="dot">·</span>
          <span>Shift + Enter 换行</span>
          <span class="dot">·</span>
          <span>{{ loading ? '点击取消可中断生成' : '支持多轮对话与长期记忆' }}</span>
        </div>
      </div>
    </footer>
  </main>
</template>

<script setup>
import { computed, ref, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import MessageBubble from './MessageBubble.vue'
import { uploadAttachment, listAttachments, deleteAttachment } from '../api/api.js'

const props = defineProps({
  currentConversation: { type: Object, default: null },
  currentKb: { type: Object, default: null },
  selectedKbIds: { type: Array, default: () => [] },
  kbList: { type: Array, default: () => [] },
  messages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  isAdmin: { type: Boolean, default: false },
})
const emit = defineEmits(['send', 'cancel'])

const inputText = ref('')
const listRef = ref(null)   // 消息列表 DOM 引用（用于滚动到底部）
const attachments = ref([]) // 当前会话的附件列表

const hasSelectedKb = computed(() => props.selectedKbIds.length > 0)
const hasProcessingAttachments = computed(() => attachments.value.some((att) => att.status === 'processing'))
const selectedKbIds = computed(() => props.selectedKbIds)
const selectedKbNames = computed(() => {
  const ids = props.selectedKbIds || []
  const names = props.kbList.filter((kb) => ids.includes(kb.id)).map((kb) => kb.name)
  if (names.length === 0) return '请选择知识库'
  if (names.length === 1) return names[0]
  return `${names.slice(0, 2).join(' + ')}${names.length > 2 ? ` 等 ${names.length} 个` : ''}`
})

const inputStatusText = computed(() => {
  if (!hasSelectedKb.value) return '请选择知识库'
  return props.loading ? 'AI 正在思考' : '可随时提问'
})

// 会话切换时：加载该会话的附件（持久化，刷新不丢）
watch(
  () => props.currentConversation?.id,
  async (id) => {
    attachments.value = []
    if (id) {
      try {
        attachments.value = await listAttachments(id)
      } catch (e) {
        /* 附件加载失败不影响聊天 */
      }
    }
  }
)

// 上传附件（只允许文档类）
async function handleUploadAttachment(file) {
  if (!props.currentConversation?.id) {
    ElMessage.warning('请先新建会话再上传附件')
    return false
  }
  // 大小校验：超大附件解析/注入 prompt 会卡住或撑爆，前端先拦截
  const MAX_MB = 20
  if (file.size > MAX_MB * 1024 * 1024) {
    ElMessage.error(`附件超过大小限制 ${MAX_MB}MB，请拆分后上传`)
    return false
  }
  try {
    attachments.value.push({
      id: `pending-${Date.now()}`,
      filename: file.name,
      status: 'processing',
      summary: '附件解析中',
      chunk_count: 0,
    })
    await uploadAttachment(props.currentConversation.id, file)
    attachments.value = await listAttachments(props.currentConversation.id)
    ElMessage.success(`已上传附件「${file.name}」`)
  } catch (e) {
    /* 错误由拦截器提示 */
  }
  return false   // 阻止 el-upload 默认上传
}

function attachmentStatusText(att) {
  if (att.status === 'processing') return '附件解析中'
  if (att.status === 'failed') return '附件解析失败'
  return '附件已就绪'
}

// 删除附件
async function handleRemoveAttachment(att) {
  try {
    await deleteAttachment(props.currentConversation.id, att.id)
    attachments.value = attachments.value.filter((a) => a.id !== att.id)
  } catch (e) {
    /* 错误由拦截器提示 */
  }
}

// 发送消息
function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.loading) return
  if (!hasSelectedKb.value) {
    ElMessage.warning('请至少选择一个知识库后再提问')
    return
  }
  if (hasProcessingAttachments.value) {
    ElMessage.warning('附件仍在解析，可稍后再问或删除解析中的附件')
    return
  }
  emit('send', text)
  inputText.value = ''
}

// 监听消息变化：有新消息就滚动到底部（配合 nextTick 等 DOM 更新完）
watch(
  () => props.messages.length,
  async () => {
    await nextTick()
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  }
)
</script>

<style scoped>
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  background:
    radial-gradient(circle at 76% 10%, rgba(37, 99, 235, 0.1), transparent 28%),
    linear-gradient(180deg, #fbfcff 0%, #f5f8fd 100%);
  position: relative;
  min-width: 0;
}
.chat-area::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 18% 22%, rgba(20, 184, 166, 0.07), transparent 22%),
    radial-gradient(circle at 88% 70%, rgba(37, 99, 235, 0.06), transparent 26%);
  opacity: 0.9;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 68px;
  padding: 0 28px;
  border-bottom: 1px solid rgba(23, 26, 34, 0.08);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(18px);
  position: relative;
  z-index: 1;
}
.chat-title {
  font-size: 18px;
  font-weight: 800;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.chat-header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.meta-pill {
  height: 28px;
  display: inline-flex;
  align-items: center;
  padding: 0 9px;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 999px;
  background: var(--kb-primary-faint);
  color: var(--kb-primary-dark);
  font-size: 12px;
}
.meta-pill.evidence {
  border-color: rgba(15, 118, 110, 0.18);
  background: rgba(217, 244, 239, 0.62);
  color: var(--kb-accent-teal);
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 34px 42px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  scroll-behavior: smooth;
}
.message-list.is-empty {
  overflow: hidden;
  padding: 34px 42px;
}

/* 空状态（渐变 + 呼吸，克制） */
.empty-hint {
  display: grid;
  grid-template-columns: minmax(340px, 0.88fr) minmax(420px, 1fr);
  align-items: center;
  gap: 56px;
  color: var(--kb-text-secondary);
  margin: auto;
  width: min(1120px, 100%);
  padding: 18px 6px;
  animation: kb-float-in 0.55s var(--kb-ease) both;
}
.empty-visual-card {
  position: relative;
  min-height: 320px;
  border: 1px solid rgba(255, 255, 255, 0.74);
  border-radius: 24px;
  overflow: hidden;
  background: #fff;
  box-shadow: 0 28px 72px rgba(15, 23, 42, 0.16);
}
.empty-visual-card::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    linear-gradient(90deg, rgba(255, 255, 255, 0.76), transparent 34%),
    linear-gradient(180deg, transparent 68%, rgba(255, 255, 255, 0.84));
  pointer-events: none;
}
.empty-visual-card img {
  width: 100%;
  height: 100%;
  min-height: 320px;
  object-fit: cover;
  display: block;
  filter: saturate(1.05) contrast(1.02);
}
.empty-visual-badge {
  position: absolute;
  z-index: 2;
  right: 18px;
  bottom: 18px;
  display: flex;
  align-items: center;
  gap: 8px;
  max-width: calc(100% - 32px);
  padding: 8px 10px;
  border: 1px solid var(--kb-border);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.82);
  box-shadow: var(--kb-shadow);
  color: var(--kb-text);
  font-size: 12px;
  font-weight: 700;
  backdrop-filter: blur(16px);
}
.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--kb-accent-teal);
  animation: kb-soft-pulse 1.8s ease infinite;
}
.empty-copy {
  min-width: 0;
}
.empty-kicker {
  display: block;
  color: var(--kb-primary);
  font-size: 12px;
  font-weight: 800;
}
.empty-copy h3 {
  margin: 12px 0 14px;
  color: var(--kb-text);
  font-size: 38px;
  line-height: 1.08;
  letter-spacing: 0;
}
.empty-copy p {
  max-width: 560px;
  font-size: 15px;
  color: var(--kb-text-secondary);
  line-height: 1.75;
}
.capability-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}
.capability-row span {
  display: inline-flex;
  align-items: center;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--kb-border);
  border-radius: 999px;
  background: #fff;
  color: var(--kb-text-secondary);
  font-size: 12px;
}

.ai-signal-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  margin-top: 22px;
  max-width: 560px;
}

.ai-signal-grid div {
  min-height: 82px;
  padding: 14px;
  border: 1px solid rgba(37, 99, 235, 0.12);
  border-radius: 16px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.92), rgba(248, 250, 252, 0.86));
  box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06);
}

.ai-signal-grid strong,
.ai-signal-grid span {
  display: block;
}

.ai-signal-grid strong {
  color: var(--kb-text);
  font-size: 14px;
  font-weight: 820;
}

.ai-signal-grid span {
  margin-top: 8px;
  color: var(--kb-text-muted);
  font-size: 12px;
  line-height: 1.5;
}

/* 加载提示（AI 思考中，透明 AI 状态展示） */
.loading-bubble {
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--kb-surface);
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius);
  padding: 10px 16px;
  color: var(--kb-text-secondary);
  font-size: 13px;
  box-shadow: var(--kb-shadow-sm);
}

/* 底部输入壳层：留白更大，像正式 AI 工作台 */
.chat-input-shell {
  padding: 0 34px 28px;
  position: relative;
  z-index: 1;
}
.chat-input-panel {
  position: relative;
  background: #fff;
  border: 1px solid rgba(23, 26, 34, 0.12);
  border-radius: 22px;
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.12);
  overflow: hidden;
}

/* 顶部状态条 */
.input-topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--kb-border-light);
  background: linear-gradient(90deg, #f8fafc, #fff);
}
.input-badges {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.input-badge {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border-radius: 999px;
  font-size: 12px;
  color: var(--kb-text-secondary);
  background: #fff;
  border: 1px solid var(--kb-border);
}
.input-badge.primary {
  color: var(--kb-primary-dark);
  background: var(--kb-primary-faint);
  border-color: var(--kb-primary-light);
}
.input-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--kb-text-muted);
  white-space: nowrap;
}
.input-status.busy {
  color: var(--kb-primary);
}

.composer-body {
  padding: 14px 16px 12px;
  background:
    radial-gradient(circle at 12% 0%, rgba(37, 99, 235, 0.07), transparent 28%),
    linear-gradient(180deg, #fff 0%, #f8fbff 100%);
}

.chat-input-bar {
  display: flex;
  align-items: stretch;
}
.chat-input-bar :deep(.el-textarea) {
  flex: 1;
}
.chat-input-bar :deep(.el-textarea__inner) {
  min-height: 88px;
  background: rgba(248, 250, 252, 0.86);
  border: 1px solid transparent;
  border-radius: 16px;
  padding: 14px 16px;
  font-size: 14px;
  line-height: 1.6;
  box-shadow: inset 0 0 0 1px var(--kb-border);
  transition: box-shadow var(--kb-duration) var(--kb-ease), background var(--kb-duration) var(--kb-ease);
}
.chat-input-bar :deep(.el-textarea__inner:focus) {
  background: #fff;
  box-shadow: inset 0 0 0 1px var(--kb-primary), 0 0 0 4px rgba(37, 99, 235, 0.08);
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
}
.send-btn {
  min-width: 96px;
  height: 40px;
  border-radius: 14px;
  font-size: 14px;
  font-weight: 600;
  box-shadow: var(--kb-shadow-sm);
}

/* 底部提示 */
.input-footer-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 14px 11px;
  border-top: 1px solid var(--kb-border-light);
  font-size: 12px;
  color: var(--kb-text-muted);
  flex-wrap: wrap;
}
.input-footer-hint .dot {
  color: #cbd5e1;
}

.composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 12px;
}
.toolbar-left {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.attach-btn {
  height: 36px;
  padding: 0 13px;
  border-radius: 12px;
  border-color: rgba(37, 99, 235, 0.18);
  color: var(--kb-text-secondary);
  background: rgba(255, 255, 255, 0.72);
  font-size: 13px;
  font-weight: 600;
}
.attach-btn:hover {
  color: var(--kb-primary);
  background: var(--kb-primary-faint);
  border-color: rgba(37, 99, 235, 0.28);
}

/* 当前会话附件条 */
.attachment-strip {
  margin-bottom: 8px;
}
.attach-list {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.attach-chip {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) 22px;
  align-items: center;
  gap: 7px;
  width: min(280px, 100%);
  min-height: 40px;
  padding: 7px 8px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(37, 99, 235, 0.14);
  color: var(--kb-text-main);
  font-size: 12px;
  min-width: 0;
  box-shadow: 0 6px 16px rgba(15, 23, 42, 0.04);
}
.attach-chip.is-processing {
  background: rgba(255, 251, 235, 0.82);
  border-color: rgba(245, 158, 11, 0.28);
  color: #92400e;
}
.attach-chip.is-failed {
  background: rgba(254, 242, 242, 0.84);
  border-color: rgba(239, 68, 68, 0.24);
  color: #991b1b;
}
.attach-icon {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 8px;
  color: var(--kb-primary);
  background: rgba(37, 99, 235, 0.09);
  font-size: 13px;
}
.attach-content {
  min-width: 0;
}
.attach-main {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
.attach-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: 700;
  color: var(--kb-text-main);
}
.attach-state {
  flex-shrink: 0;
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  color: var(--kb-primary-dark);
  background: rgba(37, 99, 235, 0.08);
  font-size: 10px;
  font-weight: 700;
}
.attach-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
  color: var(--kb-text-muted);
  font-size: 11px;
  line-height: 1.2;
  overflow: hidden;
}
.attach-summary span:first-child {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.chunk-count {
  flex-shrink: 0;
  color: var(--kb-text-secondary);
  font-variant-numeric: tabular-nums;
}
.attach-remove {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--kb-text-muted);
  cursor: pointer;
  font-size: 12px;
  transition: color var(--kb-duration) var(--kb-ease), background var(--kb-duration) var(--kb-ease);
}
.attach-remove:hover {
  color: var(--kb-danger);
  background: rgba(239, 68, 68, 0.08);
}
.attach-hint {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 12px;
  color: var(--kb-text-muted);
}

@media (max-width: 1100px) {
  .empty-hint {
    grid-template-columns: 1fr;
    gap: 28px;
  }
  .empty-visual-card {
    min-height: 240px;
  }
  .empty-visual-card img {
    min-height: 240px;
  }
}

@media (max-width: 760px) {
  .chat-header {
    align-items: flex-start;
    flex-direction: column;
  }
  .message-list {
    padding: 18px;
  }
  .empty-visual-card {
    display: none;
  }
  .empty-copy h3 {
    font-size: 24px;
  }
  .ai-signal-grid {
    grid-template-columns: 1fr;
  }
  .chat-input-shell {
    padding: 0 12px 14px;
  }
  .chat-input-bar {
    align-items: stretch;
  }
  .composer-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .toolbar-left {
    align-items: flex-start;
    flex-direction: column;
  }
  .attach-list {
    grid-template-columns: 1fr;
  }
  .attach-hint {
    white-space: normal;
  }
}
</style>
