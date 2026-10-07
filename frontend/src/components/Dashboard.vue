<!--
  Dashboard.vue：概览页（现代 AI 工作台）
  - 顶部：统计卡片（知识库/文档/会话/消息/向量片段）
  - 中部：各知识库文档分布列表（点击切知识库）
  - 下部：最近会话列表（点击跳转对话区）
  视觉：大卡片 + 渐变图标 + 圆角阴影，悬停浮起
-->
<template>
  <main class="dashboard">
    <!-- 顶部标题 -->
    <header class="dash-header">
      <div class="dash-title">
        <h2>知识治理驾驶舱</h2>
        <p>用真实数据展示知识库覆盖、文档管线、向量片段和会话活跃度。</p>
      </div>
      <div class="dash-signal" aria-hidden="true">
        <strong>RAG</strong>
        <span>证据就绪</span>
      </div>
      <el-button type="primary" plain @click="$emit('back-to-chat')">
        <el-icon><ChatDotRound /></el-icon>&nbsp;返回聊天
      </el-button>
    </header>

    <!-- 统计卡片 -->
    <div class="stat-grid">
      <div v-for="card in cards" :key="card.key" class="stat-card">
        <div class="stat-icon" :style="{ background: card.bg }">
          <el-icon><component :is="card.icon" /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ loading ? '—' : card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </div>
    </div>

    <!-- 内容区：左库右详情（三级下钻） -->
    <div class="content-grid">
      <!-- 左栏：知识库列表（带状态进度条） -->
      <section class="panel">
        <div class="panel-header">
          <h3>知识库</h3>
          <span class="panel-sub">点击查看文档</span>
        </div>
        <div v-if="loading" class="panel-loading" v-loading="true"></div>
        <div v-else-if="kbStats.length === 0" class="empty-list">
          <el-icon><FolderOpened /></el-icon>
          <span>暂无知识库</span>
        </div>
        <div v-else class="kb-list">
          <div
            v-for="kb in kbStats"
            :key="kb.id"
            class="kb-item"
            :class="{ active: selectedKb?.id === kb.id }"
            @click="selectKb(kb)"
          >
            <div class="kb-icon">
              <el-icon><FolderOpened /></el-icon>
            </div>
            <div class="kb-main">
              <div class="kb-name-row">
                <span class="kb-name">{{ kb.name }}</span>
                <el-tag v-if="kb.status_counts.failed > 0" type="danger" size="small">{{ kb.status_counts.failed }}失败</el-tag>
              </div>
              <div class="kb-meta">
                <span>{{ kb.doc_count }} 文档</span>
                <span class="dot">·</span>
                <span>{{ kb.chunk_count }} 片段</span>
              </div>
              <!-- 状态进度条：done/processing/failed 占比 -->
              <div class="kb-progress">
                <div class="kb-progress-track">
                  <div class="kb-progress-done" :style="{ width: pctDone(kb) + '%' }"></div>
                  <div class="kb-progress-processing" :style="{ width: pctProcessing(kb) + '%' }"></div>
                  <div class="kb-progress-failed" :style="{ width: pctFailed(kb) + '%' }"></div>
                </div>
                <div class="kb-progress-legend">
                  <span class="lg-done">✓ {{ kb.status_counts.done }}</span>
                  <span class="lg-processing">⟳ {{ kb.status_counts.processing }}</span>
                  <span class="lg-failed">✗ {{ kb.status_counts.failed }}</span>
                </div>
              </div>
            </div>
            <el-icon class="kb-arrow"><ArrowRight /></el-icon>
          </div>
        </div>
      </section>

      <!-- 右栏：选中知识库的详情（文档 + chunk 下钻） -->
      <section class="panel kb-detail-panel">
        <div class="panel-header">
          <h3>{{ selectedKb ? selectedKb.name + ' · 文档' : '知识库详情' }}</h3>
          <span class="panel-sub" v-if="selectedKb">点击文档查看切片</span>
        </div>
        <div v-if="!selectedKb" class="empty-list">
          <el-icon><Mouse /></el-icon>
          <span>选择左侧知识库查看详情</span>
        </div>
        <template v-else>
          <!-- 文档列表（每个文档卡片 + 其展开的 chunk 紧跟其后） -->
          <div class="doc-list">
            <template v-for="doc in selectedKb?.documents || []" :key="doc?.id">
              <div
                class="doc-item"
                :data-doc-id="doc?.id"
                @click="toggleDoc(doc)"
              >
                <div class="doc-icon">
                  <el-icon><Document /></el-icon>
                </div>
                <div class="doc-main">
                  <div class="doc-name-row">
                    <span class="doc-name">{{ doc?.filename }}</span>
                    <el-tag :type="statusTagType(doc?.status)" size="small">{{ statusText(doc?.status) }}</el-tag>
                  </div>
                  <div class="doc-meta">
                    <span>{{ formatSize(doc?.file_size) }}</span>
                    <span class="dot">·</span>
                    <span>{{ doc?.chunk_count }} 切片</span>
                    <span class="dot">·</span>
                    <span>{{ doc?.created_at }}</span>
                  </div>
                </div>
                <el-icon class="doc-arrow" :class="{ open: expandedDocId === doc?.id }">
                  <ArrowDown />
                </el-icon>
              </div>

              <!-- 展开的 chunk 列表：紧跟当前文档卡片 -->
              <div v-if="expandedDocId === doc?.id" class="chunk-list">
                <div v-if="chunkLoading" class="chunk-loading" v-loading="true"></div>
                <div v-else-if="chunks.length === 0" class="chunk-empty">该文档暂无切片</div>
                <div v-else v-for="chunk in chunks" :key="chunk.chunk_id" class="chunk-item">
                  <div class="chunk-head">
                    <span class="chunk-idx">#{{ chunk.chunk_id }}</span>
                    <span class="chunk-section">{{ chunk.section_title }}</span>
                  </div>
                  <div class="chunk-preview">{{ chunk.full_text || chunk.text_preview }}</div>
                </div>
              </div>
            </template>
          </div>
        </template>
      </section>
    </div>

    <!-- 最近会话 -->
    <div class="conv-section">
      <section class="panel">
        <div class="panel-header">
          <h3>最近会话</h3>
          <span class="panel-sub">点击继续对话</span>
        </div>
        <div v-if="loading" class="panel-loading" v-loading="true"></div>
        <div v-else-if="recentConvs.length === 0" class="empty-list">
          <el-icon><ChatDotRound /></el-icon>
          <span>暂无会话</span>
        </div>
        <div v-else class="conv-list">
          <div
            v-for="conv in recentConvs"
            :key="conv.id"
            class="conv-item"
            @click="handleSelectConv(conv)"
          >
            <div class="conv-icon">
              <el-icon><ChatDotRound /></el-icon>
            </div>
            <div class="conv-main">
              <div class="conv-title">{{ conv.title }}</div>
              <div class="conv-meta">
                <span>{{ conv.message_count }} 条消息</span>
                <span class="dot">·</span>
                <span>{{ conv.updated_at }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- 加载失败提示 -->
    <div v-if="error" class="dash-error">统计数据加载失败：{{ error }}</div>
  </main>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getStatsSummary, getDocChunks } from '../api/api.js'

const props = defineProps({
  currentKb: { type: Object, default: null },  // 当前知识库（用于高亮选中项）
})
const emit = defineEmits(['back-to-chat', 'select-kb', 'select-conversation'])

const stats = ref({ kb_count: 0, doc_count: 0, conv_count: 0, msg_count: 0, chunk_count: 0 })
const loading = ref(true)
const error = ref('')

// 下钻状态：选中的知识库 + 展开的文档 + 其 chunk
const selectedKb = ref(null)
const expandedDocId = ref(null)
const chunks = ref([])
const chunkLoading = ref(false)

const cards = computed(() => [
  { key: 'kb', label: '知识库', value: stats.value.kb_count, icon: 'FolderOpened', bg: 'var(--kb-graphite)' },
  { key: 'doc', label: '文档', value: stats.value.doc_count, icon: 'Document', bg: 'var(--kb-primary)' },
  { key: 'conv', label: '会话', value: stats.value.conv_count, icon: 'ChatDotRound', bg: '#3f4757' },
  { key: 'msg', label: '消息', value: stats.value.msg_count, icon: 'Message', bg: '#566176' },
  { key: 'chunk', label: '向量片段', value: stats.value.chunk_count, icon: 'Grid', bg: '#68758c' },
])

const kbStats = computed(() => stats.value.kb_stats || [])
const recentConvs = computed(() => stats.value.recent_conversations || [])

// 选中知识库：右侧详情面板显示该库文档
function selectKb(kb) {
  selectedKb.value = kb
  expandedDocId.value = null
  chunks.value = []
}

// 展开/收起文档的 chunk 列表
async function toggleDoc(doc) {
  if (expandedDocId.value === doc.id) {
    expandedDocId.value = null
    chunks.value = []
    return
  }
  expandedDocId.value = doc.id
  chunkLoading.value = true
  chunks.value = []
  try {
    chunks.value = await getDocChunks(doc.id)
  } catch (e) {
    chunks.value = []
  } finally {
    chunkLoading.value = false
  }
}

// 知识库状态占比（用于进度条）
function pctDone(kb) {
  const total = kb.status_counts.done + kb.status_counts.processing + kb.status_counts.failed
  if (!total) return 0
  return Math.round((kb.status_counts.done / total) * 100)
}
function pctProcessing(kb) {
  const total = kb.status_counts.done + kb.status_counts.processing + kb.status_counts.failed
  if (!total) return 0
  return Math.round((kb.status_counts.processing / total) * 100)
}
function pctFailed(kb) {
  const total = kb.status_counts.done + kb.status_counts.processing + kb.status_counts.failed
  if (!total) return 0
  return Math.round((kb.status_counts.failed / total) * 100)
}

// 文档状态展示
function statusText(status) {
  return { done: '完成', processing: '解析中', failed: '失败' }[status] || status
}
function statusTagType(status) {
  return { done: 'success', processing: 'warning', failed: 'danger' }[status] || 'info'
}
function formatSize(size) {
  if (!size) return '0B'
  if (size < 1024) return size + 'B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + 'KB'
  return (size / (1024 * 1024)).toFixed(1) + 'MB'
}

onMounted(async () => {
  try {
    stats.value = await getStatsSummary()
    // 默认选中第一个知识库
    if (kbStats.value.length > 0) selectedKb.value = kbStats.value[0]
  } catch (e) {
    error.value = e.message || '未知错误'
  } finally {
    loading.value = false
  }
})

// 点击会话：跳回对话区并加载该会话（App 层负责跳转）
function handleSelectConv(conv) {
  emit('select-conversation', { id: conv.id, title: conv.title })
}
</script>

<style scoped>
.dashboard {
  flex: 1;
  height: 100%;
  overflow-y: auto;
  background:
    radial-gradient(circle at 78% 6%, rgba(37, 99, 235, 0.13), transparent 30%),
    linear-gradient(180deg, #fbfcfe 0%, #f4f8fd 100%);
  padding: 36px 42px;
}

.dash-header {
  position: relative;
  overflow: hidden;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 22px;
  min-height: 176px;
  margin-bottom: 22px;
  padding: 30px;
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: 26px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(241, 247, 255, 0.84));
  box-shadow: 0 28px 76px rgba(15, 23, 42, 0.11);
  animation: kb-float-in 0.45s var(--kb-ease) both;
}

.dash-header::before {
  content: '';
  position: absolute;
  left: 30px;
  right: 30px;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.42), rgba(15, 118, 110, 0.24), transparent);
}

.dash-title h2 {
  font-size: 38px;
  font-weight: 860;
  color: var(--kb-text);
  margin-bottom: 10px;
  letter-spacing: 0;
}
.dash-title p {
  max-width: 620px;
  font-size: 15px;
  color: var(--kb-text-secondary);
  line-height: 1.8;
}

.dash-signal {
  position: absolute;
  right: 150px;
  top: 34px;
  width: 172px;
  height: 108px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(37, 99, 235, 0.13);
  border-radius: 24px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.74), rgba(255, 255, 255, 0.42));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.74), 0 18px 40px rgba(37, 99, 235, 0.08);
}

.dash-signal::before,
.dash-signal::after {
  content: '';
  position: absolute;
  border: 1px solid rgba(37, 99, 235, 0.2);
  border-radius: 50%;
}

.dash-signal::before {
  inset: 18px 34px;
}

.dash-signal::after {
  inset: 28px 22px;
  border-color: rgba(15, 118, 110, 0.18);
}

.dash-signal strong,
.dash-signal span {
  position: relative;
  z-index: 1;
  display: block;
}

.dash-signal strong {
  color: var(--kb-text);
  font-size: 25px;
  font-weight: 880;
  line-height: 1;
}

.dash-signal span {
  margin-top: 44px;
  color: var(--kb-text-muted);
  font-size: 11px;
}

/* ===== 统计卡片 ===== */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 22px;
}
.stat-card {
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  padding: 20px;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.06);
  transition: transform 0.2s var(--kb-ease), box-shadow 0.2s var(--kb-ease);
  animation: kb-float-in 0.5s var(--kb-ease) both;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--kb-shadow);
}
.stat-card::after {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 0;
  height: 2px;
  border-radius: 999px 999px 0 0;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.6), rgba(15, 118, 110, 0.32));
  opacity: 0.42;
}
.stat-icon {
  width: 46px;
  height: 46px;
  border-radius: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: #fff;
  flex-shrink: 0;
  box-shadow: 0 8px 18px rgba(15, 23, 42, 0.1);
}
.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--kb-text);
  line-height: 1.2;
}
.stat-label {
  font-size: 13px;
  color: var(--kb-text-secondary);
  margin-top: 2px;
}

/* ===== 内容区（左库右详情）===== */
.content-grid {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 18px;
  align-items: start;
  margin-bottom: 20px;
}
@media (max-width: 1100px) {
  .dash-signal {
    display: none;
  }
  .dash-header {
    min-height: auto;
  }
  .content-grid {
    grid-template-columns: 1fr;
  }
}

/* 知识库状态进度条 */
.kb-progress {
  margin-top: 8px;
}
.kb-progress-track {
  display: flex;
  height: 6px;
  border-radius: 3px;
  overflow: hidden;
  background: var(--kb-border-light);
}
.kb-progress-done { background: var(--kb-success, #10b981); }
.kb-progress-processing { background: #f59e0b; }
.kb-progress-failed { background: var(--kb-danger, #ef4444); }
.kb-progress-legend {
  display: flex;
  gap: 10px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--kb-text-muted);
}
.lg-done { color: var(--kb-success, #10b981); }
.lg-processing { color: #d97706; }
.lg-failed { color: var(--kb-danger, #ef4444); }
.kb-arrow {
  color: var(--kb-text-muted);
  font-size: 14px;
  flex-shrink: 0;
}

/* 右侧详情面板（文档 + chunk） */
.kb-detail-panel {
  max-height: 520px;
  overflow-y: auto;
}
.doc-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.doc-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--kb-border-light);
  cursor: pointer;
  transition: background 0.15s var(--kb-ease), border-color 0.15s var(--kb-ease);
}
.doc-item:hover {
  background: var(--kb-surface-hover);
  border-color: var(--kb-border);
}
.doc-icon {
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: var(--kb-primary-faint);
  color: var(--kb-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.doc-main {
  flex: 1;
  min-width: 0;
}
.doc-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.doc-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.doc-meta {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 3px;
}
.doc-arrow {
  color: var(--kb-text-muted);
  font-size: 14px;
  flex-shrink: 0;
  transition: transform 0.2s var(--kb-ease);
}
.doc-arrow.open {
  transform: rotate(180deg);
}

/* chunk 列表 */
.chunk-list {
  margin: 4px 0 6px 46px;
  padding: 8px 12px;
  border-left: 1px solid var(--kb-primary-light);
  background: #f8fafc;
  border-radius: 0 8px 8px 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chunk-loading {
  height: 40px;
}
.chunk-empty {
  font-size: 12px;
  color: var(--kb-text-muted);
  padding: 8px 0;
}
.chunk-item {
  padding: 6px 0;
  border-bottom: 1px dashed var(--kb-border-light);
}
.chunk-item:last-child {
  border-bottom: none;
}
.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}
.chunk-idx {
  font-size: 11px;
  font-weight: 700;
  color: var(--kb-primary);
  background: var(--kb-primary-faint);
  padding: 1px 6px;
  border-radius: 4px;
  flex-shrink: 0;
}
.chunk-section {
  font-size: 12px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chunk-preview {
  font-size: 12px;
  color: var(--kb-text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 120px;
  overflow-y: auto;
}

/* 最近会话区块 */
.conv-section {
  margin-bottom: 8px;
}

.panel {
  background: #fff;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 20px;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.055);
  padding: 22px;
  min-height: 220px;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 14px;
}
.panel-header h3 {
  font-size: 15px;
  font-weight: 600;
  color: var(--kb-text);
}
.panel-sub {
  font-size: 12px;
  color: var(--kb-text-muted);
}
.panel-loading {
  height: 100px;
}

/* 空态 */
.empty-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 40px 0;
  color: var(--kb-text-muted);
  font-size: 13px;
}
.empty-list .el-icon {
  font-size: 40px;
}

/* 知识库列表 */
.kb-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.kb-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: background 0.15s var(--kb-ease), border-color 0.15s var(--kb-ease), transform 0.15s var(--kb-ease);
}
.kb-item:hover {
  background: var(--kb-surface-hover);
  border-color: var(--kb-border);
  transform: translateX(2px);
}
.kb-item.active {
  background: var(--kb-primary-faint);
  border-color: var(--kb-primary-light);
}
.kb-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--kb-primary-faint);
  color: var(--kb-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.kb-main {
  flex: 1;
  min-width: 0;
}
.kb-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.kb-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--kb-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.kb-meta {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 3px;
}
.kb-check {
  color: var(--kb-primary);
  font-size: 18px;
  flex-shrink: 0;
}
.dot {
  margin: 0 4px;
  color: var(--kb-text-muted);
}

/* 会话列表 */
.conv-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conv-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 12px;
  border: 1px solid transparent;
  cursor: pointer;
  transition: background 0.15s var(--kb-ease), border-color 0.15s var(--kb-ease), transform 0.15s var(--kb-ease);
}
.conv-item:hover {
  background: var(--kb-surface-hover);
  border-color: var(--kb-border);
  transform: translateX(2px);
}
.conv-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--kb-primary-faint);
  color: var(--kb-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.conv-main {
  flex: 1;
  min-width: 0;
}
.conv-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--kb-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-meta {
  font-size: 12px;
  color: var(--kb-text-secondary);
  margin-top: 3px;
}

.dash-error {
  margin-top: 24px;
  color: var(--kb-danger);
  font-size: 14px;
}
</style>
