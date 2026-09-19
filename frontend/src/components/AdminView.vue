<template>
  <main class="admin-console">
    <aside class="admin-rail">
      <div class="admin-brand">
        <div class="admin-mark">W</div>
        <div>
          <div class="admin-title">企业知识库后台管理台</div>
          <div class="admin-subtitle">管理员工作区</div>
        </div>
      </div>

      <nav class="admin-nav">
        <button
          v-for="item in navItems"
          :key="item.key"
          type="button"
          :class="{ active: activeTab === item.key }"
          @click="activeTab = item.key"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </button>
      </nav>

      <button class="back-home" type="button" @click="$emit('back-to-home')">
        <el-icon><Back /></el-icon>
        <span>返回工作台</span>
      </button>
    </aside>

    <section class="admin-main">
      <header class="admin-head">
        <div>
          <h1>{{ activeTitle }}</h1>
          <p>企业治理、知识库、模型配置和 RAG 评测集中管理。</p>
        </div>
        <div class="admin-user-chip">
          <span>{{ currentUser?.display_name?.slice(0, 1) || '管' }}</span>
          <div>
            <strong>{{ currentUser?.display_name }}</strong>
            <small>管理员</small>
          </div>
        </div>
      </header>

      <section v-if="activeTab === 'overview'" class="admin-section">
        <div class="metric-grid">
          <article v-for="metric in metrics" :key="metric.label" class="metric-card">
            <span>{{ metric.label }}</span>
            <strong>{{ metric.value }}</strong>
          </article>
        </div>
        <div class="admin-panel">
          <div class="panel-head">
            <h2>最近操作</h2>
            <button class="text-action" type="button" @click="activeTab = 'audit'">查看全部</button>
          </div>
          <div class="audit-list compact">
            <div v-for="log in overview.recent_audits || []" :key="log.id" class="audit-item">
              <span class="audit-action" :title="auditActionLabel(log)">{{ auditActionLabel(log) }}</span>
              <span class="audit-target">{{ log.target_name || '-' }}</span>
              <time>{{ log.created_at }}</time>
            </div>
            <el-empty v-if="!(overview.recent_audits || []).length" description="暂无审计记录" />
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'enterprises'" class="admin-section">
        <div class="admin-panel">
          <div class="panel-head">
            <h2>企业审核与管理</h2>
          </div>
          <div class="enterprise-toolbar" aria-label="企业搜索与状态筛选">
            <div class="enterprise-toolbar-copy">
              <strong>企业审核与治理</strong>
              <span>搜索、筛选、审核和维护平台企业</span>
            </div>
            <div class="enterprise-toolbar-search">
              <span>搜索企业</span>
              <el-input
                v-model="enterpriseSearch"
                size="small"
                clearable
                class="enterprise-search"
                placeholder="名称 / 代码 / 联系人"
                @keyup.enter="loadEnterprises"
                @clear="loadEnterprises"
              >
                <template #prefix>
                  <el-icon><Search /></el-icon>
                </template>
              </el-input>
            </div>
            <div class="enterprise-toolbar-filter">
              <span>筛选状态</span>
              <el-select
                v-model="enterpriseStatusFilter"
                size="small"
                class="status-filter"
                aria-label="状态筛选"
                @change="loadEnterprises"
              >
                <el-option label="默认" value="" />
                <el-option label="待审核" value="pending_review" />
                <el-option label="已启用" value="active" />
                <el-option label="已禁用" value="disabled" />
                <el-option label="已拒绝" value="rejected" />
                <el-option label="已删除" value="deleted" />
              </el-select>
            </div>
            <div class="enterprise-toolbar-actions">
              <el-button size="small" type="primary" @click="loadEnterprises">
                <el-icon><Search /></el-icon>
                <span>查询</span>
              </el-button>
              <el-button size="small" type="primary" plain @click="openEnterpriseDialog()">
                <el-icon><Plus /></el-icon>
                <span>新建企业</span>
              </el-button>
              <el-button size="small" text type="primary" @click="loadEnterprises">
                <el-icon><Refresh /></el-icon>
                <span>刷新</span>
              </el-button>
            </div>
          </div>
          <el-table :data="enterprises" stripe class="enterprise-table">
            <el-table-column label="企业信息" min-width="260">
              <template #default="{ row }">
                <div class="enterprise-info-cell">
                  <strong>{{ row.name }}</strong>
                  <span>{{ row.code }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="联系人" min-width="260">
              <template #default="{ row }">
                <div class="enterprise-contact-cell">
                  <strong>{{ row.contact_name || '未填写联系人' }}</strong>
                  <span>{{ row.contact_email || '未填写邮箱' }}</span>
                  <span>{{ row.contact_phone || '未填写手机号' }}</span>
                </div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="132" align="center">
              <template #default="{ row }">
                <el-tag :type="enterpriseStatusType(row.status)" effect="light">
                  {{ enterpriseStatusText(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="176" align="right" fixed="right">
              <template #default="{ row }">
                <div class="enterprise-row-actions">
                  <el-button
                    v-if="row.status === 'pending_review'"
                    size="small"
                    type="primary"
                    @click="approveEnterpriseRow(row)"
                  >
                    通过
                  </el-button>
                  <el-button v-else size="small" type="primary" plain @click="openEnterpriseDialog(row)">编辑</el-button>
                  <el-dropdown trigger="click">
                    <el-button size="small">
                      更多
                      <el-icon class="el-icon--right"><ArrowDown /></el-icon>
                    </el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item v-if="row.status === 'pending_review'" @click="approveEnterpriseRow(row)">审核通过</el-dropdown-item>
                        <el-dropdown-item v-if="row.status === 'pending_review'" @click="rejectEnterpriseRow(row)">拒绝申请</el-dropdown-item>
                        <el-dropdown-item v-if="row.status === 'active'" @click="setEnterpriseStatus(row, 'disabled')">禁用企业</el-dropdown-item>
                        <el-dropdown-item v-if="row.status === 'disabled'" @click="setEnterpriseStatus(row, 'active')">启用企业</el-dropdown-item>
                        <el-dropdown-item
                          v-if="row.code !== 'system' && row.status !== 'deleted'"
                          divided
                          @click="removeEnterprise(row)"
                        >
                          删除企业
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </section>

      <section v-if="activeTab === 'enterpriseSettings'" class="admin-section two-column">
        <div class="admin-panel">
          <div class="panel-head">
            <h2>{{ isSystemAdmin ? '平台模型配置' : '企业设置' }}</h2>
            <span class="panel-note">{{ enterpriseSettings.enterprise?.code || '-' }}</span>
          </div>
          <div class="settings-list">
            <div><span>企业名称</span><strong>{{ enterpriseSettings.enterprise?.name || '-' }}</strong></div>
            <div><span>状态</span><strong>{{ enterpriseStatusText(enterpriseSettings.enterprise?.status) }}</strong></div>
          </div>
        </div>
        <form class="admin-panel create-user-card" @submit.prevent="saveModelKeys">
          <div class="panel-head">
            <h2>模型 Key</h2>
            <span class="panel-note">仅显示配置状态和后四位</span>
          </div>
          <div class="settings-list">
            <div><span>DeepSeek 对话模型</span><strong>{{ enterpriseSettings.model_keys?.deepseek?.configured ? enterpriseSettings.model_keys.deepseek.masked : '未配置' }}</strong></div>
            <div><span>SiliconFlow 嵌入模型</span><strong>{{ enterpriseSettings.model_keys?.siliconflow?.configured ? enterpriseSettings.model_keys.siliconflow.masked : '未配置' }}</strong></div>
            <div><span>DeepSeek 模型名称</span><strong>{{ modelKeyDefaults.deepseekModel }}</strong></div>
            <div><span>SiliconFlow 模型名称</span><strong>{{ modelKeyDefaults.embedModel }}</strong></div>
            <div v-if="enterpriseSettings.uses_env_fallback"><span>兜底状态</span><strong>当前使用 .env 过渡兜底</strong></div>
          </div>
          <label>
            <span>DeepSeek API Key</span>
            <el-input v-model="modelKeyForm.deepseek_api_key" type="password" show-password placeholder="修改时输入完整 Key" />
          </label>
          <label>
            <span>DeepSeek Base URL</span>
            <el-input v-model="modelKeyForm.deepseek_base_url" placeholder="https://api.deepseek.com/v1" />
          </label>
          <label>
            <span>DeepSeek 模型名称</span>
            <el-input v-model="modelKeyForm.deepseek_model" placeholder="deepseek-v4-flash" />
          </label>
          <label>
            <span>SiliconFlow API Key</span>
            <el-input v-model="modelKeyForm.siliconflow_api_key" type="password" show-password placeholder="修改时输入完整 Key" />
          </label>
          <label>
            <span>SiliconFlow Base URL</span>
            <el-input v-model="modelKeyForm.siliconflow_base_url" placeholder="https://api.siliconflow.cn/v1" />
          </label>
          <label>
            <span>SiliconFlow 模型名称</span>
            <el-input v-model="modelKeyForm.embed_model_name" placeholder="BAAI/bge-m3" />
          </label>
          <div class="model-key-actions">
            <el-button native-type="submit" type="primary" :loading="savingModelKeys">保存模型配置</el-button>
            <el-button native-type="button" type="primary" plain :loading="testingModelKeys" @click="testModelKeys">
              <el-icon><Connection /></el-icon>
              <span>检验连接</span>
            </el-button>
          </div>
          <div v-if="modelKeyTestResult" class="key-test-result">
            <div>
              <el-tag size="small" :type="modelKeyTestResult.deepseek?.ok ? 'success' : 'danger'" effect="light">DeepSeek 对话模型</el-tag>
              <span>{{ modelKeyTestResult.deepseek?.message || '-' }} · {{ modelKeyTestResult.deepseek?.base_url || '-' }} · {{ modelKeyTestResult.deepseek?.model || '-' }}</span>
              <small>{{ modelKeyTestResult.deepseek?.latency_ms ?? 0 }}ms</small>
            </div>
            <div>
              <el-tag size="small" :type="modelKeyTestResult.siliconflow?.ok ? 'success' : 'danger'" effect="light">SiliconFlow 嵌入模型</el-tag>
              <span>{{ modelKeyTestResult.siliconflow?.message || '-' }} · {{ modelKeyTestResult.siliconflow?.base_url || '-' }} · {{ modelKeyTestResult.siliconflow?.model || '-' }}</span>
              <small>{{ modelKeyTestResult.siliconflow?.latency_ms ?? 0 }}ms</small>
            </div>
          </div>
        </form>
      </section>

      <section v-if="activeTab === 'users'" class="admin-section two-column">
        <div class="admin-panel">
          <div class="panel-head">
            <h2>用户管理</h2>
            <span class="panel-note">账号由管理员统一开通</span>
          </div>
          <el-table :data="users" stripe>
            <el-table-column prop="display_name" label="姓名" min-width="130" />
            <el-table-column prop="username" label="用户名" min-width="130" />
            <el-table-column prop="enterprise_code" label="企业代码" width="120" />
            <el-table-column prop="enterprise_name" label="企业名称" min-width="150" />
            <el-table-column label="角色" width="100">
              <template #default="{ row }">
                <el-tag :type="isAdminRole(row.role) ? 'primary' : 'info'" effect="light">
                  {{ roleText(row.role) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-tag :type="row.status === 'active' ? 'success' : 'danger'" effect="light">
                  {{ row.status === 'active' ? '启用' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="last_login_at" label="最后登录" min-width="140" />
            <el-table-column label="操作" width="230" fixed="right">
              <template #default="{ row }">
                <div v-if="!isAdminRole(row.role)" class="user-row-actions">
                  <el-button size="small" text type="primary" @click="openUserDialog(row)">编辑员工</el-button>
                  <el-button size="small" text type="primary" @click="toggleUser(row)">
                    {{ row.status === 'active' ? '禁用' : '启用' }}
                  </el-button>
                  <el-button size="small" text type="danger" @click="removeUser(row)">删除员工</el-button>
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>

        <form class="admin-panel create-user-card" @submit.prevent="submitUser">
          <div class="panel-head">
            <h2>创建员工</h2>
          </div>
          <label>
            <span><b class="field-required">*</b>姓名</span>
            <el-input v-model="newUser.display_name" placeholder="例如：知识运营" />
          </label>
          <label>
            <span><b class="field-required">*</b>用户名</span>
            <el-input v-model="newUser.username" placeholder="例如：operator" />
          </label>
          <label v-if="isSystemAdmin">
            <span><b class="field-required">*</b>员工归属企业</span>
            <el-select v-model="newUser.enterprise_code" class="full-width" placeholder="选择员工所属企业">
              <el-option
                v-for="enterprise in activeEnterpriseOptions"
                :key="enterprise.code"
                :label="`${enterprise.name}（${enterprise.code}）`"
                :value="enterprise.code"
              />
            </el-select>
          </label>
          <label>
            <span>当前企业代码</span>
            <el-input :model-value="selectedEmployeeEnterpriseCode" disabled />
          </label>
          <label>
            <span>当前企业名称</span>
            <el-input :model-value="selectedEmployeeEnterpriseName" disabled />
          </label>
          <label>
            <span><b class="field-required">*</b>密码信息</span>
            <el-input v-model="newUser.password" type="password" show-password placeholder="至少 6 位" />
          </label>
          <el-button native-type="submit" type="primary" :loading="creatingUser">创建员工</el-button>
        </form>
      </section>

      <section v-if="activeTab === 'knowledge'" class="admin-section">
        <div class="admin-panel">
          <div class="panel-head">
            <h2>知识库治理</h2>
            <el-button size="small" type="primary" @click="openCreateKbDialog">
              <el-icon><Plus /></el-icon>
              <span>新建知识库</span>
            </el-button>
          </div>
          <div class="kb-governance-list">
            <article v-for="kb in knowledge.knowledge_bases || []" :key="kb.id" class="kb-governance-card">
              <div class="kb-card-head">
                <button class="kb-fold-btn" type="button" @click="toggleKbExpanded(kb.id)">
                  <el-icon v-if="isKbExpanded(kb.id)"><ArrowDown /></el-icon>
                  <el-icon v-else><ArrowRight /></el-icon>
                  <span>{{ isKbExpanded(kb.id) ? '收起' : '展开' }}</span>
                </button>
                <div class="kb-title-block">
                  <div v-if="editingKbId === kb.id" class="kb-edit-row">
                    <el-input
                      v-model="editingKbName"
                      size="small"
                      placeholder="知识库名称"
                      @keydown.enter="saveKbName(kb)"
                    />
                    <el-button size="small" type="primary" @click="saveKbName(kb)">保存名称</el-button>
                    <el-button size="small" text @click="cancelEditKb">取消</el-button>
                  </div>
                  <template v-else>
                    <h3>{{ kb.name }}</h3>
                    <p>{{ kb.doc_count }} 个文档 · {{ kb.chunk_count }} 个切片 · 最近更新 {{ kb.last_upload_at || '暂无' }}</p>
                  </template>
                </div>
                <div class="kb-card-actions">
                  <el-button text type="primary" @click="startEditKb(kb)">编辑名称</el-button>
                  <el-button text type="danger" @click="removeKb(kb)">删除</el-button>
                </div>
              </div>
              <div class="status-row">
                <span>已完成 {{ kb.status_counts.done }}</span>
                <span>解析中 {{ kb.status_counts.processing }}</span>
                <span>失败 {{ kb.status_counts.failed }}</span>
              </div>
              <div class="kb-progress-block">
                <div class="kb-progress-meta">
                  <span>解析进度</span>
                  <strong>{{ kbProgressPercent(kb) }}%</strong>
                </div>
                <div class="kb-progress-bar" aria-label="知识库解析进度">
                  <i
                    class="kb-progress-segment done"
                    :style="{ width: kbStatusPercent(kb, 'done') + '%' }"
                  ></i>
                  <i
                    class="kb-progress-segment processing"
                    :style="{ width: kbStatusPercent(kb, 'processing') + '%' }"
                  ></i>
                  <i
                    class="kb-progress-segment failed"
                    :style="{ width: kbStatusPercent(kb, 'failed') + '%' }"
                  ></i>
                </div>
              </div>
              <div v-if="isKbExpanded(kb.id)" class="kb-detail-panel">
                <div class="upload-row">
                  <div>
                    <span>库内文档</span>
                    <small v-if="kb.status_counts.processing > 0">后台正在排队解析，列表会自动刷新</small>
                  </div>
                  <div class="upload-actions">
                    <el-button
                      size="small"
                      type="danger"
                      plain
                      :disabled="getSelectedAdminDocIds(kb.id).length === 0"
                      :loading="batchDeletingKbId === kb.id"
                      @click="batchRemoveDocs(kb)"
                    >
                      批量删除
                    </el-button>
                    <el-upload
                      :auto-upload="false"
                      :show-file-list="false"
                      multiple
                      accept=".txt,.pdf,.docx,.csv,.xlsx,.xls"
                      :on-change="(file) => uploadKbDoc(kb, file)"
                    >
                      <el-button size="small" plain :loading="uploadingKbIds.has(kb.id)">上传文档</el-button>
                    </el-upload>
                  </div>
                </div>
                <div class="doc-row doc-row-head" v-if="kb.documents.length">
                  <span>文件名</span>
                  <small>状态</small>
                  <small>解析方式</small>
                  <span>操作</span>
                </div>
                <div class="doc-row" v-for="doc in kb.documents" :key="doc.id">
                  <label class="doc-select">
                    <el-checkbox
                      :model-value="isAdminDocSelected(kb.id, doc.id)"
                      @change="(checked) => toggleAdminDocSelection(kb.id, doc.id, checked)"
                    />
                    <span>{{ doc.filename }}</span>
                  </label>
                  <small class="doc-progress-cell">
                    <span>{{ doc.status }} · {{ doc.chunk_count }} 切片</span>
                    <i class="doc-progress" aria-label="文档解析进度">
                      <b
                        :class="doc.status"
                        :style="{ width: docStatusPercent(doc) + '%' }"
                      ></b>
                    </i>
                    <el-tooltip
                      v-if="doc.status === 'failed' && doc.failure_reason"
                      :content="doc.failure_reason"
                      placement="top"
                    >
                      <button class="failure-reason-btn" type="button">失败原因</button>
                    </el-tooltip>
                  </small>
                  <small class="doc-parser-cell" :title="formatParserDetail(doc)">
                    {{ formatParserName(doc.parser_name) }}
                    <span v-if="doc.parsed_chars"> · {{ doc.parsed_chars }} 字</span>
                  </small>
                  <el-button
                    text
                    type="danger"
                    :loading="deletingDocIds.has(doc.id)"
                    @click="removeDoc(doc)"
                  >
                    删除
                  </el-button>
                </div>
                <el-empty v-if="!kb.documents.length" description="该知识库暂无文档" />
              </div>
            </article>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'evaluation'" class="admin-section">
        <div class="admin-panel evaluation-workbench">
          <div class="panel-head">
            <div>
              <h2>RAG 评测中心</h2>
              <p>评测集管理 · 运行评测 · 任务历史 · 逐题诊断 · 下载报告。</p>
            </div>
            <div class="eval-actions">
              <el-upload
                :auto-upload="false"
                :show-file-list="false"
                accept=".csv,.md,.markdown,.txt"
                :on-change="handleUploadEvaluationDataset"
              >
                <el-button size="small" plain>上传题集</el-button>
              </el-upload>
              <el-button size="small" plain @click="handleDownloadDatasetTemplate">下载题集模板</el-button>
              <el-button size="small" plain @click="datasetFormatVisible = true">题集格式说明</el-button>
              <el-button size="small" plain @click="handleDownloadEvaluationDataset('csv')">下载题集CSV</el-button>
              <el-button size="small" plain @click="handleDownloadEvaluationDataset('md')">下载题集Markdown</el-button>
            </div>
          </div>

          <div class="evaluation-stage-grid">
            <section class="eval-stage-card">
              <div class="subsection-title">
                <span>评测集管理</span>
                <button
                  type="button"
                  class="text-action"
                  :disabled="datasetDetailLoading"
                  @click="loadSelectedDatasetDetail({ notify: true })"
                >
                  {{ datasetDetailLoading ? '加载中' : '查看题目' }}
                </button>
              </div>
              <el-select v-model="selectedDatasetVersion" size="small" class="full-width" @change="loadSelectedDatasetDetail">
                <el-option
                  v-for="dataset in evaluationDatasets"
                  :key="dataset.version"
                  :label="`${dataset.name} · ${dataset.status === 'approved' ? '已审核' : '草稿'}`"
                  :value="dataset.version"
                />
              </el-select>
              <div class="dataset-meta" v-if="selectedDataset">
                <el-tag size="small" :type="selectedDataset.status === 'approved' ? 'success' : 'warning'" effect="light">
                  {{ selectedDataset.status === 'approved' ? '已审核' : '待审核' }}
                </el-tag>
                <el-tag size="small" effect="light">{{ selectedDataset.source_type }}</el-tag>
                <span>{{ selectedDataset.case_count }} 题</span>
              </div>
              <p class="panel-note">
                上传的题集必须是 CSV，或带表头的 Markdown 表格，且表头至少包含
                <code>case_id</code> 和 <code>question</code> 两列。上传后先进入草稿，审核通过才能运行评测。
                不确定怎么写就点上面的「题集格式说明」或「下载题集模板」。
              </p>
              <div class="eval-dataset-actions">
                <el-button
                  size="small"
                  type="primary"
                  plain
                  :disabled="!selectedDataset || selectedDataset.status === 'approved' || selectedDataset.source_type === 'builtin'"
                  @click="handleApproveEvaluationDataset"
                >
                  审核通过
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  :disabled="!selectedDataset || selectedDataset.source_type === 'builtin'"
                  @click="handleDeleteEvaluationDataset"
                >
                  删除题集
                </el-button>
              </div>
            </section>

            <section class="eval-stage-card">
              <div class="subsection-title">
                <span>运行评测</span>
              </div>
              <el-select v-model="selectedEvaluationKbId" size="small" class="eval-kb-select">
                <el-option label="全部知识库" value="all" />
                <el-option
                  v-for="kb in knowledge.knowledge_bases || []"
                  :key="kb.id"
                  :label="kb.name"
                  :value="String(kb.id)"
                />
              </el-select>
              <el-radio-group v-model="evaluationMode" size="small">
                <el-radio-button label="retrieval">检索评测</el-radio-button>
                <el-radio-button label="rules">主评测</el-radio-button>
                <el-radio-button label="ragas">RAGAS诊断</el-radio-button>
                <el-radio-button label="combined">主评测+诊断</el-radio-button>
              </el-radio-group>
              <p class="panel-note">{{ evaluationModeNote }}</p>
              <el-button type="primary" :loading="evaluationLoading" @click="handleCreateEvaluationRun">运行评测</el-button>
            </section>

            <section class="eval-stage-card wide">
              <div class="subsection-title">
                <span>逐题诊断</span>
                <button
                  type="button"
                  class="text-action"
                  :disabled="datasetDetailLoading"
                  @click="loadSelectedDatasetDetail({ notify: true })"
                >
                  {{ datasetDetailLoading ? '刷新中' : '刷新' }}
                </button>
              </div>
              <div class="dataset-case-preview">
                <details
                  v-for="item in selectedDatasetDetail.items || []"
                  :key="item.case_id"
                  class="dataset-case-row"
                >
                  <summary>
                    <strong>{{ item.case_id }} · {{ item.category }} · {{ item.difficulty }}</strong>
                    <span>{{ item.question }}</span>
                  </summary>
                  <dl class="dataset-case-detail">
                    <dt>期望来源</dt>
                    <dd>{{ item.expected_doc || '未覆盖拒答题' }}{{ item.expected_section ? ` / ${item.expected_section}` : '' }}{{ item.expected_clause ? ` / ${item.expected_clause}` : '' }}</dd>
                    <dt>标准答案</dt>
                    <dd>{{ item.standard_answer || '待审核补充' }}</dd>
                    <dt>答案要点</dt>
                    <dd>{{ formatList(item.answer_points) || '待审核补充' }}</dd>
                    <dt>引用要求</dt>
                    <dd>{{ formatList(item.required_citations) || '无' }}</dd>
                    <dt>评测重点</dt>
                    <dd>{{ item.evaluation_focus || '检验回答是否有知识库依据、是否覆盖答案要点、是否正确引用来源。' }}</dd>
                    <dt>审核状态</dt>
                    <dd>{{ item.review_status === 'human_reviewed' ? '已人工审核' : '待人工审核' }}</dd>
                    <dt>证据指纹</dt>
                    <dd>{{ item.evidence_hash || '内置或上传题集未记录证据快照' }}</dd>
                    <dt>证据原文</dt>
                    <dd>{{ item.evidence_text || '无证据快照' }}</dd>
                  </dl>
                </details>
                <el-empty v-if="!(selectedDatasetDetail.items || []).length" description="选择题集后查看题目" />
              </div>
            </section>
          </div>

          <div class="evaluation-layout">
            <div class="evaluation-run-list">
              <div class="subsection-title">
                <span>任务历史</span>
                <div class="run-list-actions">
                  <button type="button" class="text-action" @click="loadEvaluationRuns">刷新</button>
                  <button
                    type="button"
                    class="text-action danger"
                    :disabled="selectedEvaluationRunIds.size === 0"
                    @click="handleBatchDeleteEvaluationRuns"
                  >
                    删除已选{{ selectedEvaluationRunIds.size ? ` ${selectedEvaluationRunIds.size}` : '' }}
                  </button>
                </div>
              </div>
              <article
                v-for="run in evaluationRuns"
                :key="run.id"
                class="evaluation-run-item"
                :class="{ active: selectedEvaluationRun?.id === run.id }"
              >
                <el-checkbox
                  :model-value="selectedEvaluationRunIds.has(run.id)"
                  @change="(checked) => toggleEvaluationRunSelection(run.id, checked)"
                />
                <button type="button" class="evaluation-run-select" @click="selectEvaluationRun(run)">
                  <strong>{{ run.name }}</strong>
                  <small>
                    <b class="run-mode-pill">{{ evaluationModeText(evaluationRunMode(run)) }}</b>
                    {{ evaluationRunStatusText(run.status) }} · {{ formatDuration(run.duration_ms, run.status) }} · {{ run.created_at }}
                  </small>
                  <el-progress
                    :percentage="runProgressPercent(run)"
                    :stroke-width="5"
                    :show-text="false"
                    :status="runProgressStatus(run)"
                  />
                </button>
                <span>{{ run.summary?.pass_rate ?? '-' }}%</span>
                <button
                  v-if="isEvaluationActive(run)"
                  type="button"
                  class="run-cancel-btn"
                  :disabled="run.status !== 'running'"
                  title="中断评测任务"
                  @click.stop="handleCancelEvaluationRun(run)"
                >
                  {{ run.status === 'canceling' ? '中断中' : '中断' }}
                </button>
                <button
                  type="button"
                  class="run-delete-btn"
                  title="删除评测任务"
                  @click.stop="handleDeleteEvaluationRun(run)"
                >
                  删除
                </button>
              </article>
              <el-empty v-if="!evaluationRuns.length" description="暂无评测任务" />
            </div>

            <div class="evaluation-detail">
              <div v-if="selectedEvaluationRun" class="evaluation-progress-panel">
                <div>
                  <span>当前进度 · {{ evaluationModeText(evaluationRunMode(selectedEvaluationRun)) }}</span>
                  <strong>{{ runProgressText(selectedEvaluationRun) }}</strong>
                </div>
                <el-progress
                  :percentage="runProgressPercent(selectedEvaluationRun)"
                  :stroke-width="10"
                  :status="runProgressStatus(selectedEvaluationRun)"
                />
                <small>{{ selectedEvaluationRun.summary?.progress?.current_case ? `正在处理：${selectedEvaluationRun.summary.progress.current_case}` : selectedEvaluationRun.summary?.progress?.status || selectedEvaluationRun.status }}</small>
                <small v-if="selectedEvaluationRun.error_message" class="run-error-message">
                  {{ selectedEvaluationRun.error_message }}
                </small>
                <el-alert
                  v-if="selectedEvaluationRun.status === 'canceled'"
                  type="warning"
                  :closable="false"
                  show-icon
                  title="评测已中断，请重新运行评测"
                />
              </div>
              <div class="eval-grid">
                <div v-for="metric in evaluationMetrics" :key="metric.label" class="eval-result">
                  <span>{{ metric.label }}</span>
                  <strong>{{ metric.value }}</strong>
                  <em>{{ metric.note }}</em>
                </div>
              </div>

              <div v-if="selectedEvaluationRun?.summary?.categories" class="eval-category-grid">
                <article v-for="(item, key) in selectedEvaluationRun.summary.categories" :key="key">
                  <span>{{ key }} · {{ item.label }}</span>
                  <strong>{{ formatPassRate(item) }}</strong>
                  <small>{{ formatCategoryCount(item) }}</small>
                </article>
              </div>

              <div class="case-toolbar">
                <el-select v-model="caseFilters.category" size="small" placeholder="题型" clearable @change="loadEvaluationCases">
                  <el-option label="A 单文档事实检索" value="A" />
                  <el-option label="B 条款解释" value="B" />
                  <el-option label="C 跨文档推理" value="C" />
                  <el-option label="D 场景应用" value="D" />
                  <el-option label="E 边界判断" value="E" />
                  <el-option label="F 未覆盖拒答" value="F" />
                </el-select>
                <el-select v-model="caseFilters.passed" size="small" placeholder="结果" clearable @change="loadEvaluationCases">
                  <el-option label="通过" value="true" />
                  <el-option label="失败" value="false" />
                </el-select>
                <el-button size="small" plain :disabled="!selectedEvaluationRun" @click="handleGenerateReport">生成 Markdown 报告</el-button>
                <el-button size="small" plain :disabled="!selectedEvaluationRun" @click="handleDownloadReport">下载评测报告</el-button>
              </div>

              <el-table :data="evaluationCases" stripe class="evaluation-case-table" @row-click="openCaseDetail">
                <el-table-column prop="case_id" label="题号" width="82" />
                <el-table-column prop="category" label="题型" width="76" />
                <el-table-column prop="question" label="问题" min-width="260" show-overflow-tooltip />
                <el-table-column label="检索 / 证据" width="176">
                  <template #default="{ row }">
                    <div class="hit-row">
                      <small>检索</small>
                      <span :class="['hit-dot', retrievalHitClass(row.retrieval_top1_hit)]">1</span>
                      <span :class="['hit-dot', retrievalHitClass(row.retrieval_top3_hit)]">3</span>
                      <span :class="['hit-dot', retrievalHitClass(row.retrieval_top5_hit)]">5</span>
                    </div>
                    <div class="hit-row">
                      <small>证据</small>
                      <span :class="['hit-dot', row.top1_hit ? 'ok' : '']">1</span>
                      <span :class="['hit-dot', row.top3_hit ? 'ok' : '']">3</span>
                      <span :class="['hit-dot', row.top5_hit ? 'ok' : '']">5</span>
                    </div>
                  </template>
                </el-table-column>
                <el-table-column label="状态" width="92">
                  <template #default="{ row }">
                    <el-tag v-if="row.skipped" type="info" effect="light">未运行</el-tag>
                    <el-tag v-else :type="row.passed ? 'success' : 'danger'" effect="light">
                      {{ row.passed ? '通过' : '失败' }}
                    </el-tag>
                    <el-tag v-if="evaluationRunHasRagasMetrics(selectedEvaluationRun) && row.needs_review" type="warning" effect="light">需复核</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="failure_reason" label="失败原因" min-width="180" show-overflow-tooltip />
              </el-table>
            </div>
          </div>
        </div>
      </section>

      <section v-if="activeTab === 'audit'" class="admin-section">
        <div class="admin-panel">
          <div class="panel-head">
            <h2>操作审计</h2>
          </div>
          <div class="audit-list">
            <div v-for="log in auditLogs" :key="log.id" class="audit-item">
              <span class="audit-action" :title="auditActionLabel(log)">{{ auditActionLabel(log) }}</span>
              <span class="audit-actor" :title="log.actor_name">{{ log.actor_name }}</span>
              <span class="audit-target" :title="log.target_name || '-'">{{ log.target_name || '-' }}</span>
              <time>{{ log.created_at }}</time>
            </div>
            <el-empty v-if="!auditLogs.length" description="暂无审计记录" />
          </div>
        </div>
      </section>
    </section>

    <el-drawer v-model="caseDrawerVisible" title="评测明细" size="46%">
      <div v-if="activeCase" class="case-drawer">
        <h3>{{ activeCase.case_id }} · {{ activeCase.question }}</h3>
        <dl>
          <dt>标准答案要点</dt>
          <dd>{{ (activeCase.answer_points || []).join('；') || '-' }}</dd>
          <dt>期望来源</dt>
          <dd>{{ activeCase.expected_doc || '未覆盖问题' }} · {{ activeCase.expected_section || '-' }} · {{ activeCase.expected_clause || '-' }}</dd>
          <dt>引用要求</dt>
          <dd>{{ (activeCase.required_citations || []).join('；') || '无额外要求' }}</dd>
          <dt>证据诊断</dt>
          <dd>
            必需证据 {{ formatMetricRate(activeCase.evidence_diagnostics?.required_citation_hit_rate) }}；
            主证据 {{ formatEvidenceHit(activeCase.evidence_diagnostics?.primary_evidence_hit) }}；
            辅助证据 {{ formatEvidenceHit(activeCase.evidence_diagnostics?.secondary_evidence_hit) }}
            <template v-if="activeCase.cross_document_diagnostics?.enabled">
              ；跨文档综合 {{ formatCrossDocumentDiagnostic(activeCase.cross_document_diagnostics) }}
            </template>
            <small v-if="(activeCase.evidence_diagnostics?.missing_required_citations || []).length">
              缺失：{{ activeCase.evidence_diagnostics.missing_required_citations.join('；') }}
            </small>
          </dd>
          <dt>实际 Top-K 来源</dt>
          <dd>
            <p v-for="(ctx, index) in activeCase.retrieved_contexts || []" :key="index">
              {{ index + 1 }}. {{ ctx.source }} · {{ Number(ctx.score || 0).toFixed(3) }}
              <small>{{ ctx.text }}</small>
            </p>
          </dd>
          <template v-if="evaluationRunHasGenerationMetrics(selectedEvaluationRun)">
            <dt>答案覆盖率</dt>
            <dd>{{ activeCase.answer_coverage ?? 0 }}%</dd>
          </template>
          <template v-if="evaluationRunHasRagasMetrics(selectedEvaluationRun)">
            <dt>RAGAS 诊断</dt>
            <dd>
              <span v-if="activeCase.ragas_skip_reason">{{ activeCase.ragas_skip_reason }}</span>
              <span v-else-if="Object.keys(activeCase.ragas_scores || {}).length">
                {{ activeCase.ragas_passed ? '诊断通过' : '诊断预警' }}；
                {{ formatRagasScores(activeCase.ragas_scores) }}
                <small v-if="activeCase.ragas_warning_reason">{{ activeCase.ragas_warning_reason }}</small>
              </span>
              <span v-else>未运行</span>
            </dd>
            <dt>复核状态</dt>
            <dd>{{ activeCase.needs_review ? '业务规则已通过，但 RAGAS 提示需人工复核' : '无需复核' }}</dd>
          </template>
          <dt>模型回答</dt>
          <dd>{{ activeCase.answer || '检索模式未生成最终回答' }}</dd>
          <dt>失败原因</dt>
          <dd>{{ activeCase.failure_reason || '无' }}</dd>
          <dt>修复建议</dt>
          <dd>{{ activeCase.fix_suggestion || '暂无' }}</dd>
        </dl>
      </div>
    </el-drawer>

    <el-dialog v-model="enterpriseDialogVisible" :title="editingEnterpriseId ? '编辑企业' : '新建企业'" width="560px">
      <el-form label-width="96px" class="enterprise-form">
        <el-form-item label="企业名称" required>
          <el-input v-model="enterpriseForm.name" placeholder="例如：华东智造有限公司" />
          <template #error>企业名称为必填项</template>
        </el-form-item>
        <el-form-item label="企业代码" required>
          <el-input v-model="enterpriseForm.code" :disabled="enterpriseForm.code === 'system'" placeholder="例如：hd-zhizao" />
          <template #error>企业代码为必填项</template>
        </el-form-item>
        <el-form-item label="联系人">
          <el-input v-model="enterpriseForm.contact_name" placeholder="联系人姓名" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="enterpriseForm.contact_email" placeholder="admin@example.com" />
        </el-form-item>
        <el-form-item label="电话">
          <el-input v-model="enterpriseForm.contact_phone" placeholder="联系电话" />
        </el-form-item>
        <el-form-item label="Logo">
          <el-input v-model="enterpriseForm.logo_url" placeholder="Logo URL，可留空" />
        </el-form-item>
        <template v-if="!editingEnterpriseId">
          <el-divider content-position="left">企业管理员账号</el-divider>
          <el-form-item label="管理员账号" required>
            <el-input v-model="enterpriseForm.admin_username" placeholder="例如：admin" />
          </el-form-item>
          <el-form-item label="管理员姓名" required>
            <el-input v-model="enterpriseForm.admin_display_name" placeholder="例如：企业管理员" />
          </el-form-item>
          <el-form-item label="初始密码" required>
            <el-input v-model="enterpriseForm.admin_password" type="password" show-password placeholder="至少 6 位" />
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="enterpriseDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingEnterprise" @click="saveEnterprise">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="kbDialogVisible" title="新建知识库" width="420px">
      <el-form label-width="96px" class="enterprise-form" @submit.prevent>
        <el-form-item label="知识库名称" required>
          <el-input
            v-model="newKbName"
            autofocus
            placeholder="例如：企业制度知识库"
            maxlength="40"
            show-word-limit
            @keydown.enter="createKb"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="kbDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="createKb">创建知识库</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="userDialogVisible" title="编辑员工" width="480px">
      <el-form label-width="96px" class="enterprise-form">
        <el-form-item label="姓名" required>
          <el-input v-model="editUser.display_name" placeholder="员工姓名" />
        </el-form-item>
        <el-form-item label="用户名" required>
          <el-input v-model="editUser.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="企业代码">
          <el-input v-model="editUser.enterprise_code" disabled />
        </el-form-item>
        <el-form-item label="企业名称">
          <el-input v-model="editUser.enterprise_name" disabled />
        </el-form-item>
        <el-form-item label="密码信息">
          <el-input v-model="editUser.password" type="password" show-password placeholder="留空则不修改，填写至少 6 位" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="savingUser" @click="saveUser">保存员工</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="datasetFormatVisible" title="题集上传格式说明" width="760px">
      <p class="panel-note">
        题集文件只支持 <b>CSV</b> 或 <b>带表头的 Markdown 表格</b>。解析不出结构化题目时会直接拒绝上传，
        不会生成残缺题集。表头行必须包含下面的列名，其中
        <code>case_id</code> 与 <code>question</code> 是必需列，其余可以留空。
        同一格里要写多个值（多条引用、多个要点）时用中文分号「；」分隔。
      </p>
      <el-table :data="datasetFormatColumns" size="small" border>
        <el-table-column prop="name" label="列名" width="180" />
        <el-table-column prop="required" label="必需" width="70" />
        <el-table-column prop="desc" label="说明" />
      </el-table>
      <template #footer>
        <el-button @click="datasetFormatVisible = false">关闭</el-button>
        <el-button type="primary" @click="handleDownloadDatasetTemplate">下载模板</el-button>
      </template>
    </el-dialog>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  createAdminUser,
  createKnowledgeBase,
  deleteDocument,
  deleteDocuments,
  deleteKnowledgeBase,
  deleteEvaluationRun,
  deleteEvaluationRuns,
  cancelEvaluationRun,
  createEvaluationRun,
  approveEvaluationDataset,
  deleteEvaluationDataset,
  generateEvaluationReport,
  getAdminAuditLogs,
  getAdminKnowledgeOverview,
  getAdminOverview,
  getAdminUsers,
  getEvaluationDataset,
  getEvaluationRun,
  downloadDatasetTemplate,
  downloadEvaluationDataset,
  downloadEvaluationReport,
  listEvaluationDatasets,
  listEvaluationRunCases,
  listEvaluationRuns,
  uploadEvaluationDataset,
  updateKnowledgeBase,
  updateAdminUserStatus,
  uploadDocument,
  listSystemEnterprises,
  createSystemEnterprise,
  updateSystemEnterprise,
  deleteSystemEnterprise,
  getSystemEnterprise,
  approveEnterprise,
  rejectEnterprise,
  updateEnterpriseStatus,
  updateAdminUser,
  deleteAdminUser,
  getEnterpriseSettings,
  saveEnterpriseModelKeys,
  testEnterpriseModelKeys,
} from '../api/api.js'

const props = defineProps({
  currentUser: { type: Object, default: null },
})
const currentUser = computed(() => props.currentUser)
const emit = defineEmits(['back-to-home', 'refresh-kbs'])

const isSystemAdmin = computed(() => ['admin', 'system_admin'].includes(currentUser.value?.role))
const isEnterpriseAdmin = computed(() => currentUser.value?.role === 'enterprise_admin')
const navItems = computed(() => [
  { key: 'overview', label: '总览', icon: 'DataBoard' },
  ...(isSystemAdmin.value ? [{ key: 'enterprises', label: '企业管理', icon: 'OfficeBuilding' }] : []),
  ...(isSystemAdmin.value ? [{ key: 'enterpriseSettings', label: '平台模型配置', icon: 'Setting' }] : []),
  ...(isEnterpriseAdmin.value ? [{ key: 'enterpriseSettings', label: '企业设置', icon: 'Setting' }] : []),
  { key: 'users', label: '用户管理', icon: 'User' },
  { key: 'knowledge', label: '知识库治理', icon: 'Files' },
  { key: 'evaluation', label: 'RAG 评测', icon: 'TrendCharts' },
  { key: 'audit', label: '操作审计', icon: 'Tickets' },
])
const activeTab = ref('overview')
const activeTitle = computed(() => navItems.value.find((item) => item.key === activeTab.value)?.label || '总览')

const overview = ref({})
const users = ref([])
const knowledge = ref({ knowledge_bases: [] })
const auditLogs = ref([])
const enterprises = ref([])
const enterpriseSettings = ref({})
const savingModelKeys = ref(false)
const testingModelKeys = ref(false)
const modelKeyTestResult = ref(null)
const modelKeyForm = reactive({
  deepseek_api_key: '',
  deepseek_base_url: '',
  deepseek_model: '',
  siliconflow_api_key: '',
  siliconflow_base_url: '',
  embed_model_name: '',
})
const enterpriseStatusFilter = ref('')
const enterpriseSearch = ref('')
const enterpriseDialogVisible = ref(false)
const savingEnterprise = ref(false)
const editingEnterpriseId = ref(null)
const enterpriseForm = reactive({
  name: '',
  code: '',
  contact_name: '',
  contact_email: '',
  contact_phone: '',
  logo_url: '',
  admin_username: 'admin',
  admin_display_name: '',
  admin_password: '',
})
const evaluationLoading = ref(false)
const datasetFormatVisible = ref(false)
// 与后端 UPLOAD_REQUIRED_COLUMNS / UPLOAD_OPTIONAL_COLUMNS 保持一致。
const datasetFormatColumns = [
  { name: 'case_id', required: '必需', desc: '题目编号，题集内唯一，例如 EA1、A1。' },
  { name: 'question', required: '必需', desc: '题目原文。' },
  { name: 'category', required: '', desc: '题型字母：A 直接事实检索 / B 跨文档综合 / C 规则应用与计算 / D 易错点与矛盾检测 / E 未覆盖问题拒答。只填首字母也行。' },
  { name: 'difficulty', required: '', desc: '难度，如 基础 / 中等 / 困难。留空默认中等。' },
  { name: 'expected_doc', required: '', desc: '期望命中的文档名，不含扩展名，例如 差旅管理制度。' },
  { name: 'expected_section', required: '', desc: '期望命中的章节名，例如 住宿标准。' },
  { name: 'expected_clause', required: '', desc: '期望命中的条款号，例如 TRV-03-002。文档里没有条款编号体系就留空。' },
  { name: 'expected_keywords', required: '', desc: '关键词，多个用「；」分隔。只用于界面展示，不参与判分。' },
  { name: 'standard_answer', required: '', desc: '标准答案，供审核人参考。' },
  { name: 'answer_points', required: '', desc: '答案要点，多个用「；」分隔。答案要点覆盖率按它计算，建议写成题目真正要考的那几条事实。' },
  { name: 'required_citations', required: '', desc: '必需引用，格式「文档名#章节名」，多个用「；」分隔。留空则自动按 expected_doc + expected_section 生成。' },
  { name: 'evaluation_focus', required: '', desc: '这道题想考察什么，用于报告展示。' },
  { name: 'negative_case', required: '', desc: '是否负例题（知识库未覆盖、应拒答）。填 true / false。' },
]
const datasetDetailLoading = ref(false)
const evaluationDatasets = ref([])
const evaluationRuns = ref([])
const selectedEvaluationRunIds = ref(new Set())
const selectedDatasetVersion = ref('enterprise_scale_360_v1')
const selectedDatasetDetail = ref({ items: [] })
const evaluationMode = ref('retrieval')
const selectedEvaluationRun = ref(null)
const evaluationCases = ref([])
const caseFilters = reactive({ category: '', passed: '' })
const caseDrawerVisible = ref(false)
const activeCase = ref(null)
const creatingUser = ref(false)
const savingUser = ref(false)
const userDialogVisible = ref(false)
const kbDialogVisible = ref(false)
const newKbName = ref('')
const expandedKbIds = ref(new Set())
const editingKbId = ref(null)
const editingKbName = ref('')
const selectedEvaluationKbId = ref('all')
const selectedAdminDocIds = ref({})
const uploadingKbIds = ref(new Set())
const deletingDocIds = ref(new Set())
const batchDeletingKbId = ref(null)
const newUser = reactive({ display_name: '', username: '', password: '', enterprise_code: '' })
const editUser = reactive({
  id: null,
  display_name: '',
  username: '',
  password: '',
  enterprise_code: '',
  enterprise_name: '',
})
let knowledgePollTimer = null
let evaluationPollTimer = null

function isAdminRole(role) {
  return ['admin', 'system_admin', 'enterprise_admin'].includes(role)
}

function roleText(role) {
  if (role === 'system_admin' || role === 'admin') return '系统管理员'
  if (role === 'enterprise_admin') return '企业管理员'
  return '企业员工'
}

const auditActionLabels = {
  login: '登录系统',
  create_user: '创建员工',
  update_user: '编辑员工',
  update_user_status: '更新账号状态',
  delete_user: '删除员工',
  create_knowledge_base: '新建知识库',
  update_knowledge_base: '更新知识库',
  delete_knowledge_base: '删除知识库',
  upload_document: '上传文档',
  delete_document: '删除文档',
  run_evaluation: '运行评测',
  cancel_evaluation_run: '中断评测任务',
  delete_evaluation_run: '删除评测任务',
  create_evaluation_dataset: '创建评测集',
  approve_evaluation_dataset: '审核评测集',
  delete_evaluation_dataset: '删除评测集',
  create_enterprise: '创建企业',
  update_enterprise: '编辑企业',
  approve_enterprise: '审核通过企业',
  reject_enterprise: '拒绝企业申请',
  update_enterprise_status: '更新企业状态',
  delete_enterprise: '删除企业',
  impersonate_enterprise: '代管企业',
  update_model_keys: '更新模型 Key',
}

const evaluationStatusLabels = {
  running: '运行中',
  canceling: '正在中断',
  canceled: '已中断',
  success: '已完成',
  failed: '失败',
  pending: '等待中',
}

const evaluationModeLabels = {
  retrieval: '检索评测',
  rules: '主评测',
  ragas: 'RAGAS诊断',
  combined: '主评测 + RAGAS诊断',
  e2e: '主评测',
  full: '主评测',
}

function auditActionLabel(log) {
  return log?.action_label || auditActionLabels[log?.action] || log?.action || '未知操作'
}

function evaluationRunStatusText(status) {
  return evaluationStatusLabels[status] || status || '未运行'
}

function evaluationModeText(mode) {
  return evaluationModeLabels[mode] || mode || '未知模式'
}

const metrics = computed(() => [
  { label: '用户', value: overview.value.user_count ?? '-' },
  { label: '知识库', value: overview.value.kb_count ?? '-' },
  { label: '文档', value: overview.value.doc_count ?? '-' },
  { label: '向量切片', value: overview.value.chunk_count ?? '-' },
  { label: '会话', value: overview.value.conversation_count ?? '-' },
  { label: '审计', value: overview.value.audit_count ?? '-' },
])

const evaluationKbPayload = computed(() => {
  if (selectedEvaluationKbId.value === 'all') {
    return (knowledge.value.knowledge_bases || []).map((kb) => kb.id)
  }
  return Number(selectedEvaluationKbId.value)
})

const selectedDataset = computed(() =>
  evaluationDatasets.value.find((dataset) => dataset.version === selectedDatasetVersion.value),
)
const currentEnterpriseCode = computed(() => enterpriseSettings.value.enterprise?.code || currentUser.value?.enterprise_code || '-')
const currentEnterpriseName = computed(() => enterpriseSettings.value.enterprise?.name || currentUser.value?.enterprise_name || '-')
const activeEnterpriseOptions = computed(() =>
  (enterprises.value || []).filter((enterprise) => enterprise.status === 'active'),
)
const selectedEmployeeEnterprise = computed(() =>
  activeEnterpriseOptions.value.find((enterprise) => enterprise.code === newUser.enterprise_code),
)
const selectedEmployeeEnterpriseCode = computed(() =>
  isSystemAdmin.value ? selectedEmployeeEnterprise.value?.code || '-' : currentEnterpriseCode.value,
)
const selectedEmployeeEnterpriseName = computed(() =>
  isSystemAdmin.value ? selectedEmployeeEnterprise.value?.name || '-' : currentEnterpriseName.value,
)
const modelKeyDefaults = computed(() => ({
  deepseekBaseUrl: enterpriseSettings.value.model_keys?.deepseek?.base_url || 'https://api.deepseek.com/v1',
  deepseekModel: enterpriseSettings.value.model_keys?.deepseek?.model || 'deepseek-v4-flash',
  siliconflowBaseUrl: enterpriseSettings.value.model_keys?.siliconflow?.base_url || 'https://api.siliconflow.cn/v1',
  embedModel: enterpriseSettings.value.model_keys?.siliconflow?.model || 'BAAI/bge-m3',
}))

onMounted(loadAll)
onBeforeUnmount(() => {
  stopKnowledgePolling()
  stopEvaluationPolling()
})

watch(activeTab, async (tab) => {
  if (tab === 'enterprises') await loadEnterprises()
  if (tab === 'enterpriseSettings') await loadEnterpriseSettings()
  if (tab === 'users') {
    await loadUsers()
    if (isSystemAdmin.value) await loadEnterprises()
  }
  if (tab === 'knowledge') await loadKnowledge()
  if (tab === 'evaluation') await loadEvaluationCenter()
  if (tab === 'audit') await loadAudit()
  if (tab !== 'knowledge') stopKnowledgePolling()
  if (tab !== 'evaluation') stopEvaluationPolling()
})

async function loadAll() {
  const loaders = [loadOverview(), loadUsers(), loadKnowledge(), loadAudit(), loadEvaluationDatasets(), loadEvaluationRuns()]
  if (isSystemAdmin.value) loaders.push(loadEnterprises())
  if (isEnterpriseAdmin.value || isSystemAdmin.value) loaders.push(loadEnterpriseSettings())
  await Promise.all(loaders)
}

async function loadOverview() {
  overview.value = await getAdminOverview()
}

async function loadUsers() {
  users.value = await getAdminUsers()
}

async function loadKnowledge() {
  const nextKnowledge = await getAdminKnowledgeOverview()
  knowledge.value = {
    ...nextKnowledge,
    knowledge_bases: (nextKnowledge.knowledge_bases || []).filter((kb) => String(kb?.name || '').trim()),
  }
  const validDocIds = new Set(
    (knowledge.value.knowledge_bases || []).flatMap((kb) => kb.documents.map((doc) => doc.id)),
  )
  const nextSelected = {}
  for (const [kbId, ids] of Object.entries(selectedAdminDocIds.value)) {
    const kept = ids.filter((id) => validDocIds.has(id))
    if (kept.length) nextSelected[kbId] = kept
  }
  selectedAdminDocIds.value = nextSelected
  if (activeTab.value === 'knowledge') syncKnowledgePolling()
}

async function loadAudit() {
  const data = await getAdminAuditLogs()
  auditLogs.value = data.items || []
}

async function loadEnterprises() {
  if (!isSystemAdmin.value) return
  const data = await listSystemEnterprises({
    status: enterpriseStatusFilter.value || undefined,
    q: enterpriseSearch.value.trim() || undefined,
  })
  enterprises.value = data.items || []
  if (!newUser.enterprise_code && activeEnterpriseOptions.value.length) {
    newUser.enterprise_code = activeEnterpriseOptions.value[0].code
  }
}

async function loadEnterpriseSettings() {
  if (!isEnterpriseAdmin.value && !isSystemAdmin.value) return
  enterpriseSettings.value = await getEnterpriseSettings()
  modelKeyForm.deepseek_base_url = modelKeyDefaults.value.deepseekBaseUrl
  modelKeyForm.deepseek_model = modelKeyDefaults.value.deepseekModel
  modelKeyForm.siliconflow_base_url = modelKeyDefaults.value.siliconflowBaseUrl
  modelKeyForm.embed_model_name = modelKeyDefaults.value.embedModel
}

function enterpriseStatusText(status) {
  return {
    pending_review: '待审核',
    active: '已启用',
    rejected: '已拒绝',
    disabled: '已禁用',
    deleted: '已删除',
  }[status] || '-'
}

function enterpriseStatusType(status) {
  return {
    pending_review: 'warning',
    active: 'success',
    rejected: 'danger',
    disabled: 'info',
    deleted: 'danger',
  }[status] || 'info'
}

async function openEnterpriseDialog(row = null) {
  const data = row ? await getSystemEnterprise(row.id) : {}
  editingEnterpriseId.value = data.id || null
  Object.assign(enterpriseForm, {
    name: data.name || '',
    code: data.code || '',
    contact_name: data.contact_name || '',
    contact_email: data.contact_email || '',
    contact_phone: data.contact_phone || '',
    logo_url: data.logo_url || '',
    admin_username: data.id ? '' : 'admin',
    admin_display_name: data.id ? '' : `${data.name || ''}管理员`,
    admin_password: data.id ? '' : generateAdminPassword(),
  })
  enterpriseDialogVisible.value = true
}

async function saveEnterprise() {
  const validationMessage = validateEnterpriseForm()
  if (validationMessage) {
    ElMessage.warning(validationMessage)
    return
  }
  savingEnterprise.value = true
  try {
    const payload = {
      name: enterpriseForm.name.trim(),
      code: enterpriseForm.code.trim().toLowerCase(),
      contact_name: enterpriseForm.contact_name.trim(),
      contact_email: enterpriseForm.contact_email.trim(),
      contact_phone: enterpriseForm.contact_phone.trim(),
      logo_url: enterpriseForm.logo_url.trim(),
    }
    if (editingEnterpriseId.value) {
      await updateSystemEnterprise(editingEnterpriseId.value, payload)
      ElMessage.success('企业信息已更新')
    } else {
      payload.admin_username = enterpriseForm.admin_username.trim()
      payload.admin_display_name = enterpriseForm.admin_display_name.trim()
      payload.admin_password = enterpriseForm.admin_password
      const created = await createSystemEnterprise(payload)
      ElMessage.success('企业已创建')
      if (created.admin_user?.username && created.initial_password) {
        await ElMessageBox.alert(
          `企业管理员账号：${created.admin_user.username}\n初始密码：${created.initial_password}\n请交付给企业管理员并提醒首次登录后修改。`,
          '企业管理员初始账号',
          { confirmButtonText: '我已记录' },
        )
      }
    }
    enterpriseDialogVisible.value = false
    await Promise.all([loadEnterprises(), loadAudit()])
  } finally {
    savingEnterprise.value = false
  }
}

function validateEnterpriseForm() {
  const code = enterpriseForm.code.trim().toLowerCase()
  const email = enterpriseForm.contact_email.trim()
  const phone = enterpriseForm.contact_phone.trim()
  const logoUrl = enterpriseForm.logo_url.trim()
  if (!enterpriseForm.name.trim()) return '企业名称为必填项'
  if (!code) return '企业代码为必填项'
  if (!/^[a-z0-9_-]{2,60}$/.test(code)) return '企业代码只能包含小写字母、数字、横线或下划线，长度 2-60'
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return '联系人邮箱格式不正确'
  if (phone && !/^1[3-9]\d{9}$/.test(phone)) return '联系人手机号格式不正确'
  if (logoUrl && !/^https?:\/\//.test(logoUrl)) return 'Logo URL 必须以 http:// 或 https:// 开头'
  if (!editingEnterpriseId.value) {
    if (enterpriseForm.admin_username.trim().length < 3) return '管理员账号至少需要 3 个字符'
    if (!enterpriseForm.admin_display_name.trim()) return '管理员姓名为必填项'
    if (enterpriseForm.admin_password.length < 6) return '管理员初始密码至少需要 6 位'
  }
  return ''
}

function generateAdminPassword() {
  return `Kb@${Math.random().toString(36).slice(2, 8)}${Math.floor(1000 + Math.random() * 9000)}`
}

async function removeEnterprise(row) {
  await ElMessageBox.confirm(`确认删除企业“${row.name}”？这是软删除，会保留历史数据和审计记录。`, '删除企业', {
    confirmButtonText: '删除企业',
    cancelButtonText: '取消',
    type: 'warning',
  })
  await deleteSystemEnterprise(row.id)
  ElMessage.success('企业已软删除')
  await Promise.all([loadEnterprises(), loadAudit()])
}

async function approveEnterpriseRow(row) {
  await approveEnterprise(row.id)
  ElMessage.success('企业已审核通过')
  await loadEnterprises()
}

async function rejectEnterpriseRow(row) {
  const reason = await ElMessageBox.prompt('请输入拒绝原因', '拒绝企业申请', {
    confirmButtonText: '确认拒绝',
    cancelButtonText: '取消',
    inputValue: '资料不完整',
  }).then(({ value }) => value).catch(() => null)
  if (reason === null) return
  await rejectEnterprise(row.id, reason)
  ElMessage.success('企业申请已拒绝')
  await loadEnterprises()
}

async function setEnterpriseStatus(row, status) {
  await updateEnterpriseStatus(row.id, status)
  ElMessage.success(status === 'active' ? '企业已启用' : '企业已禁用')
  await loadEnterprises()
}

async function saveModelKeys() {
  if (!modelKeyForm.deepseek_api_key && !modelKeyForm.siliconflow_api_key && !modelKeyForm.deepseek_base_url && !modelKeyForm.deepseek_model && !modelKeyForm.siliconflow_base_url && !modelKeyForm.embed_model_name) {
    ElMessage.warning('请输入需要更新的模型配置')
    return
  }
  savingModelKeys.value = true
  try {
    enterpriseSettings.value.model_keys = await saveEnterpriseModelKeys({
      deepseek_api_key: modelKeyForm.deepseek_api_key || undefined,
      deepseek_base_url: normalizeProviderBaseUrl('deepseek', modelKeyForm.deepseek_base_url) || undefined,
      deepseek_model: modelKeyForm.deepseek_model || undefined,
      siliconflow_api_key: modelKeyForm.siliconflow_api_key || undefined,
      siliconflow_base_url: normalizeProviderBaseUrl('siliconflow', modelKeyForm.siliconflow_base_url) || undefined,
      embed_model_name: modelKeyForm.embed_model_name || undefined,
    })
    modelKeyForm.deepseek_api_key = ''
    modelKeyForm.siliconflow_api_key = ''
    ElMessage.success('模型配置已保存')
    await loadEnterpriseSettings()
  } finally {
    savingModelKeys.value = false
  }
}

async function testModelKeys() {
  testingModelKeys.value = true
  modelKeyTestResult.value = null
  try {
    modelKeyTestResult.value = await testEnterpriseModelKeys({
      deepseek_api_key: modelKeyForm.deepseek_api_key || undefined,
      deepseek_base_url: normalizeProviderBaseUrl('deepseek', modelKeyForm.deepseek_base_url) || undefined,
      deepseek_model: modelKeyForm.deepseek_model || undefined,
      siliconflow_api_key: modelKeyForm.siliconflow_api_key || undefined,
      siliconflow_base_url: normalizeProviderBaseUrl('siliconflow', modelKeyForm.siliconflow_base_url) || undefined,
      embed_model_name: modelKeyForm.embed_model_name || undefined,
    })
    const ok = modelKeyTestResult.value.deepseek?.ok && modelKeyTestResult.value.siliconflow?.ok
    ElMessage[ok ? 'success' : 'warning'](ok ? '模型连接检验通过' : '部分模型连接检验未通过')
  } catch (error) {
    const message = error?.response?.data?.detail || error?.message || '检验连接失败'
    modelKeyTestResult.value = {
      deepseek: { ok: false, message, latency_ms: 0, base_url: modelKeyForm.deepseek_base_url || modelKeyDefaults.value.deepseekBaseUrl, model: modelKeyForm.deepseek_model || modelKeyDefaults.value.deepseekModel },
      siliconflow: { ok: false, message, latency_ms: 0, base_url: modelKeyForm.siliconflow_base_url || modelKeyDefaults.value.siliconflowBaseUrl, model: modelKeyForm.embed_model_name || modelKeyDefaults.value.embedModel },
    }
    ElMessage.warning('模型连接检验未通过')
  } finally {
    testingModelKeys.value = false
  }
}

function normalizeProviderBaseUrl(provider, baseUrl) {
  const value = (baseUrl || '').trim().replace(/\/+$/, '')
  if (!value) return ''
  const knownHosts = {
    deepseek: 'https://api.deepseek.com',
    siliconflow: 'https://api.siliconflow.cn',
  }
  return value === knownHosts[provider] ? `${value}/v1` : value
}

async function loadEvaluationCenter() {
  await Promise.all([loadEvaluationDatasets(), loadEvaluationRuns()])
  syncEvaluationPolling()
}

async function loadEvaluationDatasets() {
  const data = await listEvaluationDatasets()
  evaluationDatasets.value = data.items || []
  if (!evaluationDatasets.value.length) {
    selectedDatasetVersion.value = ''
    selectedDatasetDetail.value = { items: [] }
    return
  }
  if (!selectedDatasetVersion.value || !evaluationDatasets.value.some((item) => item.version === selectedDatasetVersion.value)) {
    selectedDatasetVersion.value = evaluationDatasets.value[0].version
  }
  if (selectedDatasetVersion.value && !(selectedDatasetDetail.value.items || []).length) {
    await loadSelectedDatasetDetail()
  }
}

async function loadSelectedDatasetDetail(options = {}) {
  const notify = options?.notify === true
  if (!selectedDatasetVersion.value) {
    selectedDatasetDetail.value = { items: [] }
    return
  }
  datasetDetailLoading.value = true
  try {
    selectedDatasetDetail.value = await getEvaluationDataset(selectedDatasetVersion.value)
    if (notify) {
      ElMessage.success(`已加载 ${(selectedDatasetDetail.value.items || []).length} 道题目`)
    }
  } catch (error) {
    selectedDatasetDetail.value = { items: [] }
    if (notify) {
      ElMessage.warning('题目加载失败，请检查评测集是否存在或是否有权限')
    }
  } finally {
    datasetDetailLoading.value = false
  }
}

async function loadEvaluationRuns() {
  const data = await listEvaluationRuns()
  evaluationRuns.value = (data.items || []).map(normalizeEvaluationRun)
  const validRunIds = new Set(evaluationRuns.value.map((run) => run.id))
  selectedEvaluationRunIds.value = new Set(
    [...selectedEvaluationRunIds.value].filter((id) => validRunIds.has(id)),
  )
  if (!selectedEvaluationRun.value && evaluationRuns.value.length) {
    await selectEvaluationRun(evaluationRuns.value[0])
  } else if (selectedEvaluationRun.value) {
    const refreshed = evaluationRuns.value.find((run) => run.id === selectedEvaluationRun.value.id)
    if (refreshed) selectedEvaluationRun.value = normalizeEvaluationRun(refreshed, selectedEvaluationRun.value)
    else {
      selectedEvaluationRun.value = null
      evaluationCases.value = []
    }
  }
}

async function submitUser() {
  const validationMessage = validateUserForm(newUser)
  if (validationMessage) {
    ElMessage.warning(validationMessage)
    return
  }
  if (isSystemAdmin.value && !newUser.enterprise_code) {
    ElMessage.warning('请选择员工归属企业')
    return
  }
  creatingUser.value = true
  try {
    await createAdminUser({
      display_name: newUser.display_name,
      username: newUser.username,
      password: newUser.password,
      enterprise_code: isSystemAdmin.value ? newUser.enterprise_code : undefined,
    })
    ElMessage.success('员工账号已创建')
    newUser.display_name = ''
    newUser.username = ''
    newUser.password = ''
    if (isSystemAdmin.value && activeEnterpriseOptions.value.length) {
      newUser.enterprise_code = activeEnterpriseOptions.value[0].code
    }
    await Promise.all([loadUsers(), loadOverview(), loadAudit()])
  } finally {
    creatingUser.value = false
  }
}

function validateUserForm(payload, { passwordRequired = true } = {}) {
  if (!payload.display_name.trim()) return '姓名为必填项'
  if (payload.username.trim().length < 3) return '用户名至少需要 3 个字符'
  if (passwordRequired && payload.password.length < 6) return '密码至少需要 6 位'
  if (!passwordRequired && payload.password && payload.password.length < 6) return '密码至少需要 6 位'
  return ''
}

function openUserDialog(row) {
  Object.assign(editUser, {
    id: row.id,
    display_name: row.display_name || '',
    username: row.username || '',
    password: '',
    enterprise_code: row.enterprise_code || '',
    enterprise_name: row.enterprise_name || '',
  })
  userDialogVisible.value = true
}

async function saveUser() {
  const validationMessage = validateUserForm(editUser, { passwordRequired: false })
  if (validationMessage) {
    ElMessage.warning(validationMessage)
    return
  }
  savingUser.value = true
  try {
    await updateAdminUser(editUser.id, {
      display_name: editUser.display_name.trim(),
      username: editUser.username.trim(),
      password: editUser.password || undefined,
    })
    userDialogVisible.value = false
    ElMessage.success('员工信息已更新')
    await Promise.all([loadUsers(), loadAudit(), loadOverview()])
  } finally {
    savingUser.value = false
  }
}

async function toggleUser(row) {
  const status = row.status === 'active' ? 'disabled' : 'active'
  await updateAdminUserStatus(row.id, status)
  ElMessage.success(status === 'active' ? '账号已启用' : '账号已禁用')
  await Promise.all([loadUsers(), loadAudit(), loadOverview()])
}

async function removeUser(row) {
  await ElMessageBox.confirm(`确认删除员工“${row.display_name || row.username}”？删除后该账号不能再登录。`, '删除员工', {
    confirmButtonText: '删除员工',
    cancelButtonText: '取消',
    type: 'warning',
  })
  await deleteAdminUser(row.id)
  ElMessage.success('员工已删除')
  await Promise.all([loadUsers(), loadAudit(), loadOverview()])
}

async function createKb() {
  if (!newKbName.value.trim()) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  const createdKb = await createKnowledgeBase(newKbName.value.trim())
  ElMessage.success('知识库已创建')
  newKbName.value = ''
  kbDialogVisible.value = false
  await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
  emit('refresh-kbs', createdKb)
}

function openCreateKbDialog() {
  newKbName.value = ''
  kbDialogVisible.value = true
}

function isKbExpanded(kbId) {
  return expandedKbIds.value.has(kbId)
}

function toggleKbExpanded(kbId) {
  const next = new Set(expandedKbIds.value)
  if (next.has(kbId)) next.delete(kbId)
  else next.add(kbId)
  expandedKbIds.value = next
}

function startEditKb(kb) {
  editingKbId.value = kb.id
  editingKbName.value = kb.name
}

function cancelEditKb() {
  editingKbId.value = null
  editingKbName.value = ''
}

async function saveKbName(kb) {
  if (!editingKbName.value.trim()) return
  await updateKnowledgeBase(kb.id, editingKbName.value.trim())
  ElMessage.success('知识库名称已更新')
  cancelEditKb()
  await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
  emit('refresh-kbs')
}

async function removeKb(kb) {
  try {
    await ElMessageBox.confirm(`确定删除知识库「${kb.name}」？`, '删除知识库', { type: 'warning' })
  } catch (error) {
    return
  }
  const previousKnowledge = knowledge.value
  knowledge.value = {
    ...knowledge.value,
    knowledge_bases: (knowledge.value.knowledge_bases || []).filter((item) => item.id !== kb.id),
  }
  try {
    await deleteKnowledgeBase(kb.id)
    ElMessage.success('知识库已删除')
    const nextSelected = { ...selectedAdminDocIds.value }
    delete nextSelected[kb.id]
    selectedAdminDocIds.value = nextSelected
    await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
    emit('refresh-kbs')
  } catch (error) {
    knowledge.value = previousKnowledge
    await loadKnowledge()
  }
}

async function uploadKbDoc(kb, file) {
  if (!validateUploadFile(file.raw)) return
  setUploadingKb(kb.id, true)
  try {
    await uploadDocument(file.raw, kb.id)
    ElMessage.success(`「${file.name}」已进入解析队列`)
    await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
    startKnowledgePolling()
  } finally {
    setUploadingKb(kb.id, false)
  }
}

async function removeDoc(doc) {
  await ElMessageBox.confirm(`确定删除文档「${doc.filename}」？`, '删除文档', { type: 'warning' })
  const previousKnowledge = knowledge.value
  removeDocsLocally([doc.id])
  setDeletingDoc(doc.id, true)
  try {
    await deleteDocument(doc.id)
    ElMessage.success('文档已删除')
    await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
  } catch (error) {
    knowledge.value = previousKnowledge
    await loadKnowledge()
  } finally {
    setDeletingDoc(doc.id, false)
  }
}

function getSelectedAdminDocIds(kbId) {
  return selectedAdminDocIds.value[String(kbId)] || []
}

function isAdminDocSelected(kbId, docId) {
  return getSelectedAdminDocIds(kbId).includes(docId)
}

function toggleAdminDocSelection(kbId, docId, checked) {
  const key = String(kbId)
  const current = new Set(getSelectedAdminDocIds(kbId))
  if (checked) current.add(docId)
  else current.delete(docId)
  selectedAdminDocIds.value = {
    ...selectedAdminDocIds.value,
    [key]: Array.from(current),
  }
}

async function batchRemoveDocs(kb) {
  const ids = getSelectedAdminDocIds(kb.id)
  if (!ids.length) return
  await ElMessageBox.confirm(`确定删除「${kb.name}」中选中的 ${ids.length} 个文档？`, '批量删除', { type: 'warning' })
  const previousKnowledge = knowledge.value
  removeDocsLocally(ids)
  batchDeletingKbId.value = kb.id
  try {
    await deleteDocuments(ids)
    ElMessage.success(`已删除 ${ids.length} 个文档`)
    selectedAdminDocIds.value = { ...selectedAdminDocIds.value, [String(kb.id)]: [] }
    await Promise.all([loadKnowledge(), loadOverview(), loadAudit()])
  } catch (error) {
    knowledge.value = previousKnowledge
    await loadKnowledge()
  } finally {
    batchDeletingKbId.value = null
  }
}

function removeDocsLocally(ids) {
  const idSet = new Set(ids)
  knowledge.value = {
    ...knowledge.value,
    knowledge_bases: (knowledge.value.knowledge_bases || []).map((kb) => ({
      ...kb,
      documents: kb.documents.filter((doc) => !idSet.has(doc.id)),
      doc_count: Math.max(0, kb.doc_count - kb.documents.filter((doc) => idSet.has(doc.id)).length),
    })),
  }
}

function validateUploadFile(file) {
  const allowed = ['.txt', '.pdf', '.docx', '.csv', '.xlsx', '.xls']
  const ext = '.' + file.name.split('.').pop().toLowerCase()
  if (!allowed.includes(ext)) {
    ElMessage.error('不支持的文件类型，仅支持 .txt / .pdf / .docx / .csv / .xlsx')
    return false
  }
  const maxMb = 20
  if (file.size > maxMb * 1024 * 1024) {
    ElMessage.error(`文件超过大小限制 ${maxMb}MB，请拆分后上传`)
    return false
  }
  return true
}

function setUploadingKb(kbId, active) {
  const next = new Set(uploadingKbIds.value)
  if (active) next.add(kbId)
  else next.delete(kbId)
  uploadingKbIds.value = next
}

function setDeletingDoc(docId, active) {
  const next = new Set(deletingDocIds.value)
  if (active) next.add(docId)
  else next.delete(docId)
  deletingDocIds.value = next
}

function hasProcessingKnowledgeDocs() {
  return (knowledge.value.knowledge_bases || []).some((kb) =>
    kb.documents.some((doc) => doc.status === 'processing'),
  )
}

function startKnowledgePolling() {
  if (knowledgePollTimer || activeTab.value !== 'knowledge') return
  knowledgePollTimer = setInterval(async () => {
    try {
      await loadKnowledge()
      if (!hasProcessingKnowledgeDocs()) stopKnowledgePolling()
    } catch (error) {
      stopKnowledgePolling()
    }
  }, 2500)
}

function stopKnowledgePolling() {
  if (knowledgePollTimer) {
    clearInterval(knowledgePollTimer)
    knowledgePollTimer = null
  }
}

function syncKnowledgePolling() {
  if (hasProcessingKnowledgeDocs()) startKnowledgePolling()
  else stopKnowledgePolling()
}

function kbTotalDocs(kb) {
  const counts = kb.status_counts || {}
  return (counts.done || 0) + (counts.processing || 0) + (counts.failed || 0)
}

function kbStatusPercent(kb, status) {
  const total = kbTotalDocs(kb)
  if (!total) return 0
  return Math.round(((kb.status_counts?.[status] || 0) / total) * 100)
}

function kbProgressPercent(kb) {
  const total = kbTotalDocs(kb)
  if (!total) return 0
  return Math.round(((kb.status_counts?.done || 0) / total) * 100)
}

function docStatusPercent(doc) {
  if (doc.status === 'done') return 100
  if (doc.status === 'failed') return 100
  if (doc.status === 'processing') return doc.chunk_count > 0 ? 72 : 42
  return 0
}

function formatParserName(parserName) {
  if (!parserName) return '待解析'
  const labelMap = {
    markitdown: 'MarkItDown',
    'pypdf+ocr': 'PDF/OCR',
    openpyxl: 'OpenPyXL',
    'python-docx': 'python-docx',
  }
  return labelMap[parserName] || parserName
}

function formatParserDetail(doc) {
  const parser = formatParserName(doc?.parser_name)
  const version = doc?.parser_version ? ` ${doc.parser_version}` : ''
  const chars = doc?.parsed_chars ? `，解析字符 ${doc.parsed_chars}` : ''
  return `${parser}${version}${chars}`
}

async function handleCreateEvaluationRun() {
  const kbId = evaluationKbPayload.value
  if (Array.isArray(kbId) && !kbId.length) {
    ElMessage.warning('请先创建知识库并上传文档')
    return
  }
  if (selectedDataset.value?.status !== 'approved') {
    ElMessage.warning('未审核题集不能运行，请先审核通过')
    return
  }
  if (['rules', 'ragas', 'combined'].includes(evaluationMode.value)) {
    try {
      await ElMessageBox.confirm(`${evaluationModeText(evaluationMode.value)}会调用模型并产生 API 成本，确认开始评测？`, '运行评测', { type: 'warning' })
    } catch (error) {
      return
    }
  }
  evaluationLoading.value = true
  try {
    const run = await createEvaluationRun({
      kb_id: kbId,
      dataset_version: selectedDatasetVersion.value,
      mode: evaluationMode.value,
    })
    selectedEvaluationRun.value = normalizeEvaluationRun({ ...run, mode: run.mode || evaluationMode.value })
    evaluationCases.value = []
    ElMessage.success('评测任务已创建，后台正在运行')
    startEvaluationPolling()
    await Promise.all([loadEvaluationRuns(), loadOverview(), loadAudit()])
  } finally {
    evaluationLoading.value = false
  }
}

async function handleUploadEvaluationDataset(file) {
  try {
    const dataset = await uploadEvaluationDataset(file.raw)
    selectedDatasetVersion.value = dataset.version
    ElMessage.success('题集已上传并生成草稿，请审核后运行')
    await Promise.all([loadEvaluationDatasets(), loadSelectedDatasetDetail(), loadAudit()])
  } catch (error) {
    // 后端解析不出题目时会返回明确的格式提示（缺哪列、该怎么写），
    // 这里直接透给用户，不再笼统地说「上传失败」。
    ElMessage.error(error?.response?.data?.detail || '题集上传失败，请检查文件内容')
  }
}

async function handleDownloadDatasetTemplate() {
  try {
    const blob = await downloadDatasetTemplate()
    saveBlob(blob, '题集上传模板.csv')
  } catch (error) {
    ElMessage.error('题集模板下载失败')
  }
}

async function handleApproveEvaluationDataset() {
  if (!selectedDatasetVersion.value) return
  const dataset = await approveEvaluationDataset(selectedDatasetVersion.value)
  selectedDatasetVersion.value = dataset.version
  ElMessage.success('评测题集已审核通过')
  await Promise.all([loadEvaluationDatasets(), loadSelectedDatasetDetail(), loadAudit()])
}

async function handleDeleteEvaluationDataset() {
  if (!selectedDatasetVersion.value || selectedDataset.value?.source_type === 'builtin') return
  await ElMessageBox.confirm('删除后该评测集题目不可恢复，已生成的历史评测任务不受影响。', '删除评测集', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await deleteEvaluationDataset(selectedDatasetVersion.value)
  selectedDatasetVersion.value = ''
  selectedDatasetDetail.value = { items: [] }
  await Promise.all([loadEvaluationDatasets(), loadAudit()])
  if (evaluationDatasets.value.length) {
    selectedDatasetVersion.value = evaluationDatasets.value[0].version
    await loadSelectedDatasetDetail()
  }
  ElMessage.success('评测集已删除')
}

async function handleDownloadEvaluationDataset(format) {
  if (!selectedDatasetVersion.value) return
  const blob = await downloadEvaluationDataset(selectedDatasetVersion.value, format)
  const suffix = format === 'md' ? 'md' : format
  saveBlob(blob, `${selectedDataset.value?.name || 'RAG评测题集'}.${suffix}`)
}

async function selectEvaluationRun(run) {
  selectedEvaluationRun.value = normalizeEvaluationRun(run)
  if (run?.id) {
    await loadEvaluationCases()
  }
}

function toggleEvaluationRunSelection(id, checked) {
  const next = new Set(selectedEvaluationRunIds.value)
  if (checked) next.add(id)
  else next.delete(id)
  selectedEvaluationRunIds.value = next
}

async function handleDeleteEvaluationRun(run) {
  if (!run?.id) return
  await ElMessageBox.confirm('删除后该评测任务和逐题结果不可恢复。', '删除评测任务', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await deleteEvaluationRun(run.id)
  if (selectedEvaluationRun.value?.id === run.id) {
    selectedEvaluationRun.value = null
    evaluationCases.value = []
  }
  await Promise.all([loadEvaluationRuns(), loadAudit(), loadOverview()])
  if (!selectedEvaluationRun.value && evaluationRuns.value.length) {
    await selectEvaluationRun(evaluationRuns.value[0])
  }
  ElMessage.success('评测任务已删除')
}

async function handleCancelEvaluationRun(run) {
  if (!run?.id || !canCancelEvaluationRun(run)) return
  await ElMessageBox.confirm('中断后当前题会先收尾，后续题目不再继续执行。已完成的逐题结果会保留。', '中断评测任务', {
    type: 'warning',
    confirmButtonText: '中断',
    cancelButtonText: '取消',
  })
  const canceled = await cancelEvaluationRun(run.id)
  selectedEvaluationRun.value = canceled
  ElMessage.success('已发送中断请求')
  startEvaluationPolling()
  await Promise.all([loadEvaluationRuns(), loadEvaluationCases(), loadAudit()])
}

async function handleBatchDeleteEvaluationRuns() {
  const ids = [...selectedEvaluationRunIds.value]
  if (!ids.length) return
  await ElMessageBox.confirm(`确定删除已选的 ${ids.length} 个评测任务吗？逐题结果也会一起删除。`, '批量删除评测任务', {
    type: 'warning',
    confirmButtonText: '删除',
    cancelButtonText: '取消',
  })
  await deleteEvaluationRuns(ids)
  selectedEvaluationRunIds.value = new Set()
  if (selectedEvaluationRun.value && ids.includes(selectedEvaluationRun.value.id)) {
    selectedEvaluationRun.value = null
    evaluationCases.value = []
  }
  await Promise.all([loadEvaluationRuns(), loadAudit(), loadOverview()])
  if (!selectedEvaluationRun.value && evaluationRuns.value.length) {
    await selectEvaluationRun(evaluationRuns.value[0])
  }
  ElMessage.success('已删除选中的评测任务')
}

async function loadEvaluationCases() {
  if (!selectedEvaluationRun.value?.id) return
  const data = await listEvaluationRunCases(selectedEvaluationRun.value.id, {
    category: caseFilters.category || undefined,
    passed: caseFilters.passed || undefined,
  })
  evaluationCases.value = data.items || []
}

async function refreshSelectedEvaluationRun() {
  if (!selectedEvaluationRun.value?.id) return
  const refreshed = await getEvaluationRun(selectedEvaluationRun.value.id)
  selectedEvaluationRun.value = normalizeEvaluationRun(refreshed, selectedEvaluationRun.value)
  await loadEvaluationCases()
}

function hasRunningEvaluation() {
  return evaluationRuns.value.some((run) => isEvaluationActive(run)) || isEvaluationActive(selectedEvaluationRun.value)
}

function startEvaluationPolling() {
  if (evaluationPollTimer || activeTab.value !== 'evaluation') return
  evaluationPollTimer = setInterval(async () => {
    try {
      await Promise.all([loadEvaluationRuns(), refreshSelectedEvaluationRun()])
      if (!hasRunningEvaluation()) stopEvaluationPolling()
    } catch (error) {
      stopEvaluationPolling()
    }
  }, 2500)
}

function stopEvaluationPolling() {
  if (evaluationPollTimer) {
    clearInterval(evaluationPollTimer)
    evaluationPollTimer = null
  }
}

function syncEvaluationPolling() {
  if (hasRunningEvaluation()) startEvaluationPolling()
  else stopEvaluationPolling()
}

async function handleGenerateReport() {
  if (!selectedEvaluationRun.value?.id) return
  const report = await generateEvaluationReport(selectedEvaluationRun.value.id)
  await navigator.clipboard?.writeText(report.markdown)
  ElMessage.success('Markdown 报告已生成并复制到剪贴板')
}

async function handleDownloadReport() {
  if (!selectedEvaluationRun.value?.id) return
  const blob = await downloadEvaluationReport(selectedEvaluationRun.value.id)
  saveBlob(blob, `${selectedEvaluationRun.value.name || 'RAG评测'}-报告.md`)
}

function openCaseDetail(row) {
  activeCase.value = row
  caseDrawerVisible.value = true
}

function saveBlob(data, filename) {
  const blob = data instanceof Blob ? data : new Blob([data])
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

function formatDuration(ms, status = '') {
  if (!ms) {
    if (status === 'success') return '已完成'
    if (status === 'failed') return '未完成'
    if (status === 'canceled') return '已中断'
    return '运行中'
  }
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatList(value) {
  if (Array.isArray(value)) return value.filter(Boolean).join('；')
  return value || ''
}

function runProgressPercent(run) {
  const progress = run?.summary?.progress
  if (progress?.percent !== undefined) return Number(progress.percent) || 0
  if (run?.status === 'success') return 100
  if (run?.status === 'canceled' || run?.status === 'failed') return 0
  return run?.status === 'running' ? 3 : 0
}

function runProgressStatus(run) {
  if (run?.status === 'failed') return 'exception'
  if (run?.status === 'success') return 'success'
  if (run?.status === 'canceling' || run?.status === 'canceled') return 'warning'
  return undefined
}

function canCancelEvaluationRun(run) {
  return run?.status === 'running'
}

function isEvaluationActive(run) {
  return ['running', 'canceling'].includes(run?.status)
}

function runProgressText(run) {
  const progress = run?.summary?.progress
  if (!progress) return evaluationRunStatusText(run?.status)
  return `${progress.completed || 0}/${progress.total || 0} · ${progress.percent || 0}%`
}

function formatMetricRate(value) {
  if (value === null || value === undefined) return '未统计'
  return `${value}%`
}

// 补召回前的检索命中标记：null 表示该次评测没统计，用灰色区分，不能当成未命中。
function retrievalHitClass(value) {
  if (value === null || value === undefined) return 'unknown'
  return value ? 'ok' : ''
}

function formatEvidenceHit(value) {
  if (value === true) return '命中'
  if (value === false) return '未命中'
  return '不适用'
}

function formatCrossDocumentDiagnostic(diagnostics = {}) {
  if (diagnostics.passed) return '已完成'
  if (diagnostics.over_refusal) return '过度拒答'
  if (diagnostics.reason) return diagnostics.reason
  return '待诊断'
}

function formatRagasMetricLabel(key) {
  const labels = {
    faithfulness: '忠实度',
    context_recall: '上下文召回',
    llm_context_recall: '上下文召回',
    factual_correctness: '事实一致性',
    answer_relevancy: '回答相关性',
    response_relevancy: '回答相关性',
  }
  return labels[key] || key
}

function formatRagasScores(scores = {}) {
  return Object.entries(scores)
    .map(([key, value]) => `${formatRagasMetricLabel(key)} ${Number(value || 0).toFixed(2)}`)
    .join('；')
}

const evaluationModeNote = computed(() => {
  const notes = {
    retrieval: '检索评测只统计召回、章节和证据命中，不调用模型生成答案。',
    rules: '主评测会调用模型生成答案，只展示企业制度业务规则指标，不展示 RAGAS 诊断指标。',
    ragas: 'RAGAS诊断会调用模型和第三方诊断指标，只展示辅助质量诊断结果，不计入主评测通过率。',
    combined: '主评测+诊断会同时展示业务规则指标和 RAGAS 辅助诊断，最终通过率仍以主评测为准。',
  }
  return notes[evaluationMode.value] || '请选择评测模式。'
})

const evaluationMetrics = computed(() => {
  const summary = selectedEvaluationRun.value?.summary || {}
  const mode = evaluationRunMode(selectedEvaluationRun.value)
  // 检索侧拆成两组：补召回前是真实检索能力，补召回后是生成实际拿到的证据是否完整。
  // 两者混在一起会把「证据在不在库里」误读成「检索排得准不准」。
  const retrievalMetrics = [
    { label: '检索 Top-1', value: formatMetricRate(summary.retrieval_top1_doc_hit_rate), note: '补召回前，真实检索首位命中' },
    { label: '检索 Top-3', value: formatMetricRate(summary.retrieval_top3_doc_hit_rate), note: '补召回前，真实检索前三命中' },
    { label: '检索 Top-5', value: formatMetricRate(summary.retrieval_top5_doc_hit_rate), note: '补召回前，真实检索前五命中' },
    { label: '检索章节', value: formatMetricRate(summary.retrieval_section_hit_rate), note: '补召回前，真实检索章节命中' },
    { label: '证据 Top-5', value: formatMetricRate(summary.top5_doc_hit_rate), note: '补召回后，证据是否可用' },
    { label: '证据章节', value: formatMetricRate(summary.section_hit_rate), note: '补召回后，章节是否可用' },
    { label: '必需证据', value: formatMetricRate(summary.required_citation_hit_rate), note: '主辅来源整体命中' },
    { label: '主证据', value: formatMetricRate(summary.primary_evidence_hit_rate), note: '主要制度依据命中' },
    { label: '辅助证据', value: formatMetricRate(summary.secondary_evidence_hit_rate), note: '跨文档辅助依据命中' },
  ]
  const businessMetrics = [
    // 两个通过率并列：上面那个是补召回后的口径（衡量生成端上限），下面那个才是用户真实路径。
    // 两者的差就是评测链路替用户补齐的证据带来的收益，不能只报上面那个。
    { label: '总通过率', value: formatMetricRate(summary.pass_rate), note: '补召回后，衡量生成端上限' },
    { label: '端到端通过率', value: formatMetricRate(summary.e2e_pass_rate), note: '不补召回，用户真实路径' },
    { label: '答案覆盖', value: formatMetricRate(summary.answer_coverage_rate), note: '标准答案要点覆盖' },
    { label: '引用正确', value: formatMetricRate(summary.citation_pass_rate), note: '回答引用支撑结论' },
    { label: '拒答正确', value: formatMetricRate(summary.refusal_pass_rate), note: '未覆盖问题拒答' },
  ]
  const ragasMetrics = [
    { label: 'RAGAS诊断通过', value: formatMetricRate(summary.ragas_pass_rate), note: '辅助质量诊断' },
    { label: 'RAGAS预警', value: summary.ragas_warning_count ?? 0, note: '诊断指标低于阈值' },
    { label: '需复核', value: summary.needs_review_count ?? 0, note: '业务通过但诊断预警' },
  ]
  let metrics = []
  if (mode === 'retrieval') {
    metrics = retrievalMetrics
  } else if (mode === 'rules') {
    metrics = [...retrievalMetrics, ...businessMetrics]
  } else if (mode === 'ragas') {
    metrics = ragasMetrics
  } else if (mode === 'combined') {
    metrics = [...retrievalMetrics, ...businessMetrics, ...ragasMetrics]
  }
  metrics.push({ label: 'P95 检索耗时', value: `${summary.p95_latency_ms ?? 0}ms`, note: evaluationRunStatusText(selectedEvaluationRun.value?.status) })
  // 工程失败单列出来：它不计入能力指标的分母，但必须让人看得见，不能藏。
  if (summary.engineering_failure_count) {
    metrics.push({
      label: '工程失败',
      value: summary.engineering_failure_count,
      note: '超时/断连等，已从能力指标分母剔除',
    })
  }
  return metrics
})

function evaluationRunMode(run) {
  const mode = run?.mode || ''
  if (mode === 'full' || mode === 'e2e') {
    return 'rules'
  }
  return mode
}

function normalizeEvaluationRun(run, previousRun = null) {
  if (!run) return run
  const previousMode = previousRun?.id === run.id ? evaluationRunMode(previousRun) : ''
  return {
    ...run,
    mode: evaluationRunMode(run) || previousMode || 'retrieval',
  }
}

function evaluationRunHasGenerationMetrics(run) {
  return ['rules', 'combined'].includes(evaluationRunMode(run))
}

function evaluationRunHasRagasMetrics(run) {
  return ['ragas', 'combined'].includes(evaluationRunMode(run))
}

function formatPassRate(item) {
  return item.pass_rate === null || item.pass_rate === undefined ? '未运行' : `${item.pass_rate}%`
}

function formatCategoryCount(item) {
  if (item.pass_rate === null || item.pass_rate === undefined) {
    return `${item.skipped_count || 0}/${item.case_count} 已跳过`
  }
  return `${item.pass_count}/${item.case_count - (item.skipped_count || 0)}`
}
</script>

<style scoped>
.admin-console {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  gap: 18px;
  padding: 18px;
  position: relative;
  z-index: 1;
}

.admin-panel {
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(23, 26, 34, 0.075);
  box-shadow: 0 16px 42px rgba(15, 23, 42, 0.06);
  backdrop-filter: blur(14px);
}

.admin-rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: 22px;
  padding: 16px;
  background:
    radial-gradient(circle at 16% 0%, rgba(37, 99, 235, 0.16), transparent 34%),
    linear-gradient(180deg, rgba(247, 250, 255, 0.96) 0%, rgba(232, 239, 250, 0.92) 100%);
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.12);
  backdrop-filter: blur(16px);
}

.admin-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px;
  border: 1px solid rgba(37, 99, 235, 0.1);
  border-radius: 18px;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(239, 246, 255, 0.76));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.88), 0 12px 30px rgba(37, 99, 235, 0.055);
}

.admin-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  color: #fff;
  background: linear-gradient(145deg, #111827, #2454d6 120%);
  font-size: 17px;
  font-weight: 850;
}

.admin-title {
  font-size: 14px;
  font-weight: 800;
  color: var(--kb-text);
}

.admin-subtitle {
  margin-top: 2px;
  font-size: 11px;
  color: var(--kb-text-muted);
}

.admin-nav {
  display: grid;
  gap: 6px;
  margin-top: 22px;
}

.admin-nav button,
.back-home {
  height: 38px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 13px;
  background: transparent;
  color: var(--kb-text-secondary);
  font: inherit;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}

.admin-nav button {
  padding: 0 12px;
}

.admin-nav button:hover,
.admin-nav button.active {
  color: var(--kb-primary-dark);
  background: rgba(255, 255, 255, 0.82);
}

.admin-nav button.active {
  background: linear-gradient(135deg, rgba(239, 246, 255, 0.98), rgba(219, 234, 254, 0.72));
  box-shadow: inset 0 0 0 1px rgba(37, 99, 235, 0.16), 0 12px 26px rgba(37, 99, 235, 0.08);
}

.back-home {
  justify-content: center;
  margin-top: auto;
  border: 1px solid rgba(37, 99, 235, 0.16);
  background: rgba(255, 255, 255, 0.72);
  color: var(--kb-primary-dark);
}

.admin-main {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow: hidden;
}

.admin-head {
  position: relative;
  overflow: hidden;
  min-height: 88px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24px 28px;
  border: 1px solid rgba(255, 255, 255, 0.78);
  border-radius: 24px;
  background:
    radial-gradient(circle at 84% 0%, rgba(37, 99, 235, 0.16), transparent 26%),
    linear-gradient(135deg, rgba(255, 255, 255, 0.94), rgba(241, 247, 255, 0.82));
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.095);
}

.admin-head::after {
  content: '';
  position: absolute;
  left: 28px;
  right: 28px;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(37, 99, 235, 0.42), rgba(15, 118, 110, 0.22), transparent);
}

.admin-head h1 {
  font-size: 28px;
  line-height: 1.2;
  letter-spacing: 0;
}

.admin-head p {
  margin-top: 8px;
  color: var(--kb-text-muted);
  font-size: 13px;
}

.admin-user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px 7px 7px;
  border: 1px solid rgba(23, 26, 34, 0.08);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.82);
  box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
}

.admin-user-chip > span {
  width: 26px;
  height: 26px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  color: #fff;
  background: var(--kb-graphite);
}

.admin-user-chip strong,
.admin-user-chip small {
  display: block;
  line-height: 1.1;
}

.admin-user-chip strong {
  font-size: 12px;
}

.admin-user-chip small {
  margin-top: 2px;
  color: var(--kb-text-muted);
  font-size: 10px;
}

.admin-section {
  min-height: 0;
  overflow: auto;
  animation: kb-float-in 0.28s var(--kb-ease) both;
}

.admin-section.two-column {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 360px;
  gap: 18px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 18px;
}

.metric-card {
  position: relative;
  overflow: hidden;
  min-height: 92px;
  padding: 18px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.055);
}

.metric-card::after {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 0;
  height: 2px;
  background: linear-gradient(90deg, rgba(37, 99, 235, 0.58), rgba(15, 118, 110, 0.24));
  opacity: 0.42;
}

.metric-card span {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.metric-card strong {
  display: block;
  margin-top: 10px;
  color: var(--kb-text);
  font-size: 28px;
  line-height: 1;
}

.admin-panel {
  border-radius: 20px;
  padding: 20px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.panel-head h2 {
  font-size: 16px;
  line-height: 1.3;
}

.panel-head p,
.panel-note {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.panel-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.enterprise-toolbar {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) 280px 180px auto;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
  padding: 14px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  background:
    linear-gradient(135deg, rgba(248, 250, 252, 0.96), rgba(255, 255, 255, 0.98)),
    radial-gradient(circle at 0 0, rgba(37, 99, 235, 0.08), transparent 34%);
}

.enterprise-toolbar-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.enterprise-toolbar-copy strong {
  color: var(--kb-text);
  font-size: 14px;
  line-height: 1.25;
}

.enterprise-toolbar-copy span {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.enterprise-toolbar-search,
.enterprise-toolbar-filter {
  min-width: 0;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.enterprise-toolbar-search > span,
.enterprise-toolbar-filter > span {
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 720;
  white-space: nowrap;
}

.enterprise-search {
  width: 190px;
}

.status-filter {
  width: 108px;
}

.enterprise-toolbar-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  white-space: nowrap;
}

.enterprise-table :deep(.el-table__cell) {
  padding: 12px 0;
  font-size: 13px;
}

.enterprise-info-cell,
.enterprise-contact-cell {
  min-width: 0;
  display: grid;
  gap: 5px;
  line-height: 1.25;
}

.enterprise-info-cell strong,
.enterprise-contact-cell strong {
  min-width: 0;
  overflow: hidden;
  color: var(--kb-text);
  font-size: 14px;
  font-weight: 760;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enterprise-info-cell span {
  width: fit-content;
  max-width: 100%;
  overflow: hidden;
  padding: 3px 8px;
  border: 1px solid rgba(37, 99, 235, 0.12);
  border-radius: 999px;
  background: rgba(37, 99, 235, 0.06);
  color: var(--kb-primary-dark);
  font-size: 12px;
  font-weight: 720;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enterprise-contact-cell span {
  min-width: 0;
  overflow: hidden;
  color: var(--kb-text-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.enterprise-form {
  padding: 4px 8px 0;
}

.enterprise-row-actions {
  display: inline-flex;
  align-items: center;
  justify-content: end;
  gap: 8px;
  white-space: nowrap;
  min-height: 32px;
}

.user-row-actions {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: max-content;
  align-items: center;
  justify-content: end;
  gap: 6px;
  white-space: nowrap;
  min-height: 32px;
}

.enterprise-row-actions :deep(.el-button),
.user-row-actions :deep(.el-button) {
  margin-left: 0;
}

.enterprise-row-actions :deep(.el-button) {
  min-width: 62px;
  font-size: 13px;
}

.text-action {
  border: 0;
  background: transparent;
  color: var(--kb-primary-dark);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.eval-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.eval-kb-select {
  width: 180px;
}

.full-width {
  width: 100%;
}

.evaluation-workbench {
  min-height: 620px;
}

.evaluation-stage-grid {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(260px, 340px) minmax(0, 1fr);
  gap: 14px;
  margin-bottom: 14px;
}

.eval-stage-card {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 14px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  background: linear-gradient(180deg, #f8fafc, #fff);
}

.dataset-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--kb-text-muted);
  font-size: 12px;
}

.eval-dataset-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.eval-dataset-actions :deep(.el-button) {
  width: 100%;
  margin-left: 0;
}

.dataset-case-preview {
  max-height: 220px;
  display: grid;
  gap: 6px;
  overflow: auto;
}

.dataset-case-row {
  display: block;
  padding: 7px 8px;
  border: 1px solid var(--kb-border-light);
  border-radius: 8px;
  background: #fff;
  color: var(--kb-text-secondary);
  font: inherit;
  text-align: left;
}

.dataset-case-row summary {
  display: grid;
  gap: 3px;
  cursor: pointer;
}

.dataset-case-row strong,
.dataset-case-detail dt {
  color: var(--kb-text);
  font-size: 12px;
}

.dataset-case-row span {
  color: var(--kb-text-muted);
  font-size: 12px;
  line-height: 1.5;
}

.dataset-case-detail {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 6px 8px;
  margin: 8px 0 0;
  padding-top: 8px;
  border-top: 1px solid var(--kb-border-light);
}

.dataset-case-detail dt {
  font-weight: 760;
}

.dataset-case-detail dd {
  min-width: 0;
  margin: 0;
  color: var(--kb-text-secondary);
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.evaluation-layout {
  display: grid;
  grid-template-columns: 296px minmax(0, 1fr);
  gap: 14px;
}

.evaluation-run-list {
  display: grid;
  align-content: start;
  gap: 8px;
  padding: 12px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  background: linear-gradient(180deg, #f8fafc, #fff);
}

.run-list-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.text-action.danger {
  color: #ef4444;
}

.text-action:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.subsection-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--kb-text-secondary);
  font-size: 13px;
  font-weight: 760;
}

.evaluation-run-item {
  min-height: 74px;
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) 54px;
  gap: 5px 8px;
  padding: 12px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 16px;
  background: #fff;
  color: var(--kb-text-secondary);
}

.evaluation-run-item.active {
  border-color: rgba(36, 84, 214, 0.28);
  background: var(--kb-primary-faint);
}

.evaluation-run-select {
  grid-column: 2;
  min-width: 0;
  display: grid;
  gap: 5px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.evaluation-run-item strong {
  overflow: hidden;
  color: var(--kb-text);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evaluation-run-item small {
  color: var(--kb-text-muted);
  font-size: 11px;
}

.run-mode-pill {
  display: inline-flex;
  align-items: center;
  margin-right: 4px;
  padding: 1px 6px;
  border: 1px solid rgba(36, 84, 214, 0.16);
  border-radius: 999px;
  background: rgba(36, 84, 214, 0.08);
  color: var(--kb-primary-dark);
  font-size: 10px;
  font-weight: 760;
  line-height: 1.5;
}

.evaluation-run-item span {
  grid-row: 1;
  grid-column: 3;
  justify-self: end;
  color: var(--kb-primary-dark);
  font-weight: 800;
}

.run-delete-btn {
  grid-row: 3;
  grid-column: 3;
  justify-self: end;
  align-self: end;
  padding: 0;
  border: 0;
  background: transparent;
  color: #ef4444;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.run-cancel-btn {
  grid-row: 2;
  grid-column: 3;
  justify-self: end;
  align-self: center;
  padding: 0;
  border: 0;
  background: transparent;
  color: #d97706;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.run-cancel-btn:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}

.evaluation-detail {
  min-width: 0;
}

.evaluation-progress-panel {
  display: grid;
  gap: 8px;
  margin-bottom: 10px;
  padding: 12px 14px;
  border: 1px solid rgba(23, 26, 34, 0.075);
  border-radius: 18px;
  background: linear-gradient(90deg, #f8fafc, #fff);
}

.evaluation-progress-panel > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.evaluation-progress-panel span,
.evaluation-progress-panel small {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.evaluation-progress-panel strong {
  color: var(--kb-text);
  font-size: 14px;
}

.run-error-message {
  color: #ef4444;
  overflow-wrap: anywhere;
}

.create-user-card {
  align-self: start;
  display: grid;
  gap: 10px;
}

.create-user-card label span {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 5px;
  color: var(--kb-text-secondary);
  font-size: 12px;
  font-weight: 680;
}

.field-required {
  display: inline-flex;
  align-items: center;
  height: auto;
  padding: 0;
  color: #b91c1c;
  background: transparent;
  font-size: 15px;
  font-weight: 900;
  line-height: 1;
}

.model-key-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.key-test-result {
  display: grid;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #f8fafc;
}

.key-test-result > div {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr) 64px;
  align-items: center;
  gap: 8px;
}

.key-test-result span {
  min-width: 0;
  color: var(--kb-text-secondary);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.key-test-result small {
  justify-self: end;
  color: var(--kb-text-muted);
  font-size: 11px;
}

.settings-list {
  display: grid;
  gap: 10px;
}

.settings-list > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  padding: 0 12px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  background: #f8fafc;
}

.settings-list span {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.settings-list strong {
  color: var(--kb-text);
  font-size: 13px;
}

.inline-create {
  display: flex;
  gap: 6px;
  width: 300px;
}

.kb-governance-list {
  display: grid;
  gap: 8px;
}

.kb-governance-card {
  padding: 14px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #f8fafc;
}

.kb-card-head,
.status-row,
.upload-row,
.doc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.kb-card-head {
  justify-content: flex-start;
}

.kb-fold-btn {
  width: 68px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  flex-shrink: 0;
  border: 1px solid rgba(37, 99, 235, 0.14);
  border-radius: var(--kb-radius);
  background: var(--kb-primary-faint);
  color: var(--kb-primary-dark);
  font: inherit;
  font-size: 12px;
  font-weight: 730;
  cursor: pointer;
  transition: all var(--kb-duration) var(--kb-ease);
}

.kb-fold-btn:hover {
  transform: translateY(-1px);
  box-shadow: var(--kb-shadow-sm);
}

.kb-title-block {
  flex: 1;
  min-width: 0;
}

.kb-card-actions {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.kb-edit-row {
  display: grid;
  grid-template-columns: minmax(180px, 280px) auto auto;
  align-items: center;
  gap: 6px;
}

.kb-card-head h3 {
  font-size: 15px;
}

.kb-card-head p,
.doc-row small {
  margin-top: 3px;
  color: var(--kb-text-muted);
  font-size: 12px;
}

.status-row {
  justify-content: flex-start;
  margin: 10px 0;
}

.status-row span {
  padding: 3px 8px;
  border: 1px solid var(--kb-border);
  border-radius: 999px;
  background: #fff;
  color: var(--kb-text-secondary);
  font-size: 12px;
}

.kb-progress-block {
  display: grid;
  gap: 6px;
  margin: 8px 0 10px;
}

.kb-progress-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--kb-text-muted);
  font-size: 12px;
}

.kb-progress-meta strong {
  color: var(--kb-text-secondary);
  font-size: 12px;
}

.kb-progress-bar {
  height: 8px;
  display: flex;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 999px;
  background: rgba(226, 232, 240, 0.7);
}

.kb-progress-segment {
  display: block;
  height: 100%;
}

.kb-progress-segment.done {
  background: linear-gradient(90deg, #14b8a6, #22c55e);
}

.kb-progress-segment.processing {
  background: linear-gradient(90deg, #60a5fa, #f59e0b);
}

.kb-progress-segment.failed {
  background: #fb7185;
}

.kb-detail-panel {
  margin-top: 10px;
  padding: 8px 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #fff;
  animation: kb-float-in 0.2s var(--kb-ease) both;
}

.upload-row {
  min-height: 36px;
}

.upload-row > div:first-child span {
  display: block;
  color: var(--kb-text-secondary);
  font-size: 13px;
  font-weight: 740;
}

.upload-row > div:first-child small {
  display: block;
  margin-top: 3px;
  color: var(--kb-text-muted);
  font-size: 11px;
}

.upload-actions {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.doc-row {
  min-height: 34px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 126px 132px 64px;
  border-top: 1px solid var(--kb-border-light);
}

.doc-row-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 126px 132px 64px;
}

.doc-progress-cell {
  width: 120px;
  flex-shrink: 0;
  display: grid;
  gap: 5px;
}

.doc-parser-cell {
  min-width: 0;
  overflow: hidden;
  color: var(--kb-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-parser-cell span {
  color: var(--kb-text-faint);
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
  width: 100%;
  height: 5px;
  display: block;
  overflow: hidden;
  border-radius: 999px;
  background: rgba(226, 232, 240, 0.8);
}

.doc-progress b {
  display: block;
  height: 100%;
  border-radius: inherit;
}

.doc-progress b.done {
  background: #22c55e;
}

.doc-progress b.processing {
  background: #60a5fa;
}

.doc-progress b.failed {
  background: #fb7185;
}

.doc-row-head {
  min-height: 28px;
  color: var(--kb-text-muted);
  font-size: 12px;
  font-weight: 720;
}

.doc-select {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.doc-select span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.eval-grid {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 8px;
}

.eval-result {
  min-height: 92px;
  display: grid;
  align-content: center;
  padding: 12px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #fff;
}

.eval-result span {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.eval-result strong {
  margin-top: 8px;
  font-size: 22px;
}

.eval-result small {
  font-size: 15px;
}

.eval-result em {
  margin-top: 6px;
  color: var(--kb-text-muted);
  font-size: 12px;
  font-style: normal;
}

.case-toolbar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin: 10px 0;
}

.evaluation-case-table {
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  overflow: hidden;
}

.hit-dot {
  width: 24px;
  height: 24px;
  display: inline-grid;
  place-items: center;
  margin-right: 5px;
  border-radius: 999px;
  background: rgba(226, 232, 240, 0.9);
  color: var(--kb-text-muted);
  font-size: 11px;
  font-weight: 800;
}

.hit-dot.ok {
  background: rgba(34, 197, 94, 0.14);
  color: #16a34a;
}

/* 补召回前没统计过的旧评测：用更浅的灰区分“未统计”，不要看起来像“未命中”。 */
.hit-dot.unknown {
  background: rgba(226, 232, 240, 0.5);
  color: rgba(148, 163, 184, 0.65);
}

.hit-row {
  display: flex;
  align-items: center;
}

.hit-row small {
  width: 26px;
  color: var(--kb-text-muted);
  font-size: 10px;
}

.case-drawer {
  color: var(--kb-text-secondary);
}

.case-drawer h3 {
  margin-bottom: 14px;
  color: var(--kb-text);
  font-size: 17px;
  line-height: 1.5;
}

.case-drawer dl {
  display: grid;
  gap: 10px;
}

.case-drawer dt {
  color: var(--kb-text);
  font-size: 13px;
  font-weight: 800;
}

.case-drawer dd {
  margin: 0;
  padding: 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  background: #f8fafc;
  font-size: 13px;
  line-height: 1.7;
}

.eval-meta {
  min-height: 96px;
  display: grid;
  grid-template-columns: repeat(2, 120px) minmax(0, 1fr);
  align-items: center;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #fff;
}

.eval-meta span,
.eval-category-grid span {
  color: var(--kb-text-muted);
  font-size: 12px;
}

.eval-meta strong {
  display: block;
  margin-top: 6px;
  color: var(--kb-text);
  font-size: 18px;
}

.eval-meta p {
  color: var(--kb-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.eval-category-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  margin-top: 10px;
}

.eval-category-grid article {
  min-height: 78px;
  padding: 10px;
  border: 1px solid var(--kb-border-light);
  border-radius: 10px;
  background: #fff;
}

.eval-category-grid strong,
.eval-category-grid small {
  display: block;
}

.eval-category-grid strong {
  margin-top: 8px;
  font-size: 20px;
}

.eval-category-grid small {
  margin-top: 4px;
  color: var(--kb-text-muted);
  font-size: 12px;
}

.audit-list {
  display: grid;
  gap: 6px;
}

.audit-list.compact {
  gap: 4px;
}

.audit-item {
  display: grid;
  grid-template-columns: minmax(128px, 170px) minmax(110px, 150px) minmax(0, 1fr) 158px;
  align-items: center;
  gap: 12px;
  min-height: 48px;
  padding: 11px 14px;
  border: 1px solid var(--kb-border-light);
  border-radius: 12px;
  background: #fff;
  color: var(--kb-text-secondary);
  font-size: 13px;
}

.audit-list.compact .audit-item {
  grid-template-columns: minmax(116px, 146px) minmax(0, 1fr) 142px;
}

.audit-action {
  min-width: 0;
  overflow: hidden;
  color: var(--kb-primary-dark);
  font-weight: 740;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-actor {
  min-width: 0;
  overflow: hidden;
  color: var(--kb-text);
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-target {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.audit-item time {
  justify-self: end;
  white-space: nowrap;
}

@media (max-width: 980px) {
  .admin-console {
    grid-template-columns: 1fr;
  }
  .admin-rail {
    display: none;
  }
  .metric-grid,
  .admin-section.two-column,
  .evaluation-layout,
  .enterprise-toolbar,
  .eval-grid,
  .eval-category-grid {
    grid-template-columns: 1fr;
  }
}
</style>
