// Vite 配置文件
// 作用：配置前端开发服务器，以及"开发时的接口代理"
// 关键点：前端跑在 5173 端口，后端跑在 8001 端口，
//        浏览器直接请求 http://localhost:8001 会跨域，
//        所以用 Vite 代理：把前端 /api 开头的请求转发到后端。
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 端口优先读 PORT 环境变量（preview_start 会注入），默认 5173
    port: process.env.PORT || 5173,
    open: false,         // 不自动打开浏览器（由用户手动开）
    proxy: {
      // 所有 /api 开头的请求，代理到后端 FastAPI 服务
      // 注：8000 端口曾被僵尸进程占用导致无法重启，当前后端跑在 8001
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
