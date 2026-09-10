<!--
  Sidebar.vue：左栏
  ┌──────────────────┐
  │  知识库下拉 + 管理  │  ← 顶部：选知识库 + 新建/删除
  │  ──────────────  │
  │  会话列表          │  ← 中部：切换会话
  │  [新建会话]        │
  │  ──────────────  │
  │  [文档管理]        │  ← 底部：打开文档抽屉
  └──────────────────┘
-->
<template>
  <!-- 左侧栏容器：收起时主体隐藏，但边缘按钮保留 -->
  <aside class="sidebar-wrap" :class="{ collapsed }">
    <aside class="sidebar saas-sidebar" :class="isAdmin ? 'admin-sidebar' : 'user-sidebar'">
      <div class="workspace-card">
        <div class="workspace-icon">企</div>
        <div class="workspace-copy">
          <div class="workspace-name">企业知识空间</div>
          <div class="workspace-meta">面向知识治理与内部问答</div>
        </div>
      </div>

      <!-- 顶部导航：概览 / 知识库 -->
      <div v-if="isAdmin" class="section-label">主导航</div>
      <div class="sidebar-nav">
        <div
          v-if="isAdmin"
          class="nav-item"
          :class="{ active: viewMode === 'dashboard' }"
          @click="$emit('show-dashboard')"
        >
          <el-icon><DataAnalysis /></el-icon>&nbsp;概览
        </div>
        <div
          class="nav-item"
          :class="{ active: viewMode === 'chat' }"
          @click="$emit('show-chat')"
        >
          <el-icon><ChatDotRound /></el-icon>&nbsp;对话
        </div>
      </div>

      <!-- 顶部：知识库选择 -->
      <div class="sidebar-header">
        <div class="section-head">
          <span>检索范围</span>
          <small>{{ selectedKbIds.length || 0 }} / {{ kbList.length }}</small>
        </div>
        <div class="kb-select-row" :class="{ multi: selectedKbIds.length > 1 }">
          <!-- 知识库多选下拉：勾选哪些库，检索就只在这些库中 -->
          <el-select
            :model-value="selectedKbIds"
            class="kb-multi-select"
            multiple
            :collapse-tags="selectedKbIds.length > 1"
            collapse-tags-tooltip
            :max-collapse-tags="1"
            placeholder="选择知识库"
            @change="handleKbChange"
          >
            <el-option
              v-for="kb in kbList"
              :key="kb.id"
              :label="kb.name"
              :value="kb.id"
            />
          </el-select>
          <div v-if="selectedKbIds.length > 1" class="kb-multi-overlay">
            {{ selectedKbSummary }}
          </div>
        </div>
        <div class="kb-selected-summary">
          {{ selectedKbDetail }}
        </div>
        <div v-if="isAdmin" class="kb-action-row">
          <!-- 新建知识库按钮 -->
          <button class="kb-action primary" type="button" @click="showCreateKb" title="新建知识库">
            <el-icon><Plus /></el-icon>
            <span>新建</span>
          </button>
          <!-- 删除知识库按钮（删除当前选中的知识库） -->
          <button
            class="kb-action danger"
            type="button"
            :disabled="!currentKb"
            @click="handleDeleteKb"
            title="删除当前知识库"
          >
            <el-icon><Delete /></el-icon>
            <span>删除</span>
          </button>
        </div>

        <div v-if="createKbVisible" class="create-kb-popover">
          <div class="create-title">新建知识库</div>
          <el-input v-model="newKbName" size="small" placeholder="输入知识库名称" @keydown.enter="handleCreateKb" />
          <div class="create-actions">
            <button type="button" class="create-btn ghost" @click="createKbVisible = false">取消</button>
            <button type="button" class="create-btn primary" @click="handleCreateKb">创建</button>
          </div>
        </div>
      </div>

    <!-- 会话列表标题 + 新建按钮 -->
    <div class="conv-header">
      <span class="conv-title">最近会话</span>
      <button class="new-conv-btn" type="button" @click="$emit('new-conversation')">
        <el-icon><Plus /></el-icon>
        <span>新建</span>
      </button>
    </div>

    <!-- 会话列表 -->
    <div class="conv-list">
      <div
        v-for="conv in conversations"
        :key="conv.id"
        class="conv-item"
        :class="{ active: currentConversation?.id === conv.id }"
        @click="$emit('select-conversation', conv)"
      >
        <!-- 会话标题：超长省略号 -->
        <span class="conv-name">{{ conv.title }}</span>
        <!-- 删除会话按钮 -->
        <el-button
          v-if="isAdmin"
          class="conv-del"
          size="small"
          text
          type="danger"
          :loading="deletingId === conv.id"
          @click.stop="handleDeleteConv(conv)"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
      </div>
      <!-- 空状态 -->
      <div v-if="conversations.length === 0" class="conv-empty">
        暂无会话，点击"新建会话"开始
      </div>
    </div>

    <!-- 底部：文档管理入口 -->
    <div v-if="isAdmin" class="sidebar-footer">
      <el-button class="doc-btn" plain @click="$emit('open-doc-manager')">
        <el-icon><FolderOpened /></el-icon>&nbsp;文档管理
      </el-button>
    </div>
  </aside>

  <!-- 侧栏边缘折叠/展开按钮：展开时贴在侧栏右侧中部，收起后留在页面左边缘中部 -->
  <div class="sidebar-toggle-rail">
    <button class="sidebar-toggle" type="button" @click="$emit('toggle-sidebar')">
      <el-icon v-if="!collapsed"><ArrowLeft /></el-icon>
      <el-icon v-else><ArrowRight /></el-icon>
    </button>
  </div>
</aside>
</template>

<script setup>
// props：父组件(App)传下来的数据；emits：向父组件抛事件
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  deleteConversation,
} from '../api/api.js'

const props = defineProps({
  kbList: { type: Array, default: () => [] },
  currentKb: { type: Object, default: null },
  selectedKbIds: { type: Array, default: () => [] },  // 勾选的知识库 id 列表
  conversations: { type: Array, default: () => [] },
  currentConversation: { type: Object, default: null },
  collapsed: { type: Boolean, default: false },   // 左侧栏是否收起
  viewMode: { type: String, default: 'chat' },   // 主区视图：chat / dashboard
  isAdmin: { type: Boolean, default: false },
})
const emit = defineEmits([
  'select-kb',
  'select-kbs',          // 多选知识库变化：传 id 数组
  'select-conversation',
  'new-conversation',
  'open-doc-manager',
  'refresh-kbs',          // 知识库列表变化后通知父组件刷新
  'delete-conversation',   // 删除会话：传会话 id，父组件本地移除
  'toggle-sidebar',        // 折叠/展开侧栏
  'show-dashboard',        // 切换到概览页
  'show-chat',             // 切换到对话区
])

// 删除中会话 id（用于按钮 loading 反馈）
const deletingId = ref(null)

// 新建知识库相关状态
const createKbVisible = ref(false)
const newKbName = ref('')

const selectedKbSummary = computed(() => {
  const ids = props.selectedKbIds || []
  const names = props.kbList.filter((kb) => ids.includes(kb.id)).map((kb) => kb.name)
  if (names.length === 0) return '未选择知识库'
  if (names.length === 1) return names[0]
  return `已选择 ${names.length} 个知识库`
})

const selectedKbDetail = computed(() => {
  const ids = props.selectedKbIds || []
  const names = props.kbList.filter((kb) => ids.includes(kb.id)).map((kb) => kb.name)
  if (names.length === 0) return '未选择知识库'
  return names.join('、')
})

// 知识库多选变化：把勾选的 id 数组发给父组件
function handleKbChange(ids) {
  emit('select-kbs', ids || [])
}

// 打开新建知识库弹窗
function showCreateKb() {
  newKbName.value = ''
  createKbVisible.value = true
}

// 创建知识库
async function handleCreateKb() {
  if (!newKbName.value.trim()) return
  try {
    await createKnowledgeBase(newKbName.value.trim())
    ElMessage.success('创建成功')
    createKbVisible.value = false
    emit('refresh-kbs')   // 通知父组件重新加载知识库列表
  } catch (e) {
    /* 错误已由拦截器统一提示 */
  }
}

// 删除当前知识库（级联删除其文档和向量数据）
async function handleDeleteKb() {
  const kb = props.currentKb
  if (!kb) return
  try {
    await ElMessageBox.confirm(
      `确定删除知识库「${kb.name}」？其下所有文档和向量数据将被删除，不可恢复。`,
      '删除知识库',
      { type: 'warning', confirmButtonText: '删除', confirmButtonClass: 'el-button--danger' }
    )
    await deleteKnowledgeBase(kb.id)
    ElMessage.success('已删除')
    emit('refresh-kbs')   // 通知父组件刷新列表（并自动切到剩余第一个）
  } catch (e) {
    /* 用户取消则不处理 */
  }
}

// 删除会话（带确认 + 删除中 loading，完成后本地移除不重拉全量）
async function handleDeleteConv(conv) {
  try {
    await ElMessageBox.confirm(`确定删除会话"${conv.title}"？`, '提示', {
      type: 'warning',
    })
    deletingId.value = conv.id   // 按钮转圈，反馈"正在删"
    await deleteConversation(conv.id)
    deletingId.value = null
    ElMessage.success('已删除')
    emit('delete-conversation', conv.id)   // 父组件本地移除，立即生效
  } catch (e) {
    deletingId.value = null
    /* 用户取消则不处理 */
  }
}

defineExpose({}) // 无对外暴露
</script>

<style scoped>
.sidebar-wrap {
  position: relative;
  width: 264px;
  height: 100%;
  flex-shrink: 0;
  transition: width 0.2s var(--kb-ease);
}
.sidebar-wrap.collapsed {
  width: 0;
}
.sidebar {
  width: 264px;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at 24% 0%, rgba(37, 99, 235, 0.14), transparent 34%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(244, 248, 255, 0.86) 100%);
  border: 1px solid rgba(255, 255, 255, 0.76);
  border-radius: 22px;
  height: 100%;
  overflow: hidden;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.11);
  backdrop-filter: blur(16px);
  transition: transform 0.2s var(--kb-ease), opacity 0.2s var(--kb-ease);
}
.sidebar-wrap.collapsed .sidebar {
  transform: translateX(-100%);
  opacity: 0;
  pointer-events: none;
}

/* 侧栏边缘控制轨道：固定在页面左侧中部，避免按钮遮住会话标题 */
.sidebar-toggle-rail {
  position: fixed;
  left: 292px;
  top: 88px;
  bottom: 24px;
  width: 26px;
  display: flex;
  align-items: center;     /* 垂直居中 */
  justify-content: center;
  pointer-events: none;    /* 只让按钮本身可点 */
  z-index: 20;
  transition: left 0.2s var(--kb-ease);
}
.sidebar-wrap.collapsed .sidebar-toggle-rail {
  left: 12px;
}

/* 边缘折叠按钮：企业级小控制柄 */
.sidebar-toggle {
  pointer-events: auto;
  width: 26px;
  height: 54px;
  border: 1px solid rgba(255, 255, 255, 0.72);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 12px 32px rgba(15, 23, 42, 0.12);
  color: var(--kb-primary-dark);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s var(--kb-ease);
}
.sidebar-toggle:hover {
  color: var(--kb-primary);
  border-color: var(--kb-primary-light);
  box-shadow: var(--kb-shadow);
}
.sidebar-toggle :deep(svg) {
  width: 16px;
  height: 16px;
}


.workspace-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 4px 4px 12px;
  padding: 14px;
  border: 1px solid rgba(37, 99, 235, 0.1);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(239, 246, 255, 0.72));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.86), 0 12px 30px rgba(37, 99, 235, 0.055);
}

.workspace-icon {
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  color: #fff;
  background: linear-gradient(145deg, #0f1117, #2454d6 120%);
  font-size: 13px;
  font-weight: 760;
}

.workspace-copy {
  min-width: 0;
}

.workspace-name {
  font-size: 13px;
  font-weight: 760;
  color: var(--kb-text);
}

.workspace-meta {
  margin-top: 2px;
  font-size: 10px;
  color: var(--kb-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar.user-sidebar .workspace-card {
  margin-bottom: 8px;
}

.sidebar.user-sidebar .sidebar-nav {
  padding-top: 2px;
}

.sidebar.user-sidebar .sidebar-header {
  padding-top: 12px;
}

.sidebar.user-sidebar .conv-list {
  padding-bottom: 8px;
}

.section-label {
  margin: 16px 12px 7px;
  color: var(--kb-text-muted);
  font-size: 11px;
  font-weight: 760;
  letter-spacing: 0;
}

.sidebar-header {
  padding: 14px 8px;
  border-bottom: 1px solid var(--kb-border-light);
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 700;
}

.section-head small {
  color: var(--kb-text-muted);
  font-size: 11px;
}

/* 顶部导航（概览/对话） */
.sidebar-nav {
  display: grid;
  gap: 6px;
  padding: 0 8px;
}
.nav-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 10px;
  border-radius: 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--kb-text-secondary);
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
  background: transparent;
}
.nav-item:hover {
  background: rgba(255, 255, 255, 0.84);
  color: var(--kb-primary-dark);
}
.nav-item.active {
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.98), rgba(219, 234, 254, 0.72));
  color: var(--kb-primary-dark);
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.16), 0 12px 26px rgba(37, 99, 235, 0.08);
}
.kb-select-row {
  display: block;
  position: relative;
}
.kb-multi-select {
  width: 100%;
}
.kb-select-row :deep(.el-select__wrapper) {
  min-height: 34px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.95);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.24) inset;
}
.kb-select-row:not(.multi) :deep(.el-select__selection) {
  flex-wrap: nowrap;
}
.kb-select-row.multi :deep(.el-select__selection) {
  opacity: 0;
}
.kb-multi-overlay {
  position: absolute;
  top: 50%;
  left: 11px;
  right: 34px;
  transform: translateY(-50%);
  color: var(--kb-text);
  font-size: 12px;
  font-weight: 680;
  overflow: hidden;
  pointer-events: none;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kb-selected-summary {
  margin-top: 6px;
  color: var(--kb-text-muted);
  font-size: 11px;
  line-height: 1.4;
}
.kb-action-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 9px;
}
.kb-action {
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid var(--kb-border);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.84);
  color: var(--kb-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}
.kb-action:hover {
  transform: translateY(-1px);
  box-shadow: var(--kb-shadow-sm);
}
.kb-action.primary {
  color: var(--kb-primary-dark);
  border-color: rgba(37, 99, 235, 0.18);
  background: var(--kb-primary-faint);
}
.kb-action.danger {
  color: #b91c1c;
  border-color: rgba(239, 68, 68, 0.18);
  background: rgba(254, 242, 242, 0.72);
}
.kb-action:disabled {
  opacity: 0.42;
  cursor: not-allowed;
  transform: none;
}
.create-kb-popover {
  margin-top: 10px;
  padding: 10px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: var(--kb-radius);
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 18px 42px rgba(15, 23, 42, 0.22);
  animation: kb-float-in 0.2s var(--kb-ease) both;
}
.create-title {
  margin-bottom: 6px;
  color: var(--kb-text);
  font-size: 12px;
  font-weight: 760;
}
.create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
  margin-top: 8px;
}
.create-btn {
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--kb-border);
  border-radius: 7px;
  background: #fff;
  color: var(--kb-text-secondary);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
.create-btn.primary {
  color: #fff;
  border-color: var(--kb-primary);
  background: var(--kb-primary);
}

.conv-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 7px 7px;
}
.conv-title {
  font-weight: 700;
  color: var(--kb-text-secondary);
  font-size: 13px;
  letter-spacing: 0;
}
.new-conv-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 30px;
  padding: 0 9px;
  border: 1px solid rgba(37, 99, 235, 0.18);
  background: var(--kb-primary-faint);
  color: var(--kb-primary-dark);
  font: inherit;
  font-size: 12px;
  font-weight: 700;
  border-radius: var(--kb-radius);
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}
.new-conv-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--kb-shadow-sm);
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 2px 6px;
}
.conv-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  margin-bottom: 6px;
  border-radius: 12px;
  color: var(--kb-text-secondary);
  cursor: pointer;
  transition: background var(--kb-duration) var(--kb-ease), transform var(--kb-duration) var(--kb-ease), box-shadow var(--kb-duration) var(--kb-ease);
  position: relative;
}
.conv-item:hover {
  background: rgba(255, 255, 255, 0.82);
  color: var(--kb-text);
  box-shadow: var(--kb-shadow-sm);
  transform: translateY(-1px);
}
.conv-item.active {
  background: var(--kb-primary-faint);
  color: var(--kb-primary-dark);
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.14);
}
/* 选中态左侧小竖条（克制强调） */
.conv-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 20%;
  bottom: 20%;
  width: 3px;
  border-radius: 2px;
  background: var(--kb-primary);
}
.conv-name {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;   /* 超长标题省略号 */
  font-size: 13px;
}
.conv-item.active .conv-name {
  font-weight: 500;
}
.conv-del {
  visibility: hidden;   /* 默认隐藏删除按钮，hover 时显示 */
  margin-left: 8px;
}
.conv-item:hover .conv-del {
  visibility: visible;
}
.conv-empty {
  text-align: center;
  color: var(--kb-text-muted);
  font-size: 13px;
  padding: 30px 0;
}

.sidebar-footer {
  padding: 7px;
  border-top: 1px solid var(--kb-border-light);
}
.doc-btn {
  width: 100%;
  height: 34px;
  border-radius: 10px;
  border-color: rgba(37, 99, 235, 0.16);
  background: rgba(255, 255, 255, 0.72);
  color: var(--kb-primary-dark);
}
</style>
