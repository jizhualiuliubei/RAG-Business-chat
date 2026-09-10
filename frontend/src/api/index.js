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
    const detail = error.response?.data?.detail
    if (error.response?.status === 401) {
      clearAuthToken()
      window.dispatchEvent(new CustomEvent('auth:expired'))
    }
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败，请检查后端服务')
    return Promise.reject(error)
  }
)

export default http