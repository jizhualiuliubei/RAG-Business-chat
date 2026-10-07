export const PRODUCT_NAME = '企业知识与事务智能体平台'
export const PRODUCT_SHORT_NAME = '知识与事务工作台'
export const PRODUCT_SUBTITLE = '企业知识问答 · 引用溯源'
export const PRODUCT_DESCRIPTION = '通过结构化混合检索与精排，从企业文档中检索相关内容，结合上下文生成回答，并提供引用来源供核对。同时支持采购与权限申请的辅助处理。'

export function shellBrand(user) {
  const tenant = user?.enterprise_name?.trim()
  const platformIdentity = ['system_admin', 'admin'].includes(user?.role)
  return tenant && !platformIdentity
    ? { title: tenant, subtitle: PRODUCT_NAME }
    : { title: PRODUCT_NAME, subtitle: '' }
}
