<template>
  <main class="login-light-shell">
    <img class="login-backdrop" src="../assets/visuals/login-enterprise-light-image2.png" alt="企业知识安全视觉" />
    <div class="login-grid"></div>
    <div class="login-beam"></div>
    <div class="login-orbit orbit-one"></div>
    <div class="login-orbit orbit-two"></div>

    <header class="login-titlebar" aria-label="项目标题">
      <div class="login-mark">W</div>
      <span>AI 企业知识库工作台</span>
    </header>

    <section class="login-hero">
      <div class="login-copy-veil" aria-hidden="true"></div>
      <div class="login-kicker">企业级知识智能入口</div>
      <h1>企业知识库智能问答平台</h1>
      <p>统一连接多知识库联合检索、引用溯源、权限隔离与持续对话能力，让团队在可信边界内快速获得有来源、有上下文的答案。</p>
      <div class="signal-row" aria-label="能力标签">
        <span>多知识库检索</span>
        <span>引用证据链</span>
        <span>权限访问</span>
        <span>持续对话</span>
      </div>
    </section>

    <section class="login-panel" aria-label="登录">
      <div class="panel-glow"></div>
      <div class="panel-head">
        <span class="panel-eyebrow">内部系统登录</span>
        <h2>欢迎回来</h2>
        <p>使用企业分配的账号访问工作台。</p>
      </div>

      <el-form class="login-form" @submit.prevent="handleSubmit">
        <label class="field-label">企业代码</label>
        <el-input v-model="form.enterprise_code" size="large" placeholder="系统管理员填 system，企业用户填企业代码" autocomplete="organization">
          <template #prefix><el-icon><OfficeBuilding /></el-icon></template>
        </el-input>

        <label class="field-label">用户名</label>
        <el-input v-model="form.username" size="large" placeholder="请输入用户名" autocomplete="username">
          <template #prefix><el-icon><User /></el-icon></template>
        </el-input>

        <label class="field-label">密码</label>
        <el-input v-model="form.password" size="large" placeholder="请输入密码" type="password" show-password autocomplete="current-password">
          <template #prefix><el-icon><Lock /></el-icon></template>
        </el-input>

        <label class="field-label">算术验证码</label>
        <div class="captcha-row">
          <el-input v-model="form.captcha_answer" size="large" placeholder="计算结果" @keydown.enter.prevent="handleSubmit">
            <template #prefix><el-icon><Key /></el-icon></template>
          </el-input>
          <button class="captcha-card" type="button" @click="refreshCaptcha" :class="{ refreshing: captchaLoading }">
            <span>{{ captcha.question || '加载中' }}</span>
            <el-icon><Refresh /></el-icon>
          </button>
        </div>

        <el-button class="login-submit" type="primary" size="large" :loading="submitting" @click="handleSubmit">
          登录工作台
        </el-button>
      </el-form>

      <button class="register-link" type="button" @click="registerVisible = true">
        企业管理员注册申请
      </button>

      <div v-if="showDevAccounts" class="account-hints">
        <span>体验账号：system / employee / employee123</span>
      </div>
    </section>

    <el-dialog v-model="registerVisible" title="企业管理员注册申请" width="520px" class="enterprise-register-dialog">
      <el-form label-position="top" class="register-form">
        <el-form-item>
          <template #label><span class="register-label"><b class="required-mark">*</b>企业名称</span></template>
          <el-input v-model="registerForm.enterprise_name" placeholder="例如：华东智造有限公司" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="required-mark">*</b>企业代码</span></template>
          <el-input v-model="registerForm.enterprise_code" placeholder="用于登录，例如：hd-zhizao" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="optional-mark">选填</b>联系人</span></template>
          <el-input v-model="registerForm.contact_name" placeholder="企业联系人姓名" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="optional-mark">选填</b>联系邮箱</span></template>
          <el-input v-model="registerForm.contact_email" placeholder="用于审核通知" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="optional-mark">选填</b>联系手机号</span></template>
          <el-input v-model="registerForm.contact_phone" placeholder="中国大陆 11 位手机号" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="required-mark">*</b>管理员用户名</span></template>
          <el-input v-model="registerForm.username" placeholder="审核通过后用于登录" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="required-mark">*</b>管理员显示名</span></template>
          <el-input v-model="registerForm.display_name" placeholder="例如：企业管理员" />
        </el-form-item>
        <el-form-item>
          <template #label><span class="register-label"><b class="required-mark">*</b>密码</span></template>
          <el-input v-model="registerForm.password" type="password" show-password placeholder="至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="registerVisible = false">取消</el-button>
        <el-button type="primary" :loading="registering" @click="handleRegister">提交申请</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { getCaptcha, login, registerEnterpriseAdmin } from '../api/api.js'
import { setAuthToken } from '../api/index.js'

const emit = defineEmits(['login-success'])

const form = reactive({
  enterprise_code: 'system',
  username: 'employee',
  password: 'employee123',
  captcha_answer: '',
})
const registerVisible = ref(false)
const registering = ref(false)
const registerForm = reactive({
  enterprise_name: '',
  enterprise_code: '',
  contact_name: '',
  contact_email: '',
  contact_phone: '',
  username: '',
  display_name: '',
  password: '',
})
const captcha = reactive({ captcha_id: '', question: '' })
const captchaLoading = ref(false)
const submitting = ref(false)
const showDevAccounts = import.meta.env.DEV

async function refreshCaptcha() {
  captchaLoading.value = true
  try {
    const data = await getCaptcha()
    captcha.captcha_id = data.captcha_id
    captcha.question = data.question
    form.captcha_answer = ''
  } finally {
    captchaLoading.value = false
  }
}

async function handleSubmit() {
  if (submitting.value) return
  if (!form.enterprise_code.trim() || !form.username.trim() || !form.password || !form.captcha_answer.trim()) {
    ElMessage.warning('请完整填写企业代码、用户名、密码和验证码')
    return
  }
  submitting.value = true
  try {
    const data = await login({
      enterprise_code: form.enterprise_code.trim(),
      username: form.username.trim(),
      password: form.password,
      captcha_id: captcha.captcha_id,
      captcha_answer: form.captcha_answer.trim(),
    })
    setAuthToken(data.access_token)
    emit('login-success', data.user)
    ElMessage.success('登录成功')
  } catch (e) {
    await refreshCaptcha()
  } finally {
    submitting.value = false
  }
}

async function handleRegister() {
  if (registering.value) return
  const validationMessage = validateEnterpriseRegisterForm()
  if (validationMessage) {
    ElMessage.warning(validationMessage)
    return
  }
  registering.value = true
  try {
    await registerEnterpriseAdmin({
      enterprise_name: registerForm.enterprise_name.trim(),
      enterprise_code: registerForm.enterprise_code.trim(),
      contact_name: registerForm.contact_name.trim(),
      contact_email: registerForm.contact_email.trim(),
      contact_phone: registerForm.contact_phone.trim(),
      username: registerForm.username.trim(),
      display_name: registerForm.display_name.trim(),
      password: registerForm.password,
    })
    registerVisible.value = false
    ElMessage.success('申请已提交，请等待系统管理员审核')
  } finally {
    registering.value = false
  }
}

function validateEnterpriseRegisterForm() {
  const code = registerForm.enterprise_code.trim().toLowerCase()
  const email = registerForm.contact_email.trim()
  const phone = registerForm.contact_phone.trim()
  if (!registerForm.enterprise_name.trim()) return '企业名称为必填项'
  if (!code) return '企业代码为必填项'
  if (!/^[a-z0-9_-]{2,60}$/.test(code)) return '企业代码只能包含小写字母、数字、横线或下划线，长度 2-60'
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return '联系邮箱格式不正确'
  if (phone && !/^1[3-9]\d{9}$/.test(phone)) return '联系手机号格式不正确'
  if (registerForm.username.trim().length < 3) return '管理员用户名至少需要 3 个字符'
  if (!registerForm.display_name.trim()) return '管理员显示名为必填项'
  if (registerForm.password.length < 6) return '密码至少需要 6 位'
  return ''
}

onMounted(refreshCaptcha)
</script>

<style scoped>
.login-light-shell {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  display: grid;
  grid-template-columns: minmax(420px, 1fr) 412px;
  align-items: center;
  gap: 48px;
  padding: 42px min(7vw, 88px);
  background: linear-gradient(135deg, #f8fafc 0%, #ffffff 44%, #eef6ff 100%);
  color: #111827;
}
.login-backdrop {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  object-position: 36% center;
  opacity: 0.72;
  filter: saturate(0.94) contrast(0.96) brightness(1.08);
  animation: login-backdrop-in 1s ease both, login-backdrop-breathe 9s ease-in-out infinite;
}
.login-light-shell::after {
  content: '';
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 78% 20%, rgba(37, 99, 235, 0.13), transparent 24%),
    radial-gradient(circle at 44% 74%, rgba(20, 184, 166, 0.1), transparent 28%),
    linear-gradient(90deg, rgba(255, 255, 255, 0.86) 0%, rgba(255, 255, 255, 0.78) 38%, rgba(255, 255, 255, 0.58) 62%, rgba(255, 255, 255, 0.92) 100%);
  pointer-events: none;
}
.login-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(37, 99, 235, 0.12) 1px, transparent 1px),
    linear-gradient(90deg, rgba(20, 184, 166, 0.1) 1px, transparent 1px);
  background-size: 42px 42px;
  mask-image: linear-gradient(to bottom, black, transparent 86%);
  opacity: 0.38;
  animation: login-grid-drift 11s linear infinite;
}
.login-beam {
  position: absolute;
  top: -12%;
  bottom: -12%;
  left: -22%;
  width: 22%;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.2), rgba(20, 184, 166, 0.18), transparent);
  transform: skewX(-16deg);
  mix-blend-mode: multiply;
  animation: login-beam 4.8s ease-in-out infinite;
}
.login-orbit {
  position: absolute;
  border: 1px solid rgba(37, 99, 235, 0.22);
  border-radius: 50%;
  box-shadow: 0 0 34px rgba(37, 99, 235, 0.12), inset 0 0 22px rgba(20, 184, 166, 0.08);
  pointer-events: none;
  z-index: 1;
}
.orbit-one {
  width: 330px;
  height: 330px;
  left: 24vw;
  top: 14vh;
  opacity: 0.34;
  animation: login-orbit 8s linear infinite;
}
.orbit-two {
  width: 190px;
  height: 190px;
  right: 23vw;
  bottom: 15vh;
  opacity: 0.72;
  animation: login-orbit 6.6s linear infinite reverse;
}
.login-hero,
.login-panel {
  position: relative;
  z-index: 2;
}
.login-hero {
  max-width: 620px;
  animation: login-panel-in 0.72s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
.login-copy-veil {
  position: absolute;
  inset: -34px -62px -36px -44px;
  z-index: -1;
  border-radius: 34px;
  background:
    radial-gradient(circle at 18% 42%, rgba(255, 255, 255, 0.96), rgba(255, 255, 255, 0.78) 42%, transparent 72%),
    linear-gradient(90deg, rgba(255, 255, 255, 0.86), rgba(255, 255, 255, 0.48), transparent);
  filter: blur(2px);
  pointer-events: none;
}
.login-titlebar {
  position: absolute;
  top: 34px;
  left: min(7vw, 88px);
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 13px;
  min-height: 48px;
  padding: 6px 16px 6px 6px;
  border: 1px solid rgba(15, 23, 42, 0.1);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.76);
  box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(20px);
  color: #111827;
  font-size: 18px;
  font-weight: 820;
  animation: login-panel-in 0.68s cubic-bezier(0.2, 0.8, 0.2, 1) both;
}
.login-mark {
  width: 36px;
  height: 36px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #111827, #2563eb);
  color: #fff;
  font-size: 18px;
  font-weight: 860;
  box-shadow: 0 14px 26px rgba(37, 99, 235, 0.18);
}
.login-kicker {
  margin-top: 0;
  color: #0f766e;
  font-size: 13px;
  font-weight: 800;
}
.login-hero h1 {
  margin: 12px 0 16px;
  max-width: 560px;
  color: #111827;
  font-size: clamp(48px, 5.8vw, 78px);
  font-weight: 430;
  line-height: 1.03;
  letter-spacing: 0;
}
.login-hero p {
  max-width: 520px;
  color: #475569;
  font-size: 15px;
  line-height: 1.8;
}
.signal-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 22px;
}
.signal-row span {
  height: 32px;
  display: inline-flex;
  align-items: center;
  padding: 0 13px;
  border: 1px solid rgba(37, 99, 235, 0.15);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.7);
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.06);
  color: #1d4ed8;
  font-size: 13px;
  font-weight: 680;
}
.login-panel {
  padding: 26px;
  border: 1px solid rgba(15, 23, 42, 0.12);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.78);
  box-shadow: 0 32px 82px rgba(15, 23, 42, 0.16), inset 0 1px 0 rgba(255, 255, 255, 0.92);
  backdrop-filter: blur(24px);
  animation: login-panel-in 0.82s 0.08s cubic-bezier(0.2, 0.8, 0.2, 1) both, login-card-float 5.6s 1s ease-in-out infinite;
}
.panel-glow {
  position: absolute;
  inset: -1px;
  border-radius: inherit;
  background: linear-gradient(135deg, rgba(37, 99, 235, 0.5), transparent 36%, rgba(20, 184, 166, 0.36));
  opacity: 0.78;
  pointer-events: none;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  padding: 1px;
}
.panel-head {
  margin-bottom: 22px;
}
.panel-eyebrow {
  color: #0f766e;
  font-size: 13px;
  font-weight: 800;
}
.panel-head h2 {
  margin-top: 8px;
  color: #111827;
  font-size: 27px;
  font-weight: 560;
  letter-spacing: 0;
}
.panel-head p {
  margin-top: 8px;
  color: #64748b;
  font-size: 13px;
}
.login-form {
  display: grid;
  gap: 10px;
}
.field-label {
  margin-top: 4px;
  color: #334155;
  font-size: 13px;
  font-weight: 760;
}
.login-form :deep(.el-input__wrapper) {
  height: 42px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.11) inset, 0 10px 24px rgba(15, 23, 42, 0.04);
  transition: box-shadow 0.22s ease, transform 0.22s ease;
}
.login-form :deep(.el-input__wrapper.is-focus) {
  transform: translateY(-1px);
  box-shadow: 0 0 0 1px rgba(37, 99, 235, 0.72) inset, 0 0 0 4px rgba(37, 99, 235, 0.12), 0 14px 30px rgba(37, 99, 235, 0.08);
}
.login-form :deep(.el-input__inner) {
  color: #111827;
}
.captcha-row {
  display: grid;
  grid-template-columns: 1fr 128px;
  gap: 10px;
}
.captcha-card {
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 1px solid rgba(37, 99, 235, 0.16);
  border-radius: 10px;
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.96), rgba(240, 253, 250, 0.96));
  color: #1d4ed8;
  font: inherit;
  font-size: 13px;
  font-weight: 760;
  cursor: pointer;
  box-shadow: 0 12px 26px rgba(37, 99, 235, 0.08);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}
.captcha-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 16px 34px rgba(37, 99, 235, 0.14);
}
.captcha-card.refreshing :deep(svg) {
  animation: login-captcha-spin 0.6s linear infinite;
}
.login-submit {
  position: relative;
  width: 100%;
  height: 44px;
  margin-top: 12px;
  border: 0;
  border-radius: 10px;
  font-weight: 780;
  background: linear-gradient(135deg, #2563eb, #0f766e);
  box-shadow: 0 18px 42px rgba(37, 99, 235, 0.26);
  overflow: hidden;
}
.login-submit::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.34), transparent);
  transform: translateX(-120%);
  animation: login-button-shine 2.2s ease infinite;
}
.register-link {
  width: 100%;
  margin-top: 12px;
  border: 0;
  background: transparent;
  color: #2563eb;
  font: inherit;
  font-size: 13px;
  font-weight: 760;
  cursor: pointer;
}
.register-link:hover {
  color: #1d4ed8;
  text-decoration: underline;
}
.register-form {
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: 14px;
}
.enterprise-register-dialog :deep(.el-form-item:nth-child(1)),
.enterprise-register-dialog :deep(.el-form-item:nth-child(8)) {
  grid-column: 1 / -1;
}
.register-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.required-mark,
.optional-mark {
  display: inline-flex;
  align-items: center;
  font-size: 11px;
  line-height: 1;
}
.required-mark {
  height: auto;
  padding: 0;
  color: #b91c1c;
  background: transparent;
  font-size: 15px;
  font-weight: 900;
}
.optional-mark {
  height: 18px;
  padding: 0 6px;
  border-radius: 999px;
  color: #64748b;
  background: #f1f5f9;
}
.account-hints {
  display: grid;
  gap: 6px;
  margin-top: 18px;
  padding-top: 16px;
  border-top: 1px solid rgba(15, 23, 42, 0.1);
  color: #64748b;
  font-size: 13px;
}
@keyframes login-backdrop-in {
  from { opacity: 0; transform: scale(1.035); }
  to { opacity: 0.72; transform: scale(1); }
}
@keyframes login-backdrop-breathe {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.018); }
}
@keyframes login-grid-drift {
  from { background-position: 0 0; }
  to { background-position: 42px 42px; }
}
@keyframes login-beam {
  0%, 32% { transform: translateX(0) skewX(-16deg); opacity: 0; }
  48% { opacity: 1; }
  100% { transform: translateX(650%) skewX(-16deg); opacity: 0; }
}
@keyframes login-orbit {
  from { transform: rotate(0deg) scale(0.96); }
  50% { transform: rotate(180deg) scale(1.04); }
  to { transform: rotate(360deg) scale(0.96); }
}
@keyframes login-panel-in {
  from { opacity: 0; transform: translateY(22px) scale(0.975); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
@keyframes login-card-float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-8px); }
}
@keyframes login-captcha-spin {
  to { transform: rotate(360deg); }
}
@keyframes login-button-shine {
  44% { transform: translateX(-120%); }
  100% { transform: translateX(120%); }
}
@media (max-width: 980px) {
  .login-light-shell {
    grid-template-columns: 1fr;
    align-items: start;
    padding: 28px;
  }
  .login-light-shell::after {
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.42), rgba(255, 255, 255, 0.92));
  }
  .login-panel {
    max-width: 430px;
  }
}
@media (max-width: 560px) {
  .login-light-shell {
    padding: 18px;
  }
  .login-hero h1 {
    font-size: 38px;
  }
  .captcha-row {
    grid-template-columns: 1fr;
  }
}
</style>
