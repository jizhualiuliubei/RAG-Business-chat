<!--
  App.vue：应用根组件，三栏式布局骨架
  ┌──────────┬───────────────────────────┐
  │ Sidebar  │  ChatArea                 │
  │ (左栏)   │  (中间对话区)              │
  └──────────┴───────────────────────────┘

  职责：
  1. 持有"当前知识库"和"当前会话"这两个全局状态
  2. 把这些状态通过 props 传给子组件
  3. 监听子组件的事件，更新状态
-->
<template>
  <LoginView v-if="authReady && !authUser" @login-success="handleLoginSuccess" />
  <div v-else-if="authReady" class="enterprise-shell" :class="{ collapsed: sidebarCollapsed, 'admin-route': route.name === 'admin' }">
    <header class="enterprise-topbar">
      <div class="brand-zone">
        <div class="brand-mark" aria-label="AI 企业知识库工作台">
          <span class="brand-letter">W</span>
        </div>
        <div class="brand-copy">
          <div class="brand-title">{{ shellBrandTitle }}</div>
        </div>
      </div>

      <div class="topbar-actions">
        <div class="status-chip">
          <span class="live-dot"></span>
          <span>服务在线</span>
        </div>
        <div class="status-chip strong">{{ selectedKbIds.length || 0 }} 个知识库参与检索</div>
        <button v-if="isAdmin" class="topbar-admin-link" type="button" @click="goAdmin">
          <el-icon><Management /></el-icon>
          <span>管理中心</span>
        </button>
        <el-dropdown trigger="click" @command="handleUserCommand">
          <button class="user-menu" type="button">
            <span class="user-avatar-mini">{{ authUser?.display_name?.slice(0, 1) || '用' }}</span>
            <span class="user-meta">
              <strong>{{ authUser?.display_name }}</strong>
              <small>{{ roleLabel }}</small>
            </span>
            <el-icon><ArrowDown /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item disabled>{{ authUser?.username }}</el-dropdown-item>
              <el-dropdown-item command="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </header>

    <AdminView
      v-if="route.name === 'admin' && isAdmin"
      :current-user="authUser"
      @back-to-home="goHome"
      @refresh-kbs="handleAdminKnowledgeRefresh"
    />

    <div v-else class="enterprise-body">
      <!-- 左栏：知识库选择 + 会话列表 + 文档管理入口 -->
      <Sidebar
        :kb-list="kbList"
        :current-kb="currentKb"
        :selected-kb-ids="selectedKbIds"
        :conversations="conversations"
        :current-conversation="currentConversation"
        :collapsed="sidebarCollapsed"
        :view-mode="viewMode"
        :is-admin="isAdmin"
        @select-kb="handleSelectKb"
        @select-kbs="handleSelectKbs"
        @select-conversation="handleSelectConversation"
        @new-conversation="handleNewConversation"
        @open-doc-manager="openDocManager"
        @refresh-kbs="loadKnowledgeBases"
        @delete-conversation="handleDeleteConversation"
        @toggle-sidebar="sidebarCollapsed = !sidebarCollapsed"
        @show-dashboard="goDashboard"
        @show-chat="goHome"
      />

      <section class="workspace-stage">
        <!-- 中间主区：概览页 / 对话区 切换 -->
        <Dashboard
          v-if="viewMode === 'dashboard'"
          :current-kb="currentKb"
          @back-to-chat="goHome"
          @select-kb="handleDashboardSelectKb"
          @select-conversation="handleDashboardSelectConversation"
        />
        <ChatArea
          v-else
          :current-conversation="currentConversation"
          :current-kb="currentKb"
          :selected-kb-ids="selectedKbIds"
          :kb-list="kbList"
          :messages="messages"
          :loading="loading"
          :is-admin="isAdmin"
          @send="handleSend"
          @cancel="handleCancel"
        />
      </section>
    </div>

    <!-- 文档管理抽屉：从左侧栏入口打开 -->
    <DocManager
      v-if="isAdmin"
      :visible="docDrawerVisible"
      :kb-id="currentKb?.id"
      :kb-name="currentKb?.name || ''"
      :kb-list="kbList"
      @select-kb="handleSelectKb"
      @update:visible="docDrawerVisible = $event"
    />
  </div>
  <div v-else class="auth-boot-screen">
    <div class="brand-mark"><span class="brand-letter">W</span></div>
    <span>正在验证登录状态...</span>
  </div>
</template>

<script setup>
// Vue 组合式 API：用 ref/reactive 管理状态
import { computed, ref, onMounted, onUnmounted } from 'vue'
import { watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import Sidebar from './components/Sidebar.vue'
import ChatArea from './components/ChatArea.vue'
import DocManager from './components/DocManager.vue'
import Dashboard from './components/Dashboard.vue'
import LoginView from './components/LoginView.vue'
import AdminView from './components/AdminView.vue'
import {
  getKnowledgeBases,
  listConversations,
  createConversation,
  getConversationMessages,
  saveConversationTurn,
  askStream,
  getMe,
  logout as logoutSession,
} from './api/api.js'
import { clearAuthToken, getAuthToken } from './api/index.js'

// ============ 状态 ============
const kbList = ref([])            // 知识库列表
const currentKb = ref(null)       // 当前选中的知识库（概览/文档管理高亮用）
const selectedKbIds = ref([])     // 勾选参与检索的知识库 id 列表（多选，默认第一个）
const conversations = ref([])     // 会话列表
const currentConversation = ref(null) // 当前会话（对象，含 id 和 title）
const messages = ref([])          // 当前会话的消息列表
const loading = ref(false)        // 是否正在等待 AI 回答
const docDrawerVisible = ref(false) // 文档管理抽屉是否打开
const sidebarCollapsed = ref(false) // 左侧栏是否收起（默认展开，刷新不记住）
const authReady = ref(false)      // 是否已完成登录态恢复
const authUser = ref(null)        // 当前登录用户
const roleLabel = computed(() => {
  if (authUser.value?.role === 'system_admin' || authUser.value?.role === 'admin') return '系统管理员'
  if (authUser.value?.role === 'enterprise_admin') return '企业管理员'
  return '企业员工'
})
const isAdmin = computed(() => ['admin', 'system_admin', 'enterprise_admin'].includes(authUser.value?.role))
const shellBrandTitle = computed(() => {
  if (authUser.value?.enterprise_code === 'system' || authUser.value?.role === 'system_admin') {
    return 'AI 企业知识库工作台'
  }
  return authUser.value?.enterprise_name || 'AI 企业知识库工作台'
})
const router = useRouter()
const route = useRoute()
const viewMode = computed(() => isAdmin.value && route.name === 'dashboard' ? 'dashboard' : 'chat')

// ============ 初始化 ============
onMounted(async () => {
  window.addEventListener('auth:expired', resetAuthState)
  await restoreSession()
})

onUnmounted(() => {
  window.removeEventListener('auth:expired', resetAuthState)
})

async function restoreSession() {
  const token = getAuthToken()
  if (!token) {
    authReady.value = true
    if (route.name !== 'login') router.replace('/login')
    return
  }
  try {
    authUser.value = await getMe()
    await initializeWorkspace()
    if (route.name === 'admin' && !isAdmin.value) router.replace('/home')
    if (route.name === 'root' || route.name === 'login') router.replace('/home')
  } catch (e) {
    resetAuthState()
  } finally {
    authReady.value = true
  }
}

async function initializeWorkspace() {
  kbList.value = await getKnowledgeBases()
  if (kbList.value.length > 0) {
    currentKb.value = kbList.value[0]
    selectedKbIds.value = [kbList.value[0].id]
  }
  await loadConversations()
}

async function handleLoginSuccess(user) {
  authUser.value = user
  await initializeWorkspace()
  router.replace('/home')
}

function resetAuthState() {
  clearAuthToken()
  authUser.value = null
  kbList.value = []
  currentKb.value = null
  selectedKbIds.value = []
  conversations.value = []
  currentConversation.value = null
  messages.value = []
  loading.value = false
  if (route.name !== 'login') router.replace('/login')
}

async function handleLogout() {
  try {
    await logoutSession()
  } catch (e) {
    /* 退出时即使后端不可达，也应清理本地登录态 */
  }
  resetAuthState()
  ElMessage.success('已退出登录')
}

function handleUserCommand(command) {
  if (command === 'logout') handleLogout()
}

function goHome() {
  if (route.name !== 'home') router.push('/home')
}

function goDashboard() {
  if (!isAdmin.value) return
  if (route.name !== 'dashboard') router.push('/dashboard')
}

function goAdmin() {
  if (!isAdmin.value) return
  if (route.name !== 'admin') router.push('/admin')
}

function openDocManager() {
  if (!isAdmin.value) return
  docDrawerVisible.value = true
}
// 加载会话列表
async function loadConversations() {
  conversations.value = await listConversations()
}

// 删除会话：本地即时移除（不等重新拉全量，删除体验更流畅）
function handleDeleteConversation(convId) {
  conversations.value = conversations.value.filter((c) => c.id !== convId)
  // 如果删的是当前会话，清空对话区
  if (currentConversation.value?.id === convId) {
    currentConversation.value = null
    messages.value = []
  }
}

// 重新加载知识库列表（新建/删除后调用）
// 删除当前知识库后，自动选中剩余的第一个
async function loadKnowledgeBases(preferredKb = null) {
  const nextKbList = await getKnowledgeBases()
  kbList.value = nextKbList
  const ids = new Set(nextKbList.map((k) => k.id))
  selectedKbIds.value = selectedKbIds.value.filter((id) => ids.has(id))

  if (preferredKb?.id && ids.has(preferredKb.id)) {
    currentKb.value = nextKbList.find((k) => k.id === preferredKb.id)
    selectedKbIds.value = [preferredKb.id]
    currentConversation.value = null
    messages.value = []
    return
  }

  const refreshedCurrent = currentKb.value
    ? nextKbList.find((k) => k.id === currentKb.value.id)
    : null
  if (refreshedCurrent) {
    currentKb.value = refreshedCurrent
  } else {
    currentKb.value = nextKbList[0] || null
    currentConversation.value = null
    messages.value = []
  }
}

async function handleAdminKnowledgeRefresh(preferredKb = null) {
  await loadKnowledgeBases(preferredKb)
}

// ============ 知识库切换 ============
// 单选切换：只在该知识库内检索（清空多选，只保留这一个）
async function handleSelectKb(kb) {
  currentKb.value = kb
  selectedKbIds.value = [kb.id]
}

// 多选切换：勾选的多个知识库联合检索（未勾选库绝不参与）
function handleSelectKbs(ids) {
  selectedKbIds.value = ids || []
  // 同步 currentKb：优先取第一个勾选的作为"当前高亮库"
  if (ids && ids.length > 0) {
    const first = kbList.value.find((k) => k.id === ids[0])
    if (first) currentKb.value = first
  }
}

// 概览页点击知识库：切知识库 + 跳回对话区
async function handleDashboardSelectKb(kb) {
  currentKb.value = kb
  selectedKbIds.value = [kb.id]
  router.replace('/home')
}

// 概览页点击会话：先跳回对话区，再加载会话（加载异步，不阻塞跳转）
async function handleDashboardSelectConversation(conv) {
  await handleSelectConversation(conv)
}

// ============ 会话操作 ============
// 切换会话：加载该会话的历史消息
async function handleSelectConversation(conv) {
  if (route.name !== 'home') await router.push('/home')
  // 先切当前会话（标题立即更新），加载期间显示 loading
  currentConversation.value = conv
  loading.value = true
  messages.value = []
  try {
    const detail = await getConversationMessages(conv.id)
    // 后端返回 { id, title, messages }，直接替换消息列表
    messages.value = detail.messages
    currentConversation.value.title = detail.title
  } finally {
    loading.value = false
  }
}

// 新建会话
async function handleNewConversation() {
  // 如果已有进行中的新建，直接复用（防止"点新建后立刻发消息"时序竞争）
  if (creatingConvPromise) return creatingConvPromise
  creatingConvPromise = (async () => {
    const conv = await createConversation()
    // 插到列表最前面
    conversations.value.unshift(conv)
    currentConversation.value = conv
    messages.value = []
    return conv
  })()
  try {
    return await creatingConvPromise
  } finally {
    creatingConvPromise = null
  }
}

// 给消息生成稳定的 id（用于 v-for 的 key，避免切换时用索引导致整列重渲染）
let msgIdSeed = 0
function makeMessage(role, content, extra = {}) {
  // created_at：当前时间（ISO），用于消息气泡展示发送/回复时间
  return { id: `m${++msgIdSeed}`, role, content, sources: [], created_at: new Date().toISOString(), ...extra }
}

// 当前流式请求的控制器（用于"取消生成"）
let streamAbort = null
// 新建会话进行中的 Promise（防止"点新建后立刻发消息"误用旧会话 id）
let creatingConvPromise = null

// 发送消息（流式：SSE 打字机效果）
async function handleSend(text) {
  if (!selectedKbIds.value.length) {
    ElMessage.warning('请至少选择一个知识库后再提问')
    return
  }

  // 只在"当前没有会话"时才自动新建一个
  // （切换知识库不再清空会话，所以切库后提问仍在原会话，不会误开新会话）
  if (!currentConversation.value || !currentConversation.value.id) {
    await handleNewConversation()
  }
  // 如果有进行中的新建会话，等它完成后再取 id（避免时序竞争）
  if (creatingConvPromise) {
    await creatingConvPromise
  }
  const convId = currentConversation.value.id
  // 检索范围：勾选的多知识库 id 数组（未勾选库绝不参与）
  const kbId = [...selectedKbIds.value]

  // 1. 立即把用户消息 + 空的 AI 占位消息显示出来
  messages.value.push(makeMessage('user', text))
  messages.value.push(makeMessage('assistant', ''))
  // 关键：从响应式数组里取 AI 消息的【响应式代理】引用
  // 不能直接持有 push 前的普通对象 —— 改了不触发 Vue 重渲染
  const aiMsg = messages.value[messages.value.length - 1]
  loading.value = true

  // 2. 创建中断控制器（思考中点击"取消"按钮 abort）
  streamAbort = new AbortController()
  let canceled = false

  try {
    // 3. 流式读取 SSE，逐块更新 AI 消息（传 conversationId：后端读历史做上下文记忆）
    await askStream(text, kbId, {
      signal: streamAbort.signal,   // 支持取消
      conversationId: convId,       // 关键：同一会话的上文记忆
      onSources: (data) => {
        aiMsg.sources = data.sources || []
        aiMsg.knowledge_base_sources = data.knowledge_base_sources || []
        aiMsg.attachment_sources = data.attachment_sources || []
      },
      onText: (content) => {
        aiMsg.content += content
      },
    })
  } catch (err) {
    // 用户取消：静默处理（保留已生成的部分回答）
    if (err.name === 'AbortError' || streamAbort?.signal.aborted) {
      canceled = true
    } else {
      // 其他错误由拦截器统一提示
    }
  } finally {
    loading.value = false
    streamAbort = null

    // 3. 流结束后，把这轮问答落库（历史会话持久化 + 更新标题）
    //    用户取消（canceled）时不落库，避免把不完整的回答存进历史。
    if (!canceled) {
      try {
        await saveConversation(convId, text, aiMsg)
        // 刷新会话列表标题
        await loadConversations()
      } catch (e) {
        /* 落库失败不影响已展示的回答 */
      }
    }
  }
}

// 保存一轮问答到会话（只持久化流式已生成的回答，不重新生成）
// 关键：流式回答和落库内容是【同一个】——否则刷新后历史会话显示的是
// 另一次生成的、不记得上文的回答（AI 刷新后"失忆"的根因）
async function saveConversation(convId, question, aiMsg) {
  const detail = await saveConversationTurn(convId, {
    question,
    answer: aiMsg.content,
    kbId: currentKb.value?.id ?? 1,
    sources: aiMsg.sources,
  })
  currentConversation.value.title = detail.title
  // 模型信息：流式接口没返回 usage，这里用已有值或留空
  const lastMsg = messages.value[messages.value.length - 1]
  if (lastMsg && lastMsg.role === 'assistant') {
    lastMsg.model_name = aiMsg.model_name || ''
    lastMsg.usage = aiMsg.usage || {}
  }
}

// 取消当前生成（思考中点击"取消"按钮）
function handleCancel() {
  if (streamAbort) {
    streamAbort.abort()   // 中断 fetch 流式请求
  }
}

watch([() => route.name, isAdmin], ([name, admin]) => {
  if (authReady.value && authUser.value && name === 'admin' && !admin) {
    ElMessage.warning('当前账号无权限访问该功能')
    router.replace('/home')
  }
})
</script>

<style scoped>
.enterprise-shell {
  display: grid;
  grid-template-rows: 66px minmax(0, 1fr);
  height: 100vh;
  overflow: hidden;
  background:
    radial-gradient(circle at 18% 0%, rgba(37, 99, 235, 0.14), transparent 31%),
    radial-gradient(circle at 82% 4%, rgba(15, 118, 110, 0.1), transparent 28%),
    linear-gradient(135deg, #f7fbff 0%, #ffffff 43%, #eef5ff 100%);
  position: relative;
}

.enterprise-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image:
    linear-gradient(rgba(36, 84, 214, 0.032) 1px, transparent 1px),
    linear-gradient(90deg, rgba(36, 84, 214, 0.026) 1px, transparent 1px);
  background-size: 48px 48px;
  mask-image: linear-gradient(to bottom, black, transparent 78%);
}

.enterprise-shell::after {
  content: '';
  position: absolute;
  left: 290px;
  right: 28px;
  top: 66px;
  height: 1px;
  pointer-events: none;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.35), rgba(15, 118, 110, 0.22), transparent);
  opacity: 0.75;
}

.enterprise-topbar {
  display: grid;
  grid-template-columns: minmax(190px, 1fr) auto;
  align-items: center;
  gap: 16px;
  padding: 0 22px;
  border-bottom: 1px solid rgba(23, 26, 34, 0.08);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(22px);
  position: relative;
  z-index: 30;
}

.brand-zone {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.brand-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(20, 23, 31, 0.08);
  border-radius: 12px;
  background:
    linear-gradient(145deg, #0f1117 0%, #182033 58%, #2454d6 145%);
  box-shadow: 0 16px 32px rgba(20, 23, 31, 0.18);
}

.brand-letter {
  color: #fff;
  font-size: 16px;
  font-weight: 850;
  line-height: 1;
}

.brand-copy {
  min-width: 0;
}

.brand-title {
  font-size: 15px;
  font-weight: 820;
  color: var(--kb-text);
  line-height: 1.2;
}

.topbar-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}

.status-chip {
  height: 30px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 8px;
  border: 1px solid var(--kb-border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  color: var(--kb-text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.status-chip.strong {
  color: var(--kb-primary-dark);
  background: var(--kb-primary-faint);
  border-color: rgba(36, 84, 214, 0.16);
}

.topbar-admin-link {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 10px;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 999px;
  background: var(--kb-primary-faint);
  color: var(--kb-primary-dark);
  font: inherit;
  font-size: 12px;
  font-weight: 730;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}
.topbar-admin-link:hover {
  transform: translateY(-1px);
  box-shadow: var(--kb-shadow-sm);
  background: #fff;
}

.live-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--kb-accent-teal);
  animation: kb-soft-pulse 1.8s ease infinite;
}

.enterprise-body {
  display: flex;
  min-height: 0;
  padding: 18px;
  gap: 18px;
  position: relative;
  z-index: 1;
}

.workspace-stage {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: 22px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.88);
  box-shadow: 0 24px 70px rgba(20, 23, 31, 0.12);
  backdrop-filter: blur(18px);
  animation: kb-float-in 0.5s var(--kb-ease) both;
}

.enterprise-shell {
  transition: all 0.2s var(--kb-ease);
}


.user-menu {
  height: 34px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 0 9px 0 4px;
  border: 1px solid var(--kb-border);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.76);
  color: var(--kb-text);
  font: inherit;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}
.user-menu:hover {
  border-color: rgba(37, 99, 235, 0.2);
  box-shadow: var(--kb-shadow-sm);
}
.user-avatar-mini {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--kb-graphite);
  color: #fff;
  font-size: 12px;
  font-weight: 760;
}
.user-meta {
  display: grid;
  gap: 1px;
  text-align: left;
  line-height: 1;
}
.user-meta strong {
  font-size: 12px;
  font-weight: 760;
}
.user-meta small {
  color: var(--kb-text-muted);
  font-size: 10px;
}
.auth-boot-screen {
  min-height: 100vh;
  display: grid;
  place-content: center;
  gap: 12px;
  background: linear-gradient(135deg, #f8fbff, #ffffff 48%, #edf7ff);
  color: var(--kb-text-secondary);
}
@media (max-width: 820px) {
  .enterprise-topbar {
    grid-template-columns: 1fr;
  }
  .topbar-actions { display: none; }
  .enterprise-body {
    padding: 6px;
  }
}
</style>
