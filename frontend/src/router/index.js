import { createRouter, createWebHistory } from 'vue-router'

const ShellView = { template: '<span />' }

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'root', component: ShellView },
    { path: '/login', name: 'login', component: ShellView },
    { path: '/home', name: 'home', component: ShellView },
    { path: '/dashboard', name: 'dashboard', component: ShellView },
    { path: '/admin', name: 'admin', component: ShellView },
    { path: '/:pathMatch(.*)*', redirect: '/home' },
  ],
})

export default router
