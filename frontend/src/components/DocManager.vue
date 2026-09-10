<!--
  DocManager.vue：文档管理抽屉
  - 拖拽/点击上传文档（上传到当前知识库）
  - 上传进度实时显示（进度条）
  - 文档列表：文件名 / 大小 / 状态 / 时间 / 删除
  - 上传后立即出现在列表（processing），轮询实时更新到"完成"
-->
<template>
  <!-- el-drawer：Element Plus 的抽屉组件，从右侧滑出 -->
  <el-drawer
    :model-value="visible"
    title="文档管线"
    size="560px"
    class="doc-manager-drawer"
    @update:model-value="$emit('update:visible', $event)"
  >
    <!-- 1. 上传区（支持拖拽，带实时上传进度） -->
    <el-upload
      :show-file-list="false"
      :before-upload="handleBeforeUpload"
      :on-progress="handleProgress"
      drag
      multiple
      class="upload-area"
      accept=".txt,.pdf,.docx,.csv,.xlsx,.xls"
    >
      <div class="upload-inner">
        <el-icon class="upload-icon"><UploadFilled /></el-icon>
        <div>
          <div class="upload-text">导入企业文档到知识库</div>
          <div class="upload-tip">拖拽或点击上传，支持 .txt / .pdf / .docx / .csv / .xlsx，上传后自动解析、切分、向量化。</div>
        </div>
      </div>
    </el-upload>

    <!-- 2. 上传进度条（有文件在传时显示） -->
    <div v-if="uploading" class="upload-progress">
      <div class="up-row">
        <span class="up-file">{{ uploading.name }}</span>
        <span class="up-pct">{{ uploadPercent }}%</span>
      </div>
      <el-progress
        :percentage="uploadPercent"
        :status="uploadPercent >= 100 ? 'success' : undefined"
        :stroke-width="6"
      />
    </div>

    <!-- 3. 当前知识库选择（与首页对话区知识库双向同步） -->
    <div class="kb-hint">
      <span class="kb-hint-label">当前知识库：</span>
      <el-select
        :model-value="kbId"
        placeholder="选择知识库"
        style="width: 220px"
        @change="handleKbChange"
      >
        <el-option
          v-for="kb in kbList"
          :key="kb.id"
          :label="kb.name"
          :value="kb.id"
        />
      </el-select>
    </div>

    <!-- 4. 文档列表表格 -->
    <div class="doc-toolbar">
      <div>
        <strong>文档列表</strong>
        <span v-if="hasProcessingDocs">后台正在排队解析，列表会自动刷新</span>
      </div>
      <el-button
        type="danger"
        plain
        size="small"
        :disabled="selectedDocRows.length === 0"
        :loading="batchDeleting"
        @click="handleBatchDelete"
      >
        批量删除
      </el-button>
    </div>

    <el-table
      :data="documents"
      v-loading="tableLoading"
      style="width: 100%"
      @selection-change="handleSelectionChange"
    >
      <el-table-column type="selection" width="38" />
      <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
      <el-table-column label="大小" width="80">
        <template #default="{ row }">{{ formatSize(row.file_size) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <div class="doc-status-box">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
            <div class="doc-progress" aria-label="解析进度">
              <span
                :class="row.status"
                :style="{ width: docStatusPercent(row) + '%' }"
              ></span>
            </div>
            <el-tooltip
              v-if="row.status === 'failed' && row.failure_reason"
              :content="row.failure_reason"
              placement="top"
            >
              <button class="failure-reason-btn" type="button">失败原因</button>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="70">
        <template #default="{ row }">
          <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 空列表提示 -->
    <div v-if="documents.length === 0 && !tableLoading" class="empty-list">
      该知识库还没有文档
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { uploadDocument, listDocuments, deleteDocument, deleteDocuments } from '../api/api.js'

const props = defineProps({
  visible: { type: Boolean, default: false },
  kbId: { type: Number, default: null },
  kbName: { type: String, default: '' },   // 当前知识库名（父组件传入）
  kbList: { type: Array, default: () => [] },  // 知识库列表（用于下拉选择）
})
const emit = defineEmits(['update:visible', 'select-kb'])

const documents = ref([])        // 文档列表
const tableLoading = ref(false)  // 表格加载中
const uploading = ref(null)      // 正在上传的文件名（null=无）
const uploadPercent = ref(0)     // 上传进度 0-100
const selectedDocRows = ref([])
const batchDeleting = ref(false)
let pollTimer = null             // 状态轮询定时器

const hasProcessingDocs = computed(() => documents.value.some((d) => d.status === 'processing'))

// 抽屉内切换知识库：通知父组件更新全局当前知识库（与首页对话区同步）
function handleKbChange(kbIdValue) {
  const kb = props.kbList.find((k) => k.id === kbIdValue)
  if (kb) emit('select-kb', kb)
}

// 监听抽屉打开 / 知识库变化：重新加载文档列表
watch(
  [() => props.visible, () => props.kbId],
  async ([v]) => {
    if (v && props.kbId) {
      await loadDocs()
      syncPollingWithDocs()
    } else {
      stopPolling()
    }
  }
)

// 组件卸载时清理轮询定时器
onBeforeUnmount(() => {
  stopPolling()
})

// 加载当前知识库的文档列表
async function loadDocs() {
  tableLoading.value = true
  try {
    documents.value = await listDocuments(props.kbId)
    selectedDocRows.value = selectedDocRows.value.filter((row) =>
      documents.value.some((doc) => doc.id === row.id),
    )
  } finally {
    tableLoading.value = false
  }
}

// 上传进度回调（el-upload 触发，evt.percent 是 0-100 上传进度）
function handleProgress(evt) {
  uploadPercent.value = Math.round(evt.percent || 0)
}

// 上传前校验（返回 false 阻止上传；我们手动处理）
function handleBeforeUpload(file) {
  if (!props.kbId) {
    ElMessage.warning('请先选择知识库')
    return false
  }
  const allowed = ['.txt', '.pdf', '.docx', '.csv', '.xlsx', '.xls']
  const ext = '.' + file.name.split('.').pop().toLowerCase()
  if (!allowed.includes(ext)) {
    ElMessage.error('不支持的文件类型，仅支持 .txt / .pdf / .docx / .csv / .xlsx')
    return false
  }
  // 大小校验：超大文件后台解析/向量化会长时间卡在"解析中"，前端先拦截
  const MAX_MB = 20
  if (file.size > MAX_MB * 1024 * 1024) {
    ElMessage.error(`文件超过大小限制 ${MAX_MB}MB，请拆分后上传`)
    return false
  }

  // 显示上传进度
  uploading.value = file.name
  uploadPercent.value = 0

  // 调用上传接口（axios onUploadProgress 实时上报进度）
  uploadDocument(file, props.kbId, (percent) => {
    uploadPercent.value = percent
  })
    .then((res) => {
      uploadPercent.value = 100
      ElMessage.success(`「${file.name}」已上传，正在解析...`)
      // 上传成功后：立即刷新列表（新文档以"解析中"出现）+ 启动轮询
      loadDocs()
      startPolling()
    })
    .catch(() => {
      /* 错误已由拦截器提示 */
    })
    .finally(() => {
      // 短暂停留 100% 后收起进度条
      setTimeout(() => {
        uploading.value = null
        uploadPercent.value = 0
      }, 800)
    })
  return false   // 阻止 el-upload 默认上传（我们手动处理）
}

// 轮询文档状态：每次都更新列表（"解析中"→"完成"实时可见）
function startPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    try {
      const docs = await listDocuments(props.kbId)
      documents.value = docs   // 每次轮询都更新，状态实时变化
      if (!docs.some((d) => d.status === 'processing')) {
        stopPolling()
        ElMessage.success('文档解析完成')
      }
    } catch (error) {
      stopPolling()
    }
  }, 1500)
  // 最多轮询 400 次（10 分钟），线上文档会排队逐个处理
  setTimeout(() => {
    stopPolling()
  }, 600000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function syncPollingWithDocs() {
  if (hasProcessingDocs.value) startPolling()
  else stopPolling()
}

function handleSelectionChange(rows) {
  selectedDocRows.value = rows
}

// 删除文档（带确认）
async function handleDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除「${row.filename}」？其向量数据也会被删除。`, '提示', {
      type: 'warning',
    })
  } catch (e) {
    return
  }
  const previousDocs = documents.value
  documents.value = documents.value.filter((doc) => doc.id !== row.id)
  selectedDocRows.value = selectedDocRows.value.filter((doc) => doc.id !== row.id)
  try {
    await deleteDocument(row.id)
    ElMessage.success('已删除')
    await loadDocs()
    syncPollingWithDocs()
  } catch (e) {
    documents.value = previousDocs
    await loadDocs()
  }
}

async function handleBatchDelete() {
  const rows = selectedDocRows.value
  if (!rows.length) return
  try {
    await ElMessageBox.confirm(`确定删除选中的 ${rows.length} 个文档？其向量数据也会被删除。`, '批量删除', {
      type: 'warning',
    })
  } catch (e) {
    return
  }
  const ids = rows.map((row) => row.id)
  const previousDocs = documents.value
  documents.value = documents.value.filter((doc) => !ids.includes(doc.id))
  selectedDocRows.value = []
  batchDeleting.value = true
  try {
    await deleteDocuments(ids)
    ElMessage.success(`已删除 ${rows.length} 个文档`)
    await loadDocs()
    syncPollingWithDocs()
  } catch (e) {
    documents.value = previousDocs
    await loadDocs()
  } finally {
    batchDeleting.value = false
  }
}

// 工具函数
function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}
function statusText(s) {
  return { processing: '解析中', done: '完成', failed: '失败' }[s] || s
}
function statusType(s) {
  return { processing: 'warning', done: 'success', failed: 'danger' }[s] || 'info'
}
function docStatusPercent(doc) {
  if (doc.status === 'done') return 100
  if (doc.status === 'failed') return 100
  if (doc.status === 'processing') return doc.chunk_count > 0 ? 72 : 42
  return 0
}
</script>

<style scoped>
.upload-area {
  margin-bottom: 16px;
}
.upload-area :deep(.el-upload-dragger) {
  position: relative;
  overflow: hidden;
  border: 1px dashed rgba(36, 84, 214, 0.34);
  border-radius: 22px;
  background:
    radial-gradient(circle at 82% 8%, rgba(37, 99, 235, 0.13), transparent 34%),
    linear-gradient(135deg, #ffffff, #f3f7ff);
  transition: all var(--kb-duration) var(--kb-ease);
  padding: 30px 24px;
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.07);
}

.upload-area :deep(.el-upload-dragger::after) {
  content: '';
  position: absolute;
  left: 24px;
  right: 24px;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.42), rgba(15, 118, 110, 0.2), transparent);
}

.upload-area :deep(.el-upload-dragger:hover) {
  border-color: var(--kb-primary);
  background:
    radial-gradient(circle at 82% 8%, rgba(37, 99, 235, 0.17), transparent 34%),
    linear-gradient(135deg, #ffffff, #eef5ff);
  transform: translateY(-2px);
}
.upload-inner {
  display: flex;
  align-items: center;
  gap: 18px;
  text-align: left;
}
.upload-icon {
  width: 56px;
  height: 56px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 18px;
  background: linear-gradient(145deg, #0f1117, #182033 65%, #2454d6 145%);
  font-size: 25px;
  color: var(--kb-primary);
  color: #fff;
  box-shadow: 0 12px 24px rgba(20, 23, 31, 0.14);
}
.upload-text {
  font-size: 17px;
  color: var(--kb-text);
  font-weight: 820;
}
.upload-text em {
  color: var(--kb-primary);
  font-style: normal;
  font-weight: 500;
}
.upload-tip {
  max-width: 620px;
  font-size: 13px;
  color: var(--kb-text-muted);
  margin-top: 7px;
  line-height: 1.7;
}
/* 上传进度条 */
.upload-progress {
  margin-bottom: 16px;
  padding: 14px 16px;
  background: #fff;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 16px;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.06);
}
.up-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  font-size: 13px;
}
.up-file {
  color: var(--kb-text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  max-width: 80%;
}
.up-pct {
  color: var(--kb-primary);
  font-weight: 600;
}
.kb-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--kb-text-secondary);
  margin-bottom: 16px;
  padding: 12px 14px;
  background: linear-gradient(90deg, #fff, #f8fafc);
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 16px;
  box-shadow: 0 14px 34px rgba(15, 23, 42, 0.05);
}
.kb-hint-label {
  flex-shrink: 0;
  font-weight: 500;
}
.doc-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 12px 0 8px;
  border-bottom: 1px solid var(--kb-border-light);
}
.doc-toolbar strong {
  display: block;
  color: var(--kb-text);
  font-size: 14px;
}
.doc-toolbar span {
  display: block;
  margin-top: 2px;
  color: var(--kb-text-muted);
  font-size: 12px;
}
.empty-list {
  text-align: center;
  color: var(--kb-text-muted);
  padding: 30px 0;
  font-size: 13px;
}

.doc-status-box {
  display: grid;
  gap: 6px;
}

.failure-reason-btn {
  width: fit-content;
  padding: 0;
  border: 0;
  background: transparent;
  color: #ef4444;
  font: inherit;
  font-size: 11px;
  cursor: help;
}

.doc-progress {
  width: 54px;
  height: 5px;
  display: block;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(226, 232, 240, 0.85);
}

.doc-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  transition: width 0.24s var(--kb-ease);
}

.doc-progress span.done {
  background: #22c55e;
}

.doc-progress span.processing {
  background: #60a5fa;
}

.doc-progress span.failed {
  background: #fb7185;
}

:deep(.el-drawer) {
  background:
    radial-gradient(circle at 80% 0%, rgba(37, 99, 235, 0.1), transparent 28%),
    linear-gradient(180deg, #fbfcfe, #f4f8fd);
}

:deep(.el-drawer__header) {
  margin-bottom: 14px;
  padding: 22px 24px 18px;
  border-bottom: 1px solid rgba(23, 26, 34, 0.08);
  color: var(--kb-text);
  font-weight: 800;
}

:deep(.el-table) {
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  overflow: hidden;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.055);
}
</style>
