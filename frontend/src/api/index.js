// axios 封装
// 作用：统一创建 axios 实例，配置 baseURL、token 注入和响应拦截器。
import axios from 'axios'
import { ElMessage } from 'element-plus'

export const AUTH_TOKEN_KEY = 'kb_auth_token'

export function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || ''
}

export function setAuthToken(token) {
  localStorage.setItem(AUTH_TOKEN_KEY, token)
}

export function clearAuthToken() {
  localStorage.removeItem(AUTH_TOKEN_KEY)
}

const http = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

http.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const status = error.response?.status
    const detail = error.response?.data?.detail
    if (status === 401) {
      clearAuthToken()
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    // 提示要能区分「后端说的」和「压根没连上」——都写成"请求失败，请检查后端服务"
    // 会让人以为是登录问题。注意 401 时后端一般会带 detail，会走第一条分支。
    let message = typeof detail === 'string' && detail ? detail : ''
    if (!message) {
      if (!error.response) {
        message = '连不上后端服务（可能刚重启或网络抖动），稍后重试即可'
      } else if (status >= 500) {
        message = `后端服务出错（${status}），请稍后重试`
      } else {
        message = '请求失败，请检查后端服务'
      }
    }
    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default http