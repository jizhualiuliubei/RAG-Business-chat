<!--
  MessageBubble.vue：单条消息
  - 用户消息：右对齐，浅蓝信息气泡
  - AI 消息：左对齐，白卡片，底部带"引用来源"折叠面板
-->
<template>
  <div class="msg-row" :class="message.role === 'user' ? 'user-row' : 'ai-row'">
    <!-- AI 消息：左边小头像 -->
    <div v-if="message.role === 'assistant'" class="avatar ai-avatar">AI</div>

    <div class="msg-body">
      <!-- 消息时间（气泡上方，小号弱化文字） -->
      <div v-if="message.created_at" class="msg-time">
        {{ formatTime(message.created_at) }}
      </div>

      <!-- 气泡内容：AI 用 markdown 渲染，用户用纯文本 -->
      <div class="bubble" :class="message.role === 'user' ? 'user-bubble' : 'ai-bubble'">
        <!-- 用户消息：纯文本（pre-wrap 保留换行） -->
        <span v-if="message.role === 'user'" class="content-text">{{ message.content }}</span>
        <!-- AI 消息：markdown 渲染（加粗/列表/代码块等） -->
        <template v-else>
          <div class="markdown-body" v-html="renderedContent"></div>
          <!-- 打字光标：流式生成中显示（惊艳时刻） -->
          <span v-if="showTypingCursor" class="kb-cursor"></span>
        </template>
      </div>

      <!-- AI 消息：模型信息（模型名 + token） -->
      <div v-if="message.role === 'assistant' && message.model_name" class="meta-info">
        {{ message.model_name }}
        <template v-if="message.usage && message.usage.total_tokens">
          · 输入 {{ message.usage.input_tokens }} / 输出 {{ message.usage.output_tokens }} tokens
        </template>
      </div>

      <!-- AI 消息：引用来源折叠面板（后端返回 sources，按 source_type 区分知识库和会话附件） -->
      <div v-if="message.role === 'assistant' && allSources.length" class="ref-panel">
        <div class="evidence-dock">
          <div class="evidence-head">
            <div class="evidence-title-wrap">
              <span class="evidence-title">引用证据链</span>
              <span class="evidence-subtitle">已匹配可信来源</span>
            </div>
            <span class="evidence-count">{{ allSources.length }} 条来源</span>
          </div>
          <div class="evidence-summary">
            <span class="evidence-metric">{{ sourceKbSummary }}</span>
            <span class="evidence-metric">最高相似度 {{ topScore }}%</span>
            <span v-if="attachmentSources.length" class="evidence-metric">会话附件 {{ attachmentSources.length }} 条</span>
          </div>
          <div class="evidence-chips">
            <span
              v-for="(src, i) in allSources.slice(0, 3)"
              :key="i"
              class="evidence-chip"
            >
              [{{ i + 1 }}] {{ src.source || '未知文档' }}
            </span>
          </div>
        </div>
        <el-collapse>
          <el-collapse-item>
            <template #title>
              <span class="ref-title">
                <el-icon><Document /></el-icon>
                引用来源（{{ allSources.length }}）
              </span>
            </template>
            <!-- 每条来源：文件名 + 知识库 + 相似度 + 片段内容（markdown 渲染，标题/列表正常显示） -->
            <div v-if="knowledgeBaseSources.length" class="ref-group-title">知识库来源</div>
            <div v-for="(src, i) in knowledgeBaseSources" :key="`kb-${i}`" class="ref-item">
              <div class="ref-meta">
                <span class="ref-file">{{ src.source }}</span>
                <el-tag v-if="src.kb_name" size="small" type="info" class="ref-kb">{{ src.kb_name }}</el-tag>
                <span class="ref-score">相似度 {{ formatScore(src.score) }}%</span>
              </div>
              <div class="ref-text markdown-body" v-html="renderSource(src.text)"></div>
            </div>
            <div v-if="attachmentSources.length" class="ref-group-title attachment">会话附件来源</div>
            <div v-for="(src, i) in attachmentSources" :key="`att-${i}`" class="ref-item attachment-ref">
              <div class="ref-meta">
                <span class="ref-file">{{ src.source }}</span>
                <el-tag size="small" type="success" class="ref-kb">会话附件</el-tag>
                <span v-if="src.locator" class="ref-locator">{{ src.locator }}</span>
                <span class="ref-score">匹配度 {{ formatScore(src.score) }}%</span>
              </div>
              <div class="ref-text markdown-body" v-html="renderSource(src.text)"></div>
            </div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>

    <!-- 用户消息：右边小头像 -->
    <div v-if="message.role === 'user'" class="avatar user-avatar">我</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'

// 创建 markdown-it 实例（渲染 AI 回答）
// 配置：html=false 禁止渲染原始 HTML（防 XSS），linkify 自动识别链接
const md = new MarkdownIt({ html: false, linkify: true })

const props = defineProps({
  message: { type: Object, required: true },
  isGenerating: { type: Boolean, default: false },   // 该消息是否正在流式生成（显示打字光标）
})

// 格式化消息时间：显示"年月日 时:分"（如 2026-08-26 13:01）
function formatTime(iso) {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const y = d.getFullYear()
  const mo = pad(d.getMonth() + 1)
  const day = pad(d.getDate())
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  return `${y}-${mo}-${day} ${hm}`
}

// 打字光标：最后一条 AI 消息且正在生成中才显示
const showTypingCursor = computed(() => {
  return props.message.role === 'assistant' && props.isGenerating
})

const allSources = computed(() => props.message.sources || [])
const knowledgeBaseSources = computed(() => {
  return allSources.value.filter((src) => (src.source_type || 'knowledge_base') !== 'attachment')
})
const attachmentSources = computed(() => {
  return allSources.value.filter((src) => src.source_type === 'attachment')
})

const topScore = computed(() => {
  const scores = allSources.value.map((src) => Number(src.score || 0))
  if (!scores.length) return 0
  return formatScore(Math.max(...scores))
})

const sourceKbSummary = computed(() => {
  const names = [...new Set(knowledgeBaseSources.value.map((src) => src.kb_name).filter(Boolean))]
  if (attachmentSources.value.length && names.length === 0) return '会话附件'
  if (names.length === 0) return '来源知识库待标注'
  if (names.length === 1) return names[0]
  return `${names.slice(0, 2).join(' + ')}${names.length > 2 ? ` 等 ${names.length} 个知识库` : ''}`
})

// 渲染引用来源片段（复用 markdown-it，让 # 标题 / - 列表等正常显示）
// 片段是检索原文，可能很长，渲染后限制高度滚动查看
function renderSource(text) {
  if (!text) return ''
  return md.render(text)
}

function formatScore(score) {
  const value = Number(score || 0)
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value * 100)))
}

// 把 AI 回答的 markdown 渲染成 HTML
// 用 Map 做内容级缓存：相同 content 直接复用渲染结果，
// 避免切换会话/流式更新时对未变化消息重复解析 markdown（性能优化）
const mdCache = new Map()
const renderedContent = computed(() => {
  if (props.message.role !== 'assistant') return ''
  const raw = props.message.content || ''
  if (mdCache.has(raw)) return mdCache.get(raw)
  const html = md.render(raw)
  if (mdCache.size > 200) mdCache.clear()   // 防止缓存无限膨胀
  mdCache.set(raw, html)
  return html
})
</script>

<style scoped>
.msg-row {
  display: flex;
  gap: 10px;
  animation: kb-message-in 0.28s var(--kb-ease) both;
}
.user-row {
  justify-content: flex-end;   /* 用户消息靠右 */
}
.ai-row {
  justify-content: flex-start; /* AI 消息靠左 */
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}
.ai-avatar {
  background: var(--kb-graphite);
  color: #fff;
  box-shadow: 0 10px 24px rgba(24, 24, 27, 0.18);
}
.user-avatar {
  background: linear-gradient(135deg, var(--kb-primary), var(--kb-accent-teal));
  color: #fff;
}

.msg-body {
  max-width: 74%;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

/* 消息时间（气泡上方，弱化） */
.msg-time {
  font-size: 11px;
  color: var(--kb-text-muted);
  line-height: 1;
  padding: 0 4px;
}
.user-row .msg-time {
  text-align: right;   /* 用户消息的时间靠右对齐 */
}

.bubble {
  padding: 12px 14px;
  border-radius: var(--kb-radius-lg);
  line-height: 1.64;
  font-size: 13.5px;
}
.user-bubble {
  background: linear-gradient(135deg, #e8f1ff 0%, #dff8f3 100%);
  color: #0f2948;
  border: 1px solid rgba(37, 99, 235, 0.18);
  border-top-right-radius: 4px;   /* 气泡小角：更自然 */
  box-shadow: 0 14px 34px rgba(37, 99, 235, 0.12);
}
.ai-bubble {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid var(--kb-border);
  border-top-left-radius: 4px;
  box-shadow: 0 14px 40px rgba(24, 24, 27, 0.08);
  backdrop-filter: blur(14px);
}
.content-text {
  white-space: pre-wrap;   /* 保留换行符，大模型回答分段更好看 */
  word-break: break-word;
}

/* AI 回答的 markdown 渲染样式（冷静克制风） */
.markdown-body {
  line-height: 1.75;
  word-break: break-word;
}
.markdown-body :deep(p) {
  margin: 0.5em 0;
}
.markdown-body :deep(strong) {
  font-weight: 600;
  color: var(--kb-primary-dark);
}
.markdown-body :deep(ul), .markdown-body :deep(ol) {
  margin: 0.5em 0;
  padding-left: 1.4em;
}
.markdown-body :deep(li) {
  margin: 0.25em 0;
}
.markdown-body :deep(h1), .markdown-body :deep(h2), .markdown-body :deep(h3) {
  margin: 0.8em 0 0.4em;
  font-weight: 600;
}
.markdown-body :deep(code) {
  background: #f0f2f5;
  padding: 2px 5px;
  border-radius: 4px;
  font-size: 0.9em;
}
.markdown-body :deep(pre) {
  background: #282c34;
  color: #fff;
  padding: 12px 14px;
  border-radius: var(--kb-radius);
  overflow-x: auto;
  margin: 0.6em 0;
}
.markdown-body :deep(pre code) {
  background: transparent;
  color: inherit;
  padding: 0;
}
.markdown-body :deep(blockquote) {
  border: 1px solid rgba(37, 99, 235, 0.14);
  margin: 0.6em 0;
  padding: 4px 12px;
  border-radius: 10px;
  color: var(--kb-text-secondary);
  background: rgba(248, 250, 252, 0.9);
}
.markdown-body :deep(a) {
  color: var(--kb-primary);
}

/* 模型信息（模型名 + token）：小号灰字，回答下方 */
.meta-info {
  font-size: 12px;
  color: var(--kb-text-secondary);
  padding-left: 2px;
}

/* 引用来源折叠面板 */
.ref-panel {
  background: rgba(255, 255, 255, 0.86);
  border: 1px solid var(--kb-border);
  border-radius: var(--kb-radius-lg);
  overflow: hidden;
  box-shadow: var(--kb-shadow-evidence);
}
.evidence-dock {
  padding: 10px 12px;
  border-bottom: 1px solid var(--kb-border-light);
  background:
    linear-gradient(135deg, rgba(239, 246, 255, 0.68), rgba(204, 251, 241, 0.28)),
    rgba(255, 255, 255, 0.78);
}
.evidence-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.evidence-title-wrap {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
}
.evidence-title {
  color: var(--kb-text);
  font-size: 13.5px;
  font-weight: 760;
}
.evidence-subtitle {
  color: var(--kb-text-muted);
  font-size: 11px;
  font-weight: 500;
}
.evidence-count {
  color: var(--kb-primary-dark);
  font-size: 12px;
  font-weight: 700;
}
.evidence-summary {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
  color: var(--kb-text-secondary);
  font-size: 12px;
}
.evidence-metric {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.62);
}
.evidence-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.evidence-chip {
  max-width: 150px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  padding: 0 8px;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--kb-primary-dark);
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ref-title {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--kb-primary-dark);
  font-size: 13px;
}
.ref-item {
  padding: 8px 10px;
  border-top: 1px solid var(--kb-border);
  background: rgba(251, 250, 248, 0.76);
}
.ref-group-title {
  padding: 9px 10px 4px;
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 760;
}
.ref-group-title.attachment {
  color: #0f766e;
}
.attachment-ref {
  background: rgba(240, 253, 250, 0.58);
}
.ref-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.ref-file {
  color: var(--kb-primary-dark);
  font-weight: 600;
  font-size: 13px;
}
.ref-kb {
  margin-left: 4px;
}
.ref-score {
  color: var(--kb-text-secondary);
  font-size: 12px;
  margin-left: auto;
}
.ref-locator {
  color: var(--kb-text-muted);
  font-size: 12px;
}
.ref-text {
  color: var(--kb-text);
  font-size: 13px;
  line-height: 1.6;
  max-height: 130px;
  overflow-y: auto;
}
/* 引用片段内的 markdown 元素间距（标题/列表） */
.ref-text :deep(h1),
.ref-text :deep(h2),
.ref-text :deep(h3) {
  font-size: 13px;
  font-weight: 600;
  margin: 0.5em 0 0.25em;
  color: var(--kb-text);
}
.ref-text :deep(h1)::before,
.ref-text :deep(h2)::before {
  content: '# ';
  color: var(--kb-primary);
}
.ref-text :deep(ul),
.ref-text :deep(ol) {
  margin: 0.3em 0;
  padding-left: 1.3em;
}
.ref-text :deep(p) {
  margin: 0.3em 0;
}

@media (max-width: 760px) {
  .msg-body {
    max-width: 100%;
  }
}
</style>
