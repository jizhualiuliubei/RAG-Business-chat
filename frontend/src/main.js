// 应用入口：创建 Vue 应用，挂载 Element Plus，启动
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'          // Element Plus 样式
import zhCn from 'element-plus/es/locale/lang/zh-cn'  // 中文语言包（组件文案变中文）
import * as Icons from '@element-plus/icons-vue'      // 图标库

import App from './App.vue'
import router from './router/index.js'
import './assets/styles.css'                  // 自定义全局样式

// 创建 Vue 应用实例
const app = createApp(App)

// 注册 Element Plus（use 是插件的挂载方式），并指定中文
app.use(ElementPlus, { locale: zhCn })
app.use(router)

// 把图标全部注册成全局组件（这样模板里可以直接用 <el-icon><Plus /></el-icon>）
for (const [name, comp] of Object.entries(Icons)) {
  app.component(name, comp)
}

// 挂载到 index.html 的 #app
app.mount('#app')
