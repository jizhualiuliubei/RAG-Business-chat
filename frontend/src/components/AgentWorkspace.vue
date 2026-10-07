<template>
  <main class="agent-workspace">
    <aside class="agent-rail">
      <div class="rail-heading">
        <button class="icon-button" type="button" title="返回问答工作台" @click="$emit('back-to-home')">
          <el-icon><ArrowLeft /></el-icon>
        </button>
        <div>
          <span>企业合规执行中心</span>
          <small>申请 · 审查 · 审批 · 执行</small>
        </div>
      </div>

      <nav class="rail-nav" aria-label="合规执行导航">
        <button :class="{ active: activeView === 'tasks' }" type="button" @click="activeView = 'tasks'">
          <el-icon><Tickets /></el-icon><span>任务工作台</span><b>{{ tasks.length }}</b>
        </button>
        <button v-if="isAgentApprover" :class="{ active: activeView === 'approvals' }" type="button" @click="openApprovals">
          <el-icon><Stamp /></el-icon><span>审批中心</span><b aria-live="polite" :title="approvalCount === null ? '暂未获取待处理审批数量' : '待处理审批数量'">{{ approvalCount ?? '-' }}</b>
        </button>
        <button v-if="isEnterpriseAdmin" :class="{ active: activeView === 'settings' }" type="button" @click="openSettings">
          <el-icon><Setting /></el-icon><span>Skill 与规则</span>
        </button>
        <button v-if="isEnterpriseAdmin" :class="{ active: activeView === 'evaluation' }" type="button" @click="openEvaluation">
          <el-icon><DataAnalysis /></el-icon><span>Agent 评测</span>
        </button>
      </nav>

      <div v-if="activeView === 'tasks'" class="task-queue">
        <div class="queue-head">
          <span>我的任务</span>
          <button class="icon-button" type="button" title="刷新任务列表" :disabled="tasksLoading" @click="refreshTaskList">
            <el-icon><Refresh /></el-icon>
          </button>
        </div>
        <button class="new-task-button" type="button" @click="beginNewTask">
          <el-icon><Plus /></el-icon><span>新建执行任务</span>
        </button>
        <div class="task-list">
          <button
            v-for="item in tasks"
            :key="item.id"
            class="task-row"
            :class="{ selected: selectedTask?.id === item.id }"
            type="button"
            @click="selectTask(item.id)"
          >
            <span class="task-kind" :class="item.skill_id">
              <el-icon><ShoppingCart v-if="item.skill_id === 'procurement'" /><Key v-else /></el-icon>
            </span>
            <span class="task-copy">
              <strong>{{ item.goal }}</strong>
              <small>#{{ item.id }} · {{ taskStatusLabel(item) }}</small>
            </span>
            <i class="status-dot" :class="item.status"></i>
          </button>
          <div v-if="!tasks.length && !tasksLoading" class="queue-empty">暂无执行任务</div>
        </div>
      </div>

      <div class="rail-health" :class="health?.agent_available ? 'healthy' : 'degraded'">
        <span></span>
        <div>
          <strong>{{ health?.agent_available ? '执行引擎在线' : '执行引擎降级' }}</strong>
          <small>知识库问答不受影响</small>
        </div>
      </div>
    </aside>

    <section class="agent-stage">
      <header class="stage-head">
        <div>
          <h1>{{ viewTitle }}</h1>
        </div>
        <div class="stage-actions">
          <span v-if="lastRefreshed && selectedTask" class="refresh-time">更新于 {{ lastRefreshed }}</span>
          <el-button v-if="activeView === 'tasks' && selectedTask" :loading="refreshing" @click="refreshSelectedTask({ manual: true })">
            <el-icon><Refresh /></el-icon>刷新
          </el-button>
          <el-button v-if="activeView === 'tasks' && selectedTask?.artifact" @click="downloadArtifact">
            <el-icon><Download /></el-icon>执行档案
          </el-button>
          <el-button v-if="activeView === 'tasks' && canWithdraw" :loading="withdrawing" :disabled="deleting" @click="withdrawTask">
            <el-icon><RefreshLeft /></el-icon>撤回申请
          </el-button>
          <el-tooltip v-if="activeView === 'tasks' && isTaskOwner && ['cancelled', 'cancel_requested', 'rejected'].includes(selectedTask?.status)" :content="deleteReason || '从任务列表删除，保留必要审计历史'" placement="bottom">
            <span><el-button type="danger" plain :disabled="!canDelete || withdrawing" :loading="deleting" @click="deleteTask">
              <el-icon><Delete /></el-icon>删除任务
            </el-button></span>
          </el-tooltip>
        </div>
      </header>

      <div v-if="activeView === 'tasks'" class="stage-scroll" :class="{ 'creation-view': !selectedTask }">
        <section v-if="!selectedTask" class="create-surface">
          <div class="create-copy">
            <h2>描述需要执行的企业事项</h2>
          </div>
          <div class="supported-capabilities"><span class="capabilities-label">支持的任务</span><div class="request-types" role="list" aria-label="可办理事项">
            <div v-for="skill in enabledSkills" :key="skill.id" class="request-type" role="listitem">
              <span class="request-type-icon"><el-icon><ShoppingCart v-if="skill.id === 'procurement'" /><Key v-else /></el-icon></span>
              <span><strong>{{ skill.name }}</strong></span>
            </div>
          </div></div>
          <div class="goal-editor"><label for="agent-goal">申请内容</label>
          <el-input
            id="agent-goal"
            v-model="newGoal"
            type="textarea"
            :rows="5"
            maxlength="4000"
            show-word-limit
            placeholder="例如：为研发团队采购 10 台显示器，预算 3 万元，供应商为……"
          />
          </div>
          <div class="create-footer">
            <div class="kb-picker"><label for="agent-kb-picker">制度检索范围</label>
            <el-select id="agent-kb-picker" v-model="newKbIds" multiple collapse-tags placeholder="选择制度知识库">
              <el-option v-for="kb in knowledgeBases" :key="kb.id" :label="kb.name" :value="kb.id" />
            </el-select>
            </div>
            <el-button type="primary" :loading="creating" @click="createTask">
              <el-icon><Promotion /></el-icon>提取申请信息
            </el-button>
          </div>
          <ol class="create-workflow" aria-label="办理流程"><li><el-icon><EditPen /></el-icon>提交申请</li><li><el-icon><Search /></el-icon>制度与风险审查</li><li><el-icon><Stamp /></el-icon>按要求审批</li><li><el-icon><CircleCheck /></el-icon>生成执行记录</li></ol>
        </section>

        <template v-else>
          <section class="task-command-bar">
            <div class="task-identity">
              <span class="task-kind large" :class="selectedTask.skill_id">
                <el-icon><ShoppingCart v-if="selectedTask.skill_id === 'procurement'" /><Key v-else /></el-icon>
              </span>
              <div>
                <p>#{{ selectedTask.id }} · {{ skillName(selectedTask.skill_id) }}</p>
                <h2>{{ selectedTask.goal }}</h2>
                <p v-if="selectedTask.applicant" class="task-applicant">申请人：<strong>{{ selectedTask.applicant.display_name }}</strong><span v-if="selectedTask.applicant.username">（{{ selectedTask.applicant.username }}）</span><span>申请时间：{{ formatApprovalTime(selectedTask.created_at) }}</span></p>
              </div>
            </div>
            <div class="command-meta">
              <span class="status-badge" :class="selectedTask.status">{{ taskStatusLabel(selectedTask) }}</span>
              <span v-if="selectedTask.risk_level" class="risk-badge" :class="selectedTask.risk_level">
                {{ riskLabel(selectedTask.risk_level) }}风险
              </span>
            </div>
          </section>

          <el-alert v-if="selectedTask.error_message && !selectedTask.withdrawn_at" :title="localizeText(selectedTask.error_message)" type="error" show-icon :closable="false" />

          <section class="execution-status" :class="selectedTask.status" aria-live="polite" aria-atomic="true">
            <el-icon v-if="isRunning || selectedTask.status === 'cancel_requested'" class="is-loading"><Loading /></el-icon>
            <el-icon v-else-if="selectedTask.status === 'waiting_approval'"><Stamp /></el-icon>
            <el-icon v-else-if="selectedTask.status === 'completed'"><CircleCheckFilled /></el-icon>
            <el-icon v-else><InfoFilled /></el-icon>
            <div><strong>{{ executionTitle }}</strong><p>{{ executionDescription }}</p></div>
            <el-button v-if="selectedTask.status === 'waiting_approval' && selectedTask.approval_context?.can_decide" @click="openApprovals">前往审批中心</el-button>
          </section>

          <section class="pipeline-strip" aria-label="执行流程">
            <div v-for="(stage, index) in pipelineStages" :key="stage.id" class="pipeline-stage" :class="pipelineState(stage.id)">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <div><strong>{{ stage.label }}</strong><small>{{ stage.note }}</small></div>
            </div>
          </section>

          <div class="task-layout">
            <div class="task-primary">
              <section v-if="selectedTask.status === 'waiting_input'" class="work-section input-required">
                <div class="section-title">
                  <div><h3>核对申请与材料</h3></div>
                  <span>{{ selectedTask.missing_fields?.length ? `${selectedTask.missing_fields.length} 项待补充` : '信息已齐全，待确认' }}</span>
                </div>
                <p class="input-confirmation-note">请核对下方信息，并按需上传任务材料。确认提交后，系统才会检索制度并进行审查。</p>
                <div class="dynamic-form">
                  <template v-for="(property, name) in orderedProperties" :key="name">
                    <div v-if="name === 'items'" class="form-field form-field-wide">
                      <label :class="{ required: isMissing(name) }">{{ property.title || '采购明细' }}</label>
                      <div v-for="(item, index) in inputForm.items" :key="index" class="item-editor">
                        <div class="item-cell item-name"><label :for="`item-name-${index}`">物品或服务</label><el-input :id="`item-name-${index}`" v-model="item.name" placeholder="填写名称" /></div>
                        <div class="item-cell"><label :for="`item-quantity-${index}`">数量</label><el-input-number :id="`item-quantity-${index}`" v-model="item.quantity" :min="1" :max="100000" controls-position="right" /></div>
                        <div class="item-cell"><label :for="`item-price-${index}`">单价（元）</label><el-input-number :id="`item-price-${index}`" v-model="item.unit_price" :min="0" :precision="2" placeholder="未填写" controls-position="right" /></div>
                        <div class="item-cell subtotal"><label>小计（元）</label><strong>{{ money(calculateAmount([item])) }}</strong></div>
                        <div class="item-actions"><button class="icon-button" type="button" title="重置当前明细" :aria-label="`重置第 ${index + 1} 行明细`" @click="resetItem(index)"><el-icon><RefreshLeft /></el-icon></button>
                        <button v-if="inputForm.items.length > 1" class="text-action" type="button" @click="removeItem(index)">移除该行</button></div>
                      </div>
                      <el-button text @click="addItem"><el-icon><Plus /></el-icon>添加明细</el-button>
                    </div>
                    <div v-else class="form-field" :class="{ 'form-field-wide': fieldType(property) === 'string' && ['purpose', 'business_reason', 'resource'].includes(name) }">
                      <label :for="`agent-field-${name}`" :class="{ required: isMissing(name) }">{{ fieldLabel(name) }}</label>
                      <template v-if="name === 'estimated_amount'">
                        <el-input-number :id="`agent-field-${name}`" :model-value="inputForm[name]" :min="0" :precision="2" controls-position="right" @update:model-value="setManualAmount" />
                        <div class="amount-note"><span>{{ amountMode === 'manual' ? '手动金额' : '明细合计' }} · {{ amountTotal === null ? '明细价格未完整填写' : `${money(amountTotal)} 元` }}</span><button class="text-action" type="button" :disabled="amountTotal === null" @click="restoreAmount">恢复明细合计</button></div>
                      </template>
                      <el-select v-else-if="fieldEnum(property).length" :id="`agent-field-${name}`" v-model="inputForm[name]" placeholder="请选择">
                        <el-option v-for="option in fieldEnum(property)" :key="option" :label="enumLabel(option)" :value="option" />
                      </el-select>
                      <span v-else-if="property.readOnly" class="readonly-state">提交后根据已解析的任务材料核对</span>
                      <el-switch v-else-if="fieldType(property) === 'boolean'" :id="`agent-field-${name}`" v-model="inputForm[name]" />
                      <el-input-number v-else-if="['integer', 'number'].includes(fieldType(property))" :id="`agent-field-${name}`" v-model="inputForm[name]" :min="fieldBound(property, 'minimum')" :max="fieldBound(property, 'maximum')" :precision="fieldType(property) === 'integer' ? 0 : undefined" controls-position="right" />
                      <el-input v-else :id="`agent-field-${name}`" v-model="inputForm[name]" :type="['purpose', 'business_reason'].includes(name) ? 'textarea' : 'text'" :rows="3" />
                    </div>
                  </template>
                </div>
                <div class="section-actions">
                  <el-button type="primary" :loading="submittingInput" :disabled="uploadingAttachment" @click="submitInput">确认并提交审查</el-button>
                </div>
              </section>

              <section class="work-section">
                <div class="section-title"><div><h3>申请摘要</h3></div></div>
                <dl class="request-grid">
                  <template v-for="(value, key) in visibleRequest" :key="key">
                    <div><dt>{{ fieldLabel(key) }}</dt><dd>{{ formatValue(value) }}</dd></div>
                  </template>
                  <div v-if="!Object.keys(selectedTask.request || {}).length" class="empty-line">等待字段抽取</div>
                </dl>
              </section>

              <section class="work-section">
                <div class="section-title"><div><h3>制度证据</h3></div><span>{{ selectedTask.evidence?.length || 0 }} 条</span></div>
                <ol v-if="selectedTask.evidence?.length" class="evidence-list">
                  <li v-for="(item, index) in selectedTask.evidence" :key="`${item.source}-${index}`">
                    <span>{{ String(index + 1).padStart(2, '0') }}</span>
                    <div>
                      <strong>{{ item.source || '任务附件' }}<em v-if="item.clause_id">{{ item.clause_id }}</em></strong>
                      <small>{{ item.section || (item.source_type === 'task_attachment' ? '任务附件' : '制度条款') }}</small>
                      <details><summary>查看证据原文</summary><p>{{ item.text }}</p></details>
                    </div>
                  </li>
                </ol>
                <div v-else class="empty-line">等待制度检索</div>
              </section>

              <section class="work-section split-section">
                <div>
                  <div class="section-title"><div><h3>执行计划</h3></div></div>
                  <p v-if="selectedTask.approval_context?.plan_outdated" class="approval-required">历史计划需要退回并重新审查，不能直接批准。</p>
                  <p v-if="!selectedTask.plan?.summary" class="empty-line">核对申请与材料并提交审查后，系统会检索制度、生成办理计划。</p>
                  <template v-else>
                  <p class="plan-summary">{{ planView.verifiedContract ? '本次只办理申请登记，具体顺序如下。' : '历史计划需要重新审查后，才能确认如何办理。' }}</p>
                  <ol v-if="planView.steps.length" class="plan-sequence">
                    <li v-for="(step, index) in planView.steps" :key="step.title"><span>{{ index + 1 }}</span><div><strong>{{ step.title }}</strong><p>{{ step.detail }}</p></div></li>
                  </ol>
                  <ul class="action-list">
                    <li v-for="action in selectedTask.plan?.actions || []" :key="action.action_type">
                      <el-icon :class="{ 'action-completed': selectedTask.status === 'completed' }"><CircleCheckFilled v-if="selectedTask.status === 'completed'" /><Document v-else /></el-icon><span>{{ actionLabel(action.action_type) }}</span>
                    </li>
                  </ul>
                  <p v-if="planView.outcome" class="plan-outcome"><strong>办理后得到什么</strong><span>{{ planView.outcome }}</span></p>
                  <p class="scope-note"><strong>办理范围</strong>{{ planView.boundary }}</p>
                  <div v-if="planView.policyChecks.length" class="policy-decisions">
                    <h4>本次申请的制度核对</h4>
                    <ul class="audit-highlights"><li v-for="(check, index) in planView.policyChecks" :key="index" :class="{ 'policy-blocked': check.status === 'blocked' }"><strong>{{ check.label }}<span v-if="check.clause_id"> · {{ check.clause_id }}</span></strong><p>{{ check.explanation }}</p></li></ul>
                    <p class="inspector-note">这里只核对可明确解析的条件，不代表全部制度要求已验证或业务审批已完成。</p>
                  </div>
                  <details v-if="executionContract.policy_requirements?.length" class="explanation-details">
                    <summary>制度要求待核验（{{ executionContract.policy_requirements.length }} 项）</summary>
                    <p class="inspector-note">以下是候选制度原文，条目数不是本次适用规则数。不匹配金额、业务对象或触发条件的条款不能套用；业务完成情况仍需核验。</p>
                    <ul class="audit-highlights policy-check-list"><li v-for="(requirement, index) in executionContract.policy_requirements" :key="index"><strong>{{ requirement.source }}<span v-if="requirement.clause_id"> · {{ requirement.clause_id }}</span></strong><p>{{ requirement.quote }}</p><small>尚未核验</small></li></ul>
                  </details>
                  <details class="explanation-details plan-explanation"><summary>查看完整计划与依据</summary>
                    <h4>计划说明</h4><p v-for="(paragraph, index) in planSummaryParagraphs" :key="index">{{ paragraph }}</p>
                    <h4 v-if="planNotes.length">本次申请的处理说明</h4>
                    <ol class="explanation-list"><li v-for="(item, index) in planNotes" :key="index"><h5>{{ item.title }}</h5><p v-for="(paragraph, part) in item.paragraphs" :key="part">{{ paragraph }}</p></li></ol>
                    <p v-if="!planNotes.length" class="inspector-note">这份计划没有单独记录场景说明。请结合上方办理顺序和制度原文核对。</p>
                    <details class="original-explanation"><summary>核对原始计划说明</summary><p>{{ selectedTask.plan.summary }}</p><p v-for="(note, index) in selectedTask.plan.review_notes || []" :key="index">{{ note }}</p></details>
                  </details>
                  </template>
                </div>
                <div>
                  <div class="section-title"><div><h3>合规复核</h3></div></div>
                  <div v-if="Object.keys(selectedTask.audit || {}).length" class="audit-verdict" :class="auditPassed ? 'passed' : 'blocked'">
                    <strong class="verdict-label"><el-icon><CircleCheckFilled v-if="auditPassed" /><WarningFilled v-else /></el-icon>{{ auditPassed ? '内部登记方案复核通过' : hasPolicyConflict ? '制度核对未通过，请修改后重提' : needsManualReview(selectedTask) ? '自动修订未通过，需人工处理' : isRunning ? '正在修订登记方案' : '复核未通过，请补充后重新审查' }}</strong>
                    <p class="review-next"><strong>接下来</strong>{{ auditNextStep }}</p>
                    <div v-if="blockingNotes.length" class="blocking-issues"><strong>执行前必须解决 · {{ blockingNotes.length }} 项</strong><ul><li v-for="(item, index) in blockingNotes.slice(0, 3)" :key="index"><strong>{{ item.title }}</strong><p>{{ explanationPreview(item) }}</p></li></ul><p v-if="blockingNotes.length > 3">其余 {{ blockingNotes.length - 3 }} 项见完整复核意见。</p></div>
                    <div v-if="auditNotes.length" class="review-overview"><h4>复核说明与建议</h4><ul class="audit-highlights"><li v-for="(item, index) in auditNotes.slice(0, 2)" :key="index"><strong>{{ item.title }}</strong><p>{{ explanationPreview(item) }}</p></li></ul></div>
                    <details v-if="auditNotes.length || blockingNotes.length" class="explanation-details audit-explanation"><summary>查看完整复核意见</summary>
                      <h4 v-if="blockingNotes.length" class="blocking-title">需要处理的问题</h4>
                      <ol class="explanation-list blocking-list"><li v-for="(item, index) in blockingNotes" :key="index"><h5>{{ item.title }}</h5><p v-for="(paragraph, part) in item.paragraphs" :key="part">{{ paragraph }}</p></li></ol>
                      <h4 v-if="auditNotes.length">判断依据与其他说明</h4>
                      <ol class="explanation-list"><li v-for="(item, index) in auditNotes" :key="index"><h5>{{ item.title }}</h5><p v-for="(paragraph, part) in item.paragraphs" :key="part">{{ paragraph }}</p></li></ol>
                      <details class="original-explanation"><summary>核对原始复核意见</summary><h5 v-if="blockingNotes.length">待处理事项原文</h5><p v-for="(reason, index) in selectedTask.audit.required_changes || []" :key="`change-${index}`">{{ reason }}</p><h5 v-if="auditNotes.length">其他意见原文</h5><p v-for="(reason, index) in selectedTask.audit.reasons || []" :key="`reason-${index}`">{{ reason }}</p></details>
                    </details>
                    <p class="scope-note"><strong>复核范围</strong>复核只判断申请登记方案。通过后仍要按要求审批，不代表已经完成采购、授权或其他业务检查。</p>
                  </div>
                  <div v-else class="empty-line">等待审计 Agent 复核</div>
                </div>
              </section>

              <section v-if="Object.keys(selectedTask.result || {}).length" class="work-section result-section">
                <div class="result-mark"><el-icon><CircleCheckFilled /></el-icon></div>
                <div><h3>内部登记记录已生成</h3><span>{{ resultSummary }}</span><p class="inspector-note">{{ registrationNotice(selectedTask.result) }}</p></div>
              </section>
            </div>

            <aside class="task-inspector">
              <section class="inspector-section">
                <div class="section-title compact"><div><h3>风险与审批</h3></div></div>
                <div class="risk-display" :class="selectedTask.risk_level || 'unknown'">
                  <strong>{{ selectedTask.risk_level ? riskLabel(selectedTask.risk_level) : '待计算' }}</strong>
                  <span>{{ selectedTask.risk_level ? '风险等级' : '等待规则结果' }}</span>
                </div>
                <p v-for="reason in selectedTask.risk?.reasons || []" :key="reason" class="inspector-note">{{ localizeText(reason) }}</p>
                <p v-if="selectedTask.risk_level && !Object.keys(selectedTask.risk || {}).length" class="inspector-note">历史风险详情未记录</p>
                <strong v-if="selectedTask.approval?.status === 'approved'" class="approval-approved">已批准内部登记</strong>
                <strong v-else-if="selectedTask.status === 'waiting_approval'" class="approval-required">{{ needsManualReview(selectedTask) ? '需管理员退回补充或拒绝' : '需要企业管理员审批' }}</strong>
              </section>

              <section class="inspector-section">
                <div class="section-title compact"><div><h3>任务材料</h3></div><span>{{ selectedTask.attachments?.length || 0 }}</span></div>
                <p class="inspector-note material-description">报价单、权限范围说明等补充依据，提交前可上传。材料仅用于本任务审查，不会自动进入共享知识库。</p>
                <input ref="attachmentInput" class="hidden-file" type="file" accept=".txt,.pdf,.docx,.csv,.xlsx,.xls" :disabled="uploadingAttachment || submittingInput || !canUpload" @change="uploadAttachment" />
                <el-button class="upload-control" :disabled="!canUpload || submittingInput" :loading="uploadingAttachment" @click="attachmentInput?.click()"><el-icon><Upload /></el-icon>{{ uploadingAttachment ? '上传并解析中' : '上传材料' }}</el-button>
                <p v-if="!canUpload" class="inspector-note upload-reason">{{ uploadReason }}</p>
                <div class="attachment-list">
                  <div v-for="item in selectedTask.attachments || []" :key="item.id">
                    <el-icon><Document /></el-icon>
                    <span><strong>{{ item.filename }}</strong><small>{{ item.status === 'done' ? '已解析' : '解析失败' }} · {{ formatBytes(item.file_size) }}</small></span>
                    <p v-if="item.failure_reason" class="attachment-error">{{ localizeText(item.failure_reason) }}</p>
                  </div>
                </div>
              </section>

              <section class="inspector-section trace-section">
                <div class="section-title compact"><div><h3>运行轨迹</h3></div><span>{{ selectedTask.steps?.length || 0 }}</span></div>
                <ol class="trace-list">
                  <li v-for="step in selectedTask.steps || []" :key="step.id" :class="step.status">
                    <span></span>
                    <div><strong>{{ nodeLabel(step.node_name) }}</strong><small>第 {{ step.attempt }} 次 · {{ step.duration_ms }}ms · {{ stepLabel(step.status) }}<template v-if="step.input_tokens || step.output_tokens"> · {{ step.input_tokens + step.output_tokens }} tokens</template></small><small v-if="step.output_summary" class="step-summary">{{ localizeText(step.output_summary) }}</small><small v-if="step.error_message" class="attachment-error">{{ localizeText(step.error_message) }}</small></div>
                  </li>
                </ol>
              </section>

              <section v-if="canCancel || selectedTask.status === 'failed'" class="inspector-actions">
                <el-button v-if="selectedTask.status === 'failed'" type="primary" :loading="retrying" @click="retryTask">从安全节点重试</el-button>
                <el-button v-if="canCancel" type="danger" plain :loading="cancelling" @click="cancelTask">中断任务</el-button>
                <details v-if="technicalDetails" class="technical-details"><summary>技术详情</summary><pre>{{ technicalDetails }}</pre></details>
              </section>
            </aside>
          </div>
        </template>
      </div>

      <div v-else-if="activeView === 'approvals'" class="stage-scroll">
        <section class="list-surface approval-surface" aria-label="企业审批台账">
          <div class="list-header approval-header"><h2>审批记录 <small>{{ approvalTotal ?? '-' }} 条</small></h2><span class="approval-pending-count">待处理 <strong>{{ approvalCount ?? '-' }}</strong></span><el-tooltip content="重置筛选" placement="top"><el-button aria-label="重置审批筛选" :disabled="approvalsLoading" @click="resetApprovalFilters"><el-icon><RefreshLeft /></el-icon></el-button></el-tooltip><el-button :loading="approvalsLoading" @click="loadApprovalHistory()"><el-icon><Refresh /></el-icon>刷新</el-button></div>
          <form class="approval-toolbar" @submit.prevent="changeApprovalFilters">
            <el-input v-model="approvalSearch" class="approval-search" aria-label="搜索审批记录" placeholder="搜索申请人、用户名、任务编号或内容" clearable :maxlength="200"><template #prefix><el-icon><Search /></el-icon></template></el-input>
            <label>审批状态<el-select v-model="approvalStatusFilter" aria-label="筛选审批状态"><el-option v-for="(label, value) in approvalStatuses" :key="value" :label="label" :value="value" /></el-select></label>
            <label>任务类型<el-select v-model="approvalSkillFilter" aria-label="筛选任务类型"><el-option label="全部类型" value="all" /><el-option label="采购申请" value="procurement" /><el-option label="系统权限申请" value="access_request" /></el-select></label>
            <label><span><el-icon><Sort /></el-icon>排序</span><el-select v-model="approvalSort" aria-label="审批记录排序"><el-option label="待处理优先" value="pending_first" /><el-option label="申请时间从新到旧" value="newest" /><el-option label="申请时间从旧到新" value="oldest" /></el-select></label>
          </form>
          <el-alert v-if="approvalError" :title="approvalError" type="error" :closable="false" show-icon />
          <div v-loading="approvalsLoading" class="approval-results" :aria-busy="approvalsLoading">
            <div v-if="approvals.length" class="approval-list">
              <article v-for="approval in approvals" :key="approval.id" :data-approval-id="approval.id" :class="{ 'approval-pending': approval.status === 'pending' }">
                <div class="approval-content">
                  <div class="approval-row-heading">
                    <span class="task-kind" :class="approval.task?.skill_id"><el-icon><ShoppingCart v-if="approval.task?.skill_id === 'procurement'" /><Key v-else /></el-icon></span>
                    <span class="approval-id">#{{ approval.task_id }}</span><span>{{ skillName(approval.task?.skill_id) }}</span>
                    <span class="approval-state" :class="approval.status">{{ approvalStateLabel(approval) }}</span>
                    <span v-if="approval.task?.risk_level" class="risk-badge" :class="approval.task.risk_level">{{ riskLabel(approval.task.risk_level) }}风险</span>
                  </div>
                  <h3 class="approval-title" :title="approval.task?.goal">{{ approvalTitle(approval) }}</h3>
                  <p class="approval-facts">{{ approvalFacts(approval) }}</p>
                  <div class="approval-metadata"><span>申请人：<strong>{{ approval.applicant?.display_name || '姓名未记录' }}</strong><span v-if="approval.applicant?.username" class="applicant-username">（{{ approval.applicant?.username }}）</span></span><span>申请时间：<time :datetime="approval.applied_at">{{ formatApprovalTime(approval.applied_at) }}</time></span></div>
                  <p class="approval-progress">任务进度：{{ approval.task?.deleted_at ? '已从任务列表删除' : taskStatusLabel(approval.task || {}) }}<span v-if="approval.decided_at"> · 处理时间：{{ formatApprovalTime(approval.decided_at) }}</span></p>
                  <p v-if="approval.comment" class="approval-comment">审批意见：{{ approval.comment }}</p>
                </div>
                <div class="approval-actions">
                  <el-tooltip :disabled="!approval.task?.deleted_at" content="申请人已删除任务，审批历史仍保留" placement="top"><span><el-button :disabled="!!approval.task?.deleted_at" @click="inspectApproval(approval)"><el-icon><Document /></el-icon>查看申请依据</el-button></span></el-tooltip>
                  <template v-if="approval.status === 'pending'">
                    <el-button :disabled="approvalsLoading || decidingApproval !== null || !approval.task?.approval_context?.can_decide" @click="decideApproval(approval, 'changes_requested')"><el-icon><RefreshLeft /></el-icon>退回</el-button>
                    <el-button :disabled="approvalsLoading || decidingApproval !== null || !approval.task?.approval_context?.can_decide" type="danger" plain @click="decideApproval(approval, 'rejected')"><el-icon><Close /></el-icon>拒绝</el-button>
                    <el-button :loading="decidingApproval === approval.id" :disabled="approvalsLoading || !approval.task?.approval_context?.can_approve || (decidingApproval !== null && decidingApproval !== approval.id)" type="primary" @click="decideApproval(approval, 'approved')"><el-icon><Check /></el-icon>批准内部登记</el-button>
                    <p v-if="approvalDecisionReason(approval)" class="approval-required">{{ approvalDecisionReason(approval) }}</p>
                  </template>
                </div>
              </article>
            </div>
            <div v-else-if="!approvalsLoading && !approvalError" class="large-empty"><el-icon><Tickets /></el-icon><strong>{{ approvalSearch || approvalStatusFilter !== 'all' || approvalSkillFilter !== 'all' ? '没有符合条件的审批记录' : '暂无审批记录' }}</strong></div>
          </div>
          <el-pagination v-if="approvalTotal > approvalPageSize" v-model:current-page="approvalPage" :page-size="approvalPageSize" :total="approvalTotal" layout="prev, pager, next" :pager-count="5" :disabled="approvalsLoading" @current-change="loadApprovalHistory()" />
        </section>
      </div>

      <div v-else-if="activeView === 'settings'" class="stage-scroll settings-surface">
        <div class="settings-toolbar">
          <div><h2>企业执行配置</h2><span>{{ enabledSkills.length }} / {{ skills.length }} 项业务能力已启用</span></div>
          <el-button :loading="settingsLoading" @click="openSettings"><el-icon><Refresh /></el-icon>重新加载</el-button>
        </div>
        <el-alert v-if="settingsError" :title="settingsError" type="error" :closable="false" show-icon />
        <div v-loading="settingsLoading" class="settings-content">
        <section class="setting-band">
          <div class="setting-heading"><h3>业务 Skill</h3><span>本企业任务类型</span></div>
          <div class="skill-rows">
            <div v-for="skill in skills" :key="skill.id">
              <span class="task-kind" :class="skill.id"><el-icon><ShoppingCart v-if="skill.id === 'procurement'" /><Key v-else /></el-icon></span>
              <div><strong>{{ skill.name }} <span class="skill-version">v{{ skill.version }}</span></strong><small>{{ skill.description }}</small></div>
              <div class="skill-toggle"><el-switch v-model="skill.enabled" :loading="!!savingSkills[skill.id]" :disabled="settingsLoading || !!settingsError" :aria-label="`${skill.name}启用状态`" @change="saveSkill(skill)" /><small>{{ savingSkills[skill.id] ? '保存中' : skill.enabled ? '已启用' : '已停用' }}</small></div>
            </div>
          </div>
        </section>
        <section class="setting-band">
          <div class="setting-heading"><h3>风险策略</h3><span>确定性规则</span></div>
          <div class="policy-content">
          <div class="policy-boundary"><el-icon><InfoFilled /></el-icon><span><strong>风险配置不替代制度审批要求。</strong> 权限申请始终需要人工审批；期限阈值不是制度规定的有效期上限。</span></div>
          <fieldset class="policy-group" :disabled="settingsLoading || savingRiskPolicy || !!settingsError">
          <legend><el-icon><ShoppingCart /></el-icon>采购申请</legend>
          <div class="policy-editor">
            <div class="policy-field">
              <label for="procurement-risk-amount">高风险金额门槛（元）</label>
              <div><el-input-number :key="`amount-${settingsLoading || savingRiskPolicy || !!settingsError}`" id="procurement-risk-amount" v-model="riskPolicy.procurement_approval_amount" :disabled="settingsLoading || savingRiskPolicy || !!settingsError" :min="0" :max="1000000000" :step="1000" :precision="0" controls-position="right" /><span>达到门槛时标记高风险并进入审批</span></div>
            </div>
            <div class="policy-field switch-field">
              <label for="quotation-risk">缺报价材料需审批</label>
              <div><el-switch id="quotation-risk" v-model="riskPolicy.procurement_requires_quotation" :disabled="settingsLoading || savingRiskPolicy || !!settingsError" /><span>材料不足时增加人工审批要求</span></div>
            </div>
            <div class="policy-field switch-field">
              <label for="sensitive-risk">敏感数据触发高风险</label>
              <div><el-switch id="sensitive-risk" v-model="riskPolicy.procurement_sensitive_data_requires_approval" :disabled="settingsLoading || savingRiskPolicy || !!settingsError" /><span>涉及敏感数据时进入高风险审批</span></div>
            </div>
          </div>
          </fieldset>
          <fieldset class="policy-group" :disabled="settingsLoading || savingRiskPolicy || !!settingsError">
          <legend><el-icon><Key /></el-icon>系统权限申请</legend>
          <div class="policy-editor">
            <div class="policy-field">
              <label for="classification-risk">高风险数据密级</label>
              <el-select id="classification-risk" v-model="riskPolicy.access_high_risk_classification" :disabled="settingsLoading || savingRiskPolicy || !!settingsError" aria-label="高风险数据密级">
                <el-option label="内部" value="internal" />
                <el-option label="秘密" value="secret" />
                <el-option label="机密" value="confidential" />
              </el-select>
            </div>
            <div class="policy-field">
              <label for="duration-risk">高风险期限阈值（天）</label>
              <div><el-input-number :key="`duration-${settingsLoading || savingRiskPolicy || !!settingsError}`" id="duration-risk" v-model="riskPolicy.access_max_duration_days" :disabled="settingsLoading || savingRiskPolicy || !!settingsError" :min="1" :max="365" :precision="0" controls-position="right" /><span>超过阈值时标记高风险，仍需核对制度期限</span></div>
            </div>
          </div>
          </fieldset>
          <div class="policy-actions"><span>{{ riskPolicyDirty ? '有未保存的风险策略修改' : '风险策略与已加载配置一致' }}</span><el-button type="primary" :loading="savingRiskPolicy" :disabled="!riskPolicyDirty || settingsLoading || !!settingsError" @click="saveRiskPolicy"><el-icon><Check /></el-icon>保存风险策略</el-button></div>
          </div>
        </section>
        </div>
      </div>

      <div v-else class="stage-scroll evaluation-surface">
        <section class="evaluation-command">
          <div><h2>Agent 组件契约评测</h2><span class="scope-label">组件验证 · 非完整业务验收</span></div>
          <div class="evaluation-summary">
            <span><strong>2</strong>业务 Skill</span>
            <span><strong>5</strong>评测维度</span>
            <span><strong>24</strong>基线场景</span>
          </div>
          <el-button type="primary" :loading="evaluationRunning" :disabled="evaluationBusy || !health?.agent_available" @click="runEvaluation">
            <el-icon><VideoPlay /></el-icon>运行 Agent 评测
          </el-button>
        </section>
        <div class="evaluation-boundary"><el-icon><InfoFilled /></el-icon><span>字段抽取使用企业配置的模型，可能产生 API 费用。结果为组件断言通过率，不代表采购完成、权限授予或真实故障恢复。</span></div>
        <el-alert v-if="evaluationError" :title="evaluationError" type="error" :closable="false" show-icon />
        <div class="evaluation-layout">
        <section class="evaluation-runs">
          <div class="list-header"><span>运行历史 <small>{{ evaluationRuns.length }}</small></span><el-button text :loading="evaluationsLoading" aria-label="刷新评测记录" @click="refreshEvaluations"><el-icon><Refresh /></el-icon></el-button></div>
          <div v-if="!evaluationRuns.length" class="evaluation-empty"><el-icon><Document /></el-icon><strong>{{ evaluationError ? '记录暂不可用' : evaluationsLoading ? '正在加载记录' : '尚无评测记录' }}</strong></div>
          <button v-for="run in evaluationRuns" :key="run.id" type="button" class="evaluation-run" :class="{ selected: selectedEvaluation?.id === run.id }" @click="selectEvaluation(run.id)">
            <div><strong>评测运行 #{{ run.id }}</strong><small>{{ formatDate(run.created_at) }}</small></div>
            <span class="status-badge" :class="run.status">{{ statusLabel(run.status) }}</span>
            <div class="score"><strong>{{ run.status === 'completed' && run.total_cases ? Math.round(run.passed_cases / run.total_cases * 100) + '%' : '-' }}</strong><small>{{ run.passed_cases }}/{{ run.total_cases }}</small></div>
          </button>
        </section>
        <section v-if="selectedEvaluation" v-loading="evaluationDetailLoading" class="evaluation-detail">
          <div class="section-title compact">
            <div><h3>运行 #{{ selectedEvaluation.id }} 诊断</h3></div>
            <span>{{ statusLabel(selectedEvaluation.status) }}</span>
          </div>
          <div class="evaluation-version">{{ selectedEvaluation.metrics?.suite_version ? `评测版本：${selectedEvaluation.metrics.suite_version}` : ['pending', 'running'].includes(selectedEvaluation.status) ? '评测版本随结果保存' : '历史评测：未记录版本，不能视为通过新版全部断言' }}</div>
          <el-alert v-if="selectedEvaluation.status === 'failed'" :title="localizeText(selectedEvaluation.metrics?.error || '评测运行失败，请检查执行引擎与模型服务')" type="error" :closable="false" show-icon />
          <div v-if="['pending', 'running'].includes(selectedEvaluation.status)" class="evaluation-progress"><el-icon class="is-loading"><Loading /></el-icon>{{ selectedEvaluation.status === 'pending' ? '等待评测队列' : '正在运行组件评测' }}</div>
          <div class="metric-line">
            <div><span>Skill 路由</span><strong>{{ percentMetric(selectedEvaluation.metrics?.routing_accuracy) }}</strong></div>
            <div><span>字段抽取</span><strong>{{ percentMetric(selectedEvaluation.metrics?.field_extraction_accuracy ?? selectedEvaluation.metrics?.field_validation_pass_rate) }}</strong></div>
            <div><span>风险门控</span><strong>{{ percentMetric(selectedEvaluation.metrics?.risk_gate_pass_rate) }}</strong></div>
            <div><span>工具权限</span><strong>{{ percentMetric(selectedEvaluation.metrics?.security_pass_rate) }}</strong></div>
            <div><span>状态转换约束</span><strong>{{ percentMetric(selectedEvaluation.metrics?.recovery_pass_rate) }}</strong></div>
          </div>
          <div class="evaluation-cases">
            <div v-for="item in selectedEvaluation.cases || []" :key="item.case_id" class="evaluation-case">
              <code>{{ item.case_id }}</code>
              <div><strong>{{ item.diagnostics?.name }}</strong><small>{{ categoryLabel(item.diagnostics?.category) }} · {{ skillName(item.skill_id) }}</small><details class="case-diagnostics"><summary>{{ item.passed ? '查看用例与判定依据' : '查看诊断' }}</summary><div v-if="item.diagnostics?.input" class="case-input"><strong>测试输入</strong><p v-if="item.diagnostics.input.goal">{{ item.diagnostics.input.goal }}</p><div v-else>{{ diagnosticValue(item.diagnostics.input) }}</div></div><dl><div v-for="(check, index) in item.diagnostics?.assertions || []" :key="index"><dt>{{ diagnosticLabel(check.field) }} · {{ check.passed ? '通过' : '不符合预期' }}</dt><dd>预期：{{ diagnosticValue(check.expected) }}</dd><dd>实际：{{ diagnosticValue(check.actual) }}</dd></div></dl><details><summary>原始技术诊断</summary><pre>{{ JSON.stringify(item.diagnostics, null, 2) }}</pre></details></details></div>
              <span class="case-result" :class="{ passed: item.passed }">{{ item.passed ? '通过' : '失败' }}</span>
            </div>
          </div>
        </section>
        <section v-else class="evaluation-detail evaluation-empty"><el-icon><DataAnalysis /></el-icon><h3>暂无评测结果</h3><span>2 项业务 Skill · 24 个组件场景</span></section>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { calculateAmount, localizeText, approvalMessage, nodeLabel, fieldLabels, conciseText, explanationItems, planPresentation, isAuditPassed, needsManualReview, canReviewAgent, registrationNotice } from './agentPresentation.mjs'
import {
  cancelAgentTask,
  createAgentEvaluationRun,
  createAgentTask,
  decideAgentApproval,
  deleteAgentTask,
  getAgentArtifact,
  getAgentEvaluationRun,
  getAgentHealth,
  getAgentTask,
  getAgentRiskPolicy,
  listAgentApprovals,
  listAgentEvaluationRuns,
  listAgentSkills,
  listAgentTasks,
  retryAgentTask,
  streamAgentTaskEvents,
  submitAgentTaskInput,
  updateAgentRiskPolicy,
  updateAgentSkill,
  uploadAgentTaskAttachment,
  withdrawAgentTask,
} from '../api/api.js'

const props = defineProps({
  currentUser: { type: Object, default: () => ({}) },
  knowledgeBases: { type: Array, default: () => [] },
  defaultKbIds: { type: Array, default: () => [] },
})
defineEmits(['back-to-home'])

const activeView = ref('tasks')
const tasks = ref([])
const selectedTask = ref(null)
const skills = ref([])
const approvals = ref([])
const approvalsLoading = ref(false)
const approvalError = ref('')
const approvalSearch = ref('')
const approvalStatusFilter = ref('all')
const approvalSkillFilter = ref('all')
const approvalSort = ref('pending_first')
const approvalPage = ref(1)
const approvalPageSize = 20
const approvalTotal = ref(0)
const approvalVersionError = '审批接口版本不匹配，请刷新整个页面；若仍异常，请更新后端服务。'
const approvalStatuses = { all: '全部状态', pending: '待处理', approved: '已批准', changes_requested: '已退回', rejected: '已拒绝', cancelled: '已取消', superseded: '已被新申请替代' }
let approvalHistoryRequest = 0
let approvalSearchTimer = null
const approvalCount = ref(null)
let approvalCountRequest = 0
const evaluationRuns = ref([])
const selectedEvaluation = ref(null)
const health = ref(null)
const tasksLoading = ref(false)
const creating = ref(false)
const submittingInput = ref(false)
const uploadingAttachment = ref(false)
const evaluationRunning = ref(false)
const settingsLoading = ref(false)
const settingsError = ref('')
const savingSkills = reactive({})
const savingRiskPolicy = ref(false)
const riskPolicySnapshot = ref('')
const evaluationsLoading = ref(false)
const evaluationDetailLoading = ref(false)
const evaluationError = ref('')
let evaluationSelectionEpoch = 0
let evaluationsRequest = 0
const refreshing = ref(false)
const retrying = ref(false)
const cancelling = ref(false)
const withdrawing = ref(false)
const deleting = ref(false)
const decidingApproval = ref(null)
const attachmentInput = ref(null)
const lastRefreshed = ref('')
const amountMode = ref('auto')
const formSnapshot = ref('{}')
let syncingForm = false
let selectionEpoch = 0
let taskListRequest = 0
let detailRequest = null
let polling = false
const newGoal = ref('')
const newKbIds = ref([...props.defaultKbIds])
const inputForm = reactive({})
const riskPolicy = reactive({
  procurement_approval_amount: 50000,
  procurement_requires_quotation: true,
  procurement_sensitive_data_requires_approval: true,
  access_high_risk_classification: 'secret',
  access_max_duration_days: 30,
})
let pollTimer = null
let eventAbort = null
let eventReconnectTimer = null
let eventRefreshTimer = null

const isEnterpriseAdmin = computed(() => props.currentUser?.role === 'enterprise_admin')
const isAgentApprover = computed(() => canReviewAgent(props.currentUser || {}))
const executionContract = computed(() => selectedTask.value?.plan?.execution_contract || {})
const enabledSkills = computed(() => skills.value.filter((item) => item.enabled))
const riskPolicyDirty = computed(() => riskPolicySnapshot.value !== '' && JSON.stringify(riskPolicy) !== riskPolicySnapshot.value)
const evaluationBusy = computed(() => evaluationRuns.value.some(run => ['pending', 'running'].includes(run.status)))
const selectedSkill = computed(() => skills.value.find((item) => item.id === selectedTask.value?.skill_id))
const schemaProperties = computed(() => selectedSkill.value?.input_schema?.properties || {})
const orderedProperties = computed(() => {
  const order = selectedTask.value?.skill_id === 'procurement'
    ? ['purpose', 'items', 'estimated_amount', 'budget_code', 'vendor', 'expected_date', 'quotation_attached', 'involves_sensitive_data']
    : ['target_system', 'resource', 'permission_level', 'data_classification', 'duration_days', 'business_reason']
  return Object.fromEntries(order.filter(key => schemaProperties.value[key]).map(key => [key, schemaProperties.value[key]]))
})
const amountTotal = computed(() => calculateAmount(inputForm.items || []))
const inputDirty = computed(() => JSON.stringify(inputForm) !== formSnapshot.value)
watch(amountTotal, total => {
  if (!syncingForm && selectedTask.value?.skill_id === 'procurement' && amountMode.value === 'auto') inputForm.estimated_amount = total
}, { flush: 'sync' })
const visibleRequest = computed(() => Object.fromEntries(Object.entries(selectedTask.value?.request || {}).filter(([key]) => fieldLabels[key])))
const canUpload = computed(() => ['waiting_input', 'failed'].includes(selectedTask.value?.status) && !selectedTask.value?.result?.record_id && (selectedTask.value?.capabilities?.upload_attachment?.allowed ?? true))
const uploadReason = computed(() => {
  if (selectedTask.value?.status === 'waiting_approval') return '需企业管理员退回后补充材料'
  if (!['waiting_input', 'failed'].includes(selectedTask.value?.status)) return '当前任务状态不能上传材料'
  return selectedTask.value?.capabilities?.upload_attachment?.reason || '当前任务状态不能上传材料'
})
const isRunning = computed(() => ['running', 'executing', 'pending_dispatch', 'queued'].includes(selectedTask.value?.status))
const executionTitle = computed(() => {
  const task = selectedTask.value
  if (!task) return ''
  if (task.withdrawn_at && task.status === 'cancel_requested') return '正在撤回申请，等待后台安全停止'
  if (task.withdrawn_at && task.status === 'cancelled') return '申请已撤回'
  if (task.status === 'waiting_approval') return hasPolicyConflict.value ? '制度核对未通过，需退回修改' : needsManualReview(task) ? '自动修订未通过，需企业管理员处理' : '等待企业管理员审批'
  if (task.status === 'waiting_input') return task.missing_fields?.length ? '请补充信息并核对材料' : '申请信息已提取，请核对后提交'
  if (task.status === 'failed') return '本次执行失败，可从安全节点恢复'
  if (task.status === 'completed') return '内部登记已完成，记录已核验'
  if (['pending_dispatch', 'queued'].includes(task.status)) return '已提交，等待执行队列处理'
  if (isRunning.value) return `正在${nodeLabel(task.current_node)}`
  return statusLabel(task.status)
})
const executionDescription = computed(() => {
  const task = selectedTask.value
  if (task?.withdrawn_at && task.status === 'cancel_requested') return '撤回请求已保存，当前节点结束后停止；安全停止后可删除任务'
  if (task?.withdrawn_at && task.status === 'cancelled') return '待审批已失效，任务不会继续执行；可删除任务以整理列表，审计历史仍会保留'
  if (task?.status === 'waiting_approval') return approvalMessage(task)
  if (task?.status === 'waiting_input') return task.missing_fields?.length ? `${task.missing_fields.length} 项信息待补充；可上传材料后一起提交审查` : '信息已齐全，可按需上传材料；确认提交后才会开始制度检索和审查'
  if (isRunning.value) return task.execution?.attempt ? `当前节点第 ${task.execution.attempt} 次执行，结果将实时更新` : '申请已保存，审查结果将实时更新'
  if (task?.status === 'failed') return '已完成的步骤与失败记录已保留，重试不会重复创建正式记录'
  return task?.status === 'completed' ? registrationNotice(task.result) : '当前状态已保存'
})
const planView = computed(() => planPresentation(selectedTask.value || {}))
const planSummaryParagraphs = computed(() => explanationItems([selectedTask.value?.plan?.summary || ''])[0]?.paragraphs || [])
const planNotes = computed(() => explanationItems(selectedTask.value?.plan?.review_notes || [], '办理说明'))
const blockingNotes = computed(() => explanationItems(selectedTask.value?.audit?.required_changes || [], '待处理事项'))
const auditNotes = computed(() => explanationItems(selectedTask.value?.audit?.reasons || [], '复核说明'))
const auditPassed = computed(() => isAuditPassed(selectedTask.value?.audit || {}))
const hasPolicyConflict = computed(() => Boolean(selectedTask.value?.audit?.policy_blocking_issues?.length))
const auditNextStep = computed(() => {
  const task = selectedTask.value || {}
  if (['cancelled', 'cancel_requested'].includes(task.status)) return '这项申请已停止或正在停止，不会继续办理。已保存的复核意见仅供核对。'
  if (task.status === 'failed') return '本轮办理失败。请先查看失败原因；修复后可从安全节点恢复，复核通过不等于已经完成登记。'
  if (needsManualReview(task)) return '请企业管理员退回补充或拒绝。复核未通过前，不能直接批准内部登记。'
  if (!auditPassed.value) return isRunning.value ? '系统正在根据下面的问题修订计划，请等待本轮结果。' : '请先处理下面的问题，再重新提交审查。'
  if (task.status === 'completed') return '申请记录已保存，可查看结果并下载执行档案。实际业务仍需按制度另行办理。'
  return task.risk?.requires_approval || executionContract.value.requires_approval ? '等待本企业另一位管理员审批。批准前不会保存正式申请记录。' : '系统将在规则检查通过后保存申请，并生成执行档案。'
})
function explanationPreview(item) { return item.preview.length > 220 ? '涉及的条件和处理要求较多，请展开完整说明核对。' : item.preview }
const technicalDetails = computed(() => {
  const unknown = Object.fromEntries(Object.entries(selectedTask.value?.request || {}).filter(([key]) => !fieldLabels[key]))
  const actions = (selectedTask.value?.plan?.actions || []).map(action => Object.fromEntries(Object.entries(action.arguments || {}).filter(([key]) => !fieldLabels[key]))).filter(value => Object.keys(value).length)
  const modelDraft = selectedTask.value?.plan?.model_review_notes || []
  return Object.keys(unknown).length || actions.length || modelDraft.length ? JSON.stringify({ request: unknown, actions, model_review_notes: modelDraft }, null, 2) : ''
})
const isTaskOwner = computed(() => selectedTask.value && Number(selectedTask.value.user_id) === Number(props.currentUser?.id))
const canWithdraw = computed(() => isTaskOwner.value && selectedTask.value?.capabilities?.withdraw?.allowed === true)
const canDelete = computed(() => isTaskOwner.value && selectedTask.value?.capabilities?.delete?.allowed === true)
const deleteReason = computed(() => selectedTask.value?.capabilities?.delete?.reason || '')
const canCancel = computed(() => selectedTask.value && !isTaskOwner.value && !['completed', 'cancelled', 'cancel_requested', 'rejected'].includes(selectedTask.value.status))
const resultSummary = computed(() => {
  const result = selectedTask.value?.result || {}
  return `${localizeText(result.record_type || '业务')}记录 #${result.record_id || '-'} · ${result.status === 'completed' ? '已核验' : localizeText(result.status || '待核验')}`
})
const viewTitle = computed(() => ({
  tasks: selectedTask.value ? '任务执行详情' : '创建执行任务',
  approvals: '人工审批中心', settings: 'Skill 与风险规则', evaluation: 'Agent 评测',
}[activeView.value]))

const pipelineStages = [
  { id: 'extract_fields', label: '字段抽取', note: '业务 Agent' },
  { id: 'retrieve_policy', label: '制度检索', note: '只读 RAG' },
  { id: 'deterministic_checks', label: '规则校验', note: '确定性引擎' },
  { id: 'compliance_audit', label: '合规复核', note: '审计 Agent' },
  { id: 'wait_for_approval', label: '人工门控', note: '按风险触发' },
  { id: 'execute_tools', label: '受控执行', note: '幂等写入' },
]

onMounted(async () => {
  // 用 allSettled：某一个初始加载失败（例如 Skill 列表接口报错）不能把整个
  // 工作台的实时轮询一起带走 —— 那样页面看着是好的，但永远不会自己更新。
  await Promise.allSettled([
    loadSkills(),
    loadTasks(),
    loadHealth(),
    isAgentApprover.value ? loadApprovalRows({ silent: true }) : Promise.resolve(),
    isEnterpriseAdmin.value ? loadEvaluations() : Promise.resolve(),
  ])
  pollTimer = window.setInterval(pollWorkspace, 3000)
})

onUnmounted(() => {
  approvalHistoryRequest += 1
  window.clearTimeout(approvalSearchTimer)
  window.clearInterval(pollTimer)
  window.clearTimeout(eventRefreshTimer)
  window.clearTimeout(eventReconnectTimer)
  eventAbort?.abort()
})

watch(() => props.defaultKbIds, (ids) => {
  if (!newKbIds.value.length) newKbIds.value = [...ids]
})
watch(approvalSearch, () => {
  window.clearTimeout(approvalSearchTimer)
  approvalHistoryRequest += 1
  approvalSearchTimer = window.setTimeout(changeApprovalFilters, 300)
})
watch([approvalStatusFilter, approvalSkillFilter, approvalSort], changeApprovalFilters)

async function loadHealth() {
  try { health.value = await getAgentHealth({ silent: true }) } catch { health.value = { agent_available: false } }
}

async function loadSkills() {
  const data = await listAgentSkills()
  skills.value = data.items || []
}

async function loadTasks() {
  const request = ++taskListRequest
  tasksLoading.value = true
  try {
    const next = await listAgentTasks({ silent: true })
    if (request === taskListRequest) tasks.value = next
  } finally { if (request === taskListRequest) tasksLoading.value = false }
}

function beginNewTask() {
  selectionEpoch += 1
  eventAbort?.abort()
  window.clearTimeout(eventRefreshTimer)
  selectedTask.value = null
  newGoal.value = ''
  newKbIds.value = [...props.defaultKbIds]
}

async function createTask() {
  if (creating.value) return
  if (!newGoal.value.trim()) return ElMessage.warning('请填写任务目标')
  if (!newKbIds.value.length) return ElMessage.warning('请至少选择一个制度知识库')
  creating.value = true
  try {
    const task = await createAgentTask({ goal: newGoal.value.trim(), kb_ids: newKbIds.value })
    await loadTasks()
    await selectTask(task.id)
    ElMessage.success('申请已保存，请在提取完成后核对信息与材料')
  } finally { creating.value = false }
}

async function selectTask(id) {
  const epoch = ++selectionEpoch
  eventAbort?.abort()
  const task = await getAgentTask(id)
  if (epoch !== selectionEpoch) return
  selectedTask.value = task
  lastRefreshed.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  syncInputForm()
  connectTaskEvents(id)
}

async function refreshSelectedTask({ manual = false } = {}) {
  if (!selectedTask.value) return
  const id = selectedTask.value.id
  const epoch = selectionEpoch
  if (manual) refreshing.value = true
  try {
    if (!detailRequest || detailRequest.id !== id || detailRequest.epoch !== epoch) {
      const promise = getAgentTask(id, { silent: true })
      const entry = { id, epoch, promise }
      detailRequest = entry
      promise.finally(() => { if (detailRequest === entry) detailRequest = null }).catch(() => {})
    }
    const next = await detailRequest.promise
    if (epoch !== selectionEpoch || selectedTask.value?.id !== id) return
    const previousVersion = selectedTask.value.version
    if (Number(next.version) < Number(previousVersion)) return
    selectedTask.value = next
    if (!inputDirty.value && previousVersion !== next.version) syncInputForm()
    lastRefreshed.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    await loadTasks()
    if (manual) ElMessage.success('已获取最新状态')
  } catch (error) {
    if (manual) ElMessage.error('刷新失败，已保留当前页面，请稍后重试')
    else throw error
  } finally { if (manual) refreshing.value = false }
}

async function refreshTaskList() {
  await loadTasks()
  ElMessage.success('任务列表已刷新')
}

async function pollWorkspace() {
  await loadHealth()
  if (polling) return
  polling = true
  try {
  if (isAgentApprover.value) {
    try { await loadApprovalRows({ silent: true }) } catch { /* 保留上次数量，下次轮询继续 */ }
    if (activeView.value === 'approvals' && !approvalsLoading.value && !approvalSearchTimer) await loadApprovalHistory({ silent: true })
  }
  if (activeView.value === 'evaluation' && isEnterpriseAdmin.value) {
    try {
      if (!await loadEvaluations()) return
      if (selectedEvaluation.value && ['pending', 'running'].includes(selectedEvaluation.value.status)) {
        await selectEvaluation(selectedEvaluation.value.id)
      }
    } catch { /* 下次轮询继续 */ }
    return
  }
  if (!selectedTask.value || ['completed', 'cancelled'].includes(selectedTask.value.status)) return
  try { await refreshSelectedTask() } catch { /* 下次轮询继续 */ }
  } finally { polling = false }
}

function connectTaskEvents(taskId) {
  eventAbort?.abort()
  window.clearTimeout(eventRefreshTimer)
  window.clearTimeout(eventReconnectTimer)
  // 控制器存局部变量：外层 eventAbort 会被下一次连接替换，回调里读它
  // 就分不清"这次连接"有没有被取消。
  const controller = new AbortController()
  eventAbort = controller
  // 记住最后收到的事件 id：重连时带给后端，从断点续传而不是整段重放。
  let lastEventId = '0-0'
  // 只有"连接自己结束"才重连；主动取消（切任务、离开页面）不重连。
  const scheduleReconnect = () => {
    if (controller.signal.aborted || selectedTask.value?.id !== taskId) return
    eventReconnectTimer = window.setTimeout(open, 2000)
  }
  const open = () => {
    streamAgentTaskEvents(taskId, {
      signal: controller.signal,
      lastEventId,
      onEvent: ({ id, type }) => {
        if (id) lastEventId = id
        if (type === 'degraded' || selectedTask.value?.id !== taskId) return
        if (type === 'task_deleted') {
          beginNewTask()
          loadTasks().catch(() => {})
          return
        }
        window.clearTimeout(eventRefreshTimer)
        eventRefreshTimer = window.setTimeout(() => refreshSelectedTask().catch(() => {}), 160)
      },
    }).then(() => {
      // 后端发完 degraded、或流结束后正常关闭响应 —— 这是 resolve 不是 reject。
      // 只挂 catch 的话，实时事件连接永远断在这里不恢复。
      scheduleReconnect()
    }).catch((error) => {
      if (error?.name === 'AbortError') return
      loadHealth()
      // 轮询仍在跑，断了也不会漏状态；重连只是把实时性拿回来。
      scheduleReconnect()
    })
  }
  open()
}

function syncInputForm() {
  syncingForm = true
  for (const key of Object.keys(inputForm)) delete inputForm[key]
  Object.assign(inputForm, JSON.parse(JSON.stringify(selectedTask.value?.request || {})))
  if (selectedTask.value?.skill_id === 'procurement' && !Array.isArray(inputForm.items)) inputForm.items = []
  if (selectedTask.value?.skill_id === 'procurement' && !inputForm.items.length) addItem()
  const total = calculateAmount(inputForm.items || [])
  const provided = inputForm.estimated_amount
  amountMode.value = provided != null && (total === null || Number(provided) !== total) ? 'manual' : 'auto'
  if (selectedTask.value?.skill_id === 'procurement' && amountMode.value === 'auto' && total !== null) inputForm.estimated_amount = total
  formSnapshot.value = JSON.stringify(inputForm)
  syncingForm = false
}

function addItem() { inputForm.items ||= []; inputForm.items.push({ name: '', quantity: 1, unit_price: null }) }
function removeItem(index) { inputForm.items.splice(index, 1); if (!inputForm.items.length) addItem() }
function resetItem(index) { inputForm.items[index] = { name: '', quantity: 1, unit_price: null } }
function setManualAmount(value) { amountMode.value = 'manual'; inputForm.estimated_amount = value }
function restoreAmount() { if (amountTotal.value === null) return; amountMode.value = 'auto'; inputForm.estimated_amount = amountTotal.value }
function money(value) { return value == null ? '—' : Number(value).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) }
function stepLabel(value) { return ({ running: '执行中', completed: '完成', failed: '失败', interrupted: '已中断' }[value] || '已记录') }

async function submitInput() {
  if (submittingInput.value || uploadingAttachment.value) return
  if (inputForm.items?.some(item => !item.name?.trim())) return ElMessage.warning('请填写每一行的物品或服务名称')
  const id = selectedTask.value.id
  submittingInput.value = true
  try {
    const updated = await submitAgentTaskInput(id, JSON.parse(JSON.stringify(inputForm)))
    if (selectedTask.value?.id !== id) return
    selectedTask.value = { ...selectedTask.value, ...updated, steps: selectedTask.value.steps }
    formSnapshot.value = JSON.stringify(inputForm)
    ElMessage.success('申请已提交，等待审查')
    connectTaskEvents(id)
    await loadTasks()
  } finally { submittingInput.value = false }
}

async function uploadAttachment(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (uploadingAttachment.value || submittingInput.value || !canUpload.value) return
  uploadingAttachment.value = true
  try {
    const item = await uploadAgentTaskAttachment(selectedTask.value.id, file)
    await refreshSelectedTask()
    if (item.status === 'done') ElMessage.success('材料已上传并解析')
    else ElMessage.warning('材料已上传，但解析失败，请查看材料列表中的原因')
  } finally { uploadingAttachment.value = false }
}

async function cancelTask() {
  if (cancelling.value) return
  const id = selectedTask.value.id
  try { await ElMessageBox.confirm('确认中断这个任务？已完成的步骤和审计记录会保留。', '中断任务', { type: 'warning' }) }
  catch (error) { if (error === 'cancel' || error === 'close') return; throw error }
  cancelling.value = true
  try {
    await cancelAgentTask(id)
    await refreshSelectedTask()
    ElMessage.success('中断请求已提交')
  } finally { cancelling.value = false }
}

async function withdrawTask() {
  if (withdrawing.value || deleting.value || !canWithdraw.value) return
  const id = selectedTask.value.id
  const epoch = selectionEpoch
  withdrawing.value = true
  try {
    await ElMessageBox.confirm('撤回后，待审批将失效，后台任务会安全停止。需要继续办理时请重新发起申请。', '撤回申请', { type: 'warning', confirmButtonText: '确认撤回', cancelButtonText: '取消' })
    const updated = await withdrawAgentTask(id)
    if (selectedTask.value?.id === id && epoch === selectionEpoch) {
      if (Number(updated.version) >= Number(selectedTask.value.version)) selectedTask.value = updated
    }
    ElMessage.success(updated.status === 'cancel_requested' ? '撤回请求已提交，等待后台安全停止' : '申请已撤回')
    await loadTasks()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close' && !error?.response) ElMessage.error('撤回失败，请刷新状态后重试')
  } finally { withdrawing.value = false }
}

async function deleteTask() {
  if (deleting.value || withdrawing.value || !canDelete.value) return
  const id = selectedTask.value.id
  const epoch = selectionEpoch
  deleting.value = true
  try {
    await ElMessageBox.confirm('确认从任务列表删除此任务？删除后不能继续办理，必要的审计历史与材料会保留，不会撤销任何正式业务记录。', '删除任务', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' })
    await deleteAgentTask(id)
    taskListRequest += 1
    tasks.value = tasks.value.filter(task => task.id !== id)
    if (selectedTask.value?.id === id && epoch === selectionEpoch) beginNewTask()
    ElMessage.success('任务已从列表删除')
    await loadTasks()
  } catch (error) {
    if (error !== 'cancel' && error !== 'close' && !error?.response) ElMessage.error('删除失败，请刷新状态后重试')
  } finally { deleting.value = false }
}

function taskStatusLabel(task) {
  if (task.withdrawn_at && task.status === 'cancel_requested') return '撤回中'
  if (task.withdrawn_at && task.status === 'cancelled') return '已撤回'
  if (task.status === 'waiting_input' && !task.missing_fields?.length) return '待确认'
  if (needsManualReview(task)) return '待人工处理'
  return statusLabel(task.status)
}

async function retryTask() {
  if (retrying.value) return
  const id = selectedTask.value.id
  retrying.value = true
  try {
    const updated = await retryAgentTask(id)
    if (selectedTask.value?.id !== id) return
    selectedTask.value = { ...selectedTask.value, ...updated, steps: selectedTask.value.steps }
    ElMessage.success('已提交恢复请求，等待执行队列处理')
    connectTaskEvents(id)
    await refreshSelectedTask()
  } finally { retrying.value = false }
}

async function downloadArtifact() {
  const text = await getAgentArtifact(selectedTask.value.id)
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `agent-task-${selectedTask.value.id}.md`
  link.click()
  URL.revokeObjectURL(url)
}

async function loadApprovalRows({ silent = false } = {}) {
  const request = ++approvalCountRequest
  const rows = await listAgentApprovals({ silent })
  if (request === approvalCountRequest) approvalCount.value = rows.length
  return rows
}

async function openApprovals() {
  activeView.value = 'approvals'
  await loadApprovalHistory()
}

function resetApprovalFilters() {
  approvalSearch.value = ''
  approvalStatusFilter.value = 'all'
  approvalSkillFilter.value = 'all'
  approvalSort.value = 'pending_first'
  changeApprovalFilters()
}

function changeApprovalFilters() {
  window.clearTimeout(approvalSearchTimer)
  approvalSearchTimer = null
  approvalPage.value = 1
  loadApprovalHistory()
}

async function loadApprovalHistory({ silent = false } = {}) {
  const request = ++approvalHistoryRequest
  const countRequest = ++approvalCountRequest
  if (!silent) approvalsLoading.value = true
  try {
    const data = await listAgentApprovals({ silent: true, params: { scope: 'all', q: approvalSearch.value.trim(), status: approvalStatusFilter.value, skill_id: approvalSkillFilter.value, sort: approvalSort.value, page: approvalPage.value, page_size: approvalPageSize } })
    if (request !== approvalHistoryRequest || activeView.value !== 'approvals') return
    if (!data || Array.isArray(data) || !Array.isArray(data.items) || !Number.isInteger(data.total) || data.total < 0 || !Number.isInteger(data.pending_count) || data.pending_count < 0) throw new Error(approvalVersionError)
    const lastPage = Math.max(1, Math.ceil(data.total / approvalPageSize))
    if (approvalPage.value > lastPage) { approvalPage.value = lastPage; return await loadApprovalHistory({ silent }) }
    approvals.value = data.items
    approvalTotal.value = data.total
    if (countRequest === approvalCountRequest) approvalCount.value = data.pending_count
    approvalError.value = ''
  } catch (error) {
    if (request === approvalHistoryRequest) approvalError.value = error.message === approvalVersionError ? approvalVersionError : '审批记录加载失败，已保留上次结果。请点击刷新重试。'
  } finally { if (request === approvalHistoryRequest) approvalsLoading.value = false }
}

function approvalStateLabel(approval) {
  return approval.status === 'pending' && approval.task?.approval_context?.manual_review_required ? '待人工处理' : approvalStatuses[approval.status] || '历史状态未记录'
}
function approvalDecisionReason(approval) {
  const context = approval.task?.approval_context || {}
  return context.approval_blocked_reason || (context.plan_outdated ? '历史计划需要退回重新审查，不能直接批准。' : !context.can_decide ? context.reason || '当前申请不可审批，请刷新后核对状态。' : '')
}
function approvalTitle(approval) {
  const task = approval.task || {}
  const request = task.request || {}
  return task.skill_id === 'procurement' ? request.purpose || task.goal : [request.target_system, request.resource].filter(Boolean).join(' · ') || task.goal
}
function approvalFacts(approval) {
  const task = approval.task || {}
  const request = task.request || {}
  if (task.skill_id === 'procurement') return [request.estimated_amount != null ? `预计金额 ${money(request.estimated_amount)} 元` : '', request.budget_code ? `预算 ${request.budget_code}` : '', request.vendor ? `供应商 ${request.vendor}` : ''].filter(Boolean).join(' · ')
  return [request.permission_level ? `权限 ${formatValue(request.permission_level)}` : '', request.data_classification ? `数据 ${formatValue(request.data_classification)}` : '', request.duration_days != null ? `期限 ${request.duration_days} 天` : ''].filter(Boolean).join(' · ')
}
function formatApprovalTime(value) {
  if (!value) return '时间未记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '时间未记录' : date.toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function inspectApproval(approval) {
  activeView.value = 'tasks'
  await selectTask(approval.task_id)
}

async function decideApproval(approval, decision) {
  if (decidingApproval.value !== null) return
  decidingApproval.value = approval.id
  try {
  const labels = { approved: '批准内部登记', rejected: '拒绝', changes_requested: '退回修改' }
  let value
  try {
    ({ value } = await ElMessageBox.prompt(`填写${labels[decision]}意见`, `${labels[decision]}任务 #${approval.task_id}`, {
      inputPlaceholder: '审批意见将写入执行档案',
      confirmButtonText: labels[decision],
      cancelButtonText: '取消',
    }))
  } catch (error) {
    if (error === 'cancel' || error === 'close') return
    throw error
  }
  await decideAgentApproval(approval.id, { decision, comment: value || '' })
  ElMessage.success(`已${labels[decision]}`)
  await loadApprovalRows({ silent: true })
  if (activeView.value === 'approvals') await loadApprovalHistory()
  await loadTasks()
  } finally { decidingApproval.value = null }
}

async function openSettings() {
  if (settingsLoading.value || savingRiskPolicy.value || Object.values(savingSkills).some(Boolean)) return
  if (riskPolicyDirty.value) {
    try { await ElMessageBox.confirm('重新加载会放弃未保存的风险策略修改。是否继续？', '重新加载配置', { confirmButtonText: '重新加载', cancelButtonText: '保留修改' }) }
    catch { return }
  }
  activeView.value = 'settings'
  settingsLoading.value = true
  settingsError.value = ''
  try {
    await loadSkills()
    Object.assign(riskPolicy, await getAgentRiskPolicy())
    riskPolicySnapshot.value = JSON.stringify(riskPolicy)
  } catch { settingsError.value = '配置加载失败，请重新加载后再修改。' }
  finally { settingsLoading.value = false }
}

async function saveSkill(skill) {
  if (savingSkills[skill.id]) return
  const enabled = skill.enabled
  savingSkills[skill.id] = true
  try {
    await updateAgentSkill(skill.id, { enabled, config: skill.config || {} }, { silent: true })
    ElMessage.success(`${skill.name}已${enabled ? '启用' : '停用'}`)
  } catch {
    skill.enabled = !enabled
    ElMessage.error(`${skill.name}保存失败，已恢复原状态`)
  } finally { savingSkills[skill.id] = false }
}

async function saveRiskPolicy() {
  if (savingRiskPolicy.value || !riskPolicyDirty.value) return
  if (!Number.isInteger(riskPolicy.procurement_approval_amount) || !Number.isInteger(riskPolicy.access_max_duration_days)) {
    ElMessage.warning('请完整填写整数金额门槛和期限阈值')
    return
  }
  savingRiskPolicy.value = true
  try {
  Object.assign(riskPolicy, await updateAgentRiskPolicy({
    procurement_approval_amount: riskPolicy.procurement_approval_amount,
    procurement_requires_quotation: riskPolicy.procurement_requires_quotation,
    procurement_sensitive_data_requires_approval: riskPolicy.procurement_sensitive_data_requires_approval,
    access_high_risk_classification: riskPolicy.access_high_risk_classification,
    access_max_duration_days: riskPolicy.access_max_duration_days,
  }, { silent: true }))
  riskPolicySnapshot.value = JSON.stringify(riskPolicy)
  ElMessage.success('企业风险策略已保存')
  } catch { ElMessage.error('风险策略保存失败，修改内容已保留，请重试') }
  finally { savingRiskPolicy.value = false }
}

async function loadEvaluations() {
  const request = ++evaluationsRequest
  evaluationsLoading.value = true
  try {
    const runs = await listAgentEvaluationRuns({ silent: true })
    if (request !== evaluationsRequest) return
    evaluationRuns.value = runs
    evaluationError.value = ''
    if (!selectedEvaluation.value && runs.length) await selectEvaluation(runs[0].id)
    return true
  } catch {
    if (request === evaluationsRequest) evaluationError.value = '评测记录加载失败，请刷新重试。'
    return false
  } finally {
    if (request === evaluationsRequest) evaluationsLoading.value = false
  }
}

async function refreshEvaluations() {
  if (!await loadEvaluations()) return
  if (selectedEvaluation.value) await selectEvaluation(selectedEvaluation.value.id)
  if (!evaluationError.value) ElMessage.success('评测记录已刷新')
}

async function openEvaluation() {
  activeView.value = 'evaluation'
  await loadEvaluations()
}

async function runEvaluation() {
  if (evaluationRunning.value || evaluationBusy.value) return
  evaluationRunning.value = true
  try {
    const run = await createAgentEvaluationRun({ silent: true })
    await loadEvaluations()
    await selectEvaluation(run.id)
    ElMessage.success('Agent 评测已进入队列')
  } catch { ElMessage.error('评测请求未完成，请刷新记录确认状态后再试')
  } finally { evaluationRunning.value = false }
}

async function selectEvaluation(id) {
  const epoch = ++evaluationSelectionEpoch
  evaluationDetailLoading.value = true
  try {
    const run = await getAgentEvaluationRun(id, { silent: true })
    if (epoch === evaluationSelectionEpoch) { selectedEvaluation.value = run; evaluationError.value = '' }
  } catch {
    if (epoch === evaluationSelectionEpoch) evaluationError.value = '评测详情加载失败，当前保留上次结果，请重新选择记录。'
  } finally { if (epoch === evaluationSelectionEpoch) evaluationDetailLoading.value = false }
}

function fieldType(property = {}) { return property.type || property.anyOf?.find((item) => item.type !== 'null')?.type || 'string' }
function fieldBound(property, key) { return property[key] ?? property.anyOf?.find((item) => item.type !== 'null')?.[key] }
function fieldEnum(property = {}) { return property.enum || property.anyOf?.find((item) => item.enum)?.enum || [] }
function isMissing(name) { return selectedTask.value?.missing_fields?.includes(name) }
function fieldLabel(name) { return fieldLabels[name] || '其他信息' }
function enumLabel(value) { return ({ read: '只读', write: '读写', admin: '管理员', public: '公开', internal: '内部', secret: '秘密', confidential: '机密' }[value] || value) }
function skillName(id) { return skills.value.find((item) => item.id === id)?.name || ({ procurement: '采购申请', access_request: '系统权限申请', system: '公共状态机组件' }[id] || '未知业务类型') }
function riskLabel(level) { return ({ low: '低', medium: '中', high: '高' }[level] || '待定') }
function statusLabel(status) { return ({ draft: '草稿', pending_dispatch: '待派发', queued: '队列中', running: '执行中', waiting_input: '待补充', waiting_approval: '待审批', executing: '写入中', completed: '已完成', failed: '执行失败', cancel_requested: '中断中', cancelled: '已中断', rejected: '已驳回', pending: '待运行' }[status] || status) }
function percentMetric(value) { return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? `${Math.round(Number(value) * 100)}%` : '未统计' }
function categoryLabel(value) { return ({ skill_routing: 'Skill 路由', field_extraction: '字段抽取', field_validation: '字段校验', risk_and_approval: '风险与审批', tool_authorization: '工具权限', state_recovery: '状态转换约束' }[value] || value || '-') }
function diagnosticLabel(value) { return fieldLabels[value] || ({ amount: '金额', fields: '结构化字段', unprovided_fields: '未提供字段不可臆造', missing: '缺失字段', level: '风险等级', requires_approval: '需要审批', allowed: '工具或状态转换许可', skill_id: '业务 Skill', payload: '申请数据', policy: '测试风险配置', action: '测试工具', current: '原状态', target: '目标状态', name: '物品', quantity: '数量', unit_price: '单价', procurement_approval_amount: '采购金额门槛' }[value] || '检查项') }
function diagnosticValue(value) {
  if (Array.isArray(value)) return value.map(diagnosticValue).join('；') || '无'
  if (value && typeof value === 'object') return Object.entries(value).map(([key, item]) => `${diagnosticLabel(key)}：${diagnosticValue(item)}`).join('；')
  if (value === 'procurement' || value === 'access_request') return skillName(value)
  if (['low', 'medium', 'high'].includes(value)) return `${riskLabel(value)}风险`
  if (['draft', 'pending_dispatch', 'running', 'completed', 'waiting_input', 'failed'].includes(value)) return statusLabel(value)
  if (['create_procurement_record', 'create_access_record'].includes(value)) return actionLabel(value)
  return typeof value === 'string' ? enumLabel(localizeText(value)) : formatValue(value)
}
function actionLabel(action) { return ({ create_procurement_record: '创建内部采购记录', create_access_record: '创建内部权限登记' }[action] || '执行内部业务动作') }
function formatValue(value) {
  if (Array.isArray(value)) return value.map((item) => typeof item === 'object' ? `${item.name || '项目'} × ${item.quantity || 1}` : item).join('；') || '-'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return value === null || value === undefined || value === '' ? '-' : localizeText(String(value))
}
function formatBytes(size = 0) { return size < 1024 ? `${size}B` : `${(size / 1024).toFixed(1)}KB` }
function formatDate(value) { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '-' }
function pipelineState(node) {
  const steps = selectedTask.value?.steps || []
  const index = pipelineStages.findIndex((item) => item.id === node)
  const completed = steps.some((step) => step.node_name === node && step.status === 'completed')
  const currentIndex = pipelineStages.findIndex((item) => item.id === selectedTask.value?.current_node)
  const activeStages = { build_plan: 'deterministic_checks', revise_plan: 'compliance_audit', approval_gate: 'wait_for_approval', verify_result: 'execute_tools', build_artifact: 'execute_tools', finalize: 'execute_tools', validate_fields: 'extract_fields', wait_for_input: 'extract_fields' }
  const current = activeStages[selectedTask.value?.current_node] || selectedTask.value?.current_node
  if (current === node && selectedTask.value?.status !== 'completed') return selectedTask.value.status === 'failed' ? 'failed' : 'current'
  if (completed || selectedTask.value?.status === 'completed') return 'done'
  if (currentIndex === index || selectedTask.value?.current_node === node) return 'current'
  return 'pending'
}
</script>

<style scoped>
.agent-workspace { --line: #e2e5eb; --muted: #646a73; --ink: #1f2329; --brand: #3370ff; --el-color-primary: #3370ff; min-height: 0; position: relative; z-index: 2; display: grid; grid-template-columns: 244px minmax(0, 1fr); background: #f5f6f8; color: var(--ink); font-size: 14px; line-height: 1.6; }
.agent-workspace * { box-sizing: border-box; letter-spacing: 0; }
.agent-workspace :is(h1,h2,h3,p,dl,dd,ul,ol) { margin: 0; }
.agent-workspace :is(ul,ol) { padding: 0; list-style: none; }
.agent-workspace :is(button,input,textarea,select):focus-visible { outline: 2px solid #245ddd; outline-offset: 3px; }
.agent-workspace ::selection { background: #dce9ff; color: #183d89; }
.agent-workspace ::-webkit-scrollbar { width: 7px; height: 7px; }
.agent-workspace ::-webkit-scrollbar-thumb { background: #bbc9db; border-radius: 4px; }
.agent-workspace button { font: inherit; }
.agent-workspace small { font-size: 12px; color: var(--muted); }
.agent-rail { min-height: 0; display: flex; flex-direction: column; padding: 20px 12px 12px; border-right: 1px solid var(--line); background: #fff; }
.rail-heading { display: flex; gap: 10px; align-items: center; padding: 0 3px 18px; }
.rail-heading > div { min-width: 0; display: grid; gap: 3px; }
.rail-heading span { font-weight: 750; }
.icon-button { width: 34px; height: 34px; flex-shrink: 0; display: inline-grid; place-items: center; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--muted); cursor: pointer; }
.icon-button:hover { color: #245ddd; border-color: #9eb9ef; }
button:disabled { cursor: not-allowed; }
.rail-nav { display: grid; gap: 5px; border-top: 1px solid var(--line); padding: 14px 0; }
.rail-nav button { min-height: 42px; display: grid; grid-template-columns: 20px minmax(0,1fr) auto; gap: 8px; align-items: center; border: 0; border-radius: 6px; padding: 8px 10px; background: transparent; color: var(--muted); text-align: left; cursor: pointer; }
.rail-nav button.active { background: #eaf1ff; color: #174dc2; font-weight: 700; }
.rail-nav button:hover { background: #eef3fb; }
.rail-nav b { min-width: 22px; height: 22px; display: grid; place-items: center; background: #dce8ff; border-radius: 50%; font-size: 12px; }
.task-queue { min-height: 0; flex: 1; display: flex; flex-direction: column; border-top: 1px solid var(--line); padding-top: 15px; }
.queue-head { display: flex; align-items: center; justify-content: space-between; color: var(--muted); padding: 0 6px 8px; }
.queue-head .icon-button { border: 0; background: transparent; }
.new-task-button { flex: 0 0 42px; display: flex; justify-content: center; align-items: center; gap: 8px; border: 1px solid #bad0fb; border-radius: 6px; color: #194fbc; background: #edf3ff; font-weight: 650; cursor: pointer; }
.task-list { min-height: 0; flex: 1; overflow: auto; display: grid; align-content: start; gap: 6px; padding-top: 10px; }
.task-row { width: 100%; min-height: 66px; display: grid; grid-template-columns: 32px minmax(0,1fr) 7px; align-items: center; gap: 8px; border: 1px solid transparent; border-radius: 6px; background: transparent; padding: 9px 8px; text-align: left; cursor: pointer; }
.task-row:hover { background: #f1f5fc; }
.task-row.selected { background: #fff; border-color: #a9c3f2; }
.task-copy { min-width: 0; display: grid; gap: 4px; }
.task-copy strong { font-size: 14px; line-height: 1.45; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-kind { width: 32px; height: 32px; display: grid; place-items: center; flex-shrink: 0; border-radius: 6px; background: #eef3fb; color: #245ddd; }
.task-kind.procurement { background: #fff2db; color: #8d5500; }
.task-kind.access_request { background: #e6f5ee; color: #147451; }
.task-kind.large { width: 44px; height: 44px; font-size: 22px; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #697c93; }
.status-dot.running, .status-dot.executing, .status-dot.queued, .status-dot.pending_dispatch { background: #245ddd; }
.status-dot.completed { background: #158458; }
.status-dot.failed { background: #c83a32; }
.status-dot.waiting_input, .status-dot.waiting_approval { background: #b77a10; }
.queue-empty { text-align: center; padding: 24px 8px; color: var(--muted); }
.rail-health { display: flex; gap: 9px; align-items: center; padding: 16px 6px 0; margin-top: 16px; border-top: 1px solid var(--line); }
.rail-health > span { width: 8px; height: 8px; border-radius: 50%; background: #c83a32; flex-shrink: 0; }
.rail-health.healthy > span { background: #158458; }
.rail-health div { display: grid; gap: 2px; }
.rail-health strong { font-size: 13px; }
.rail-heading,.rail-nav,.queue-head,.rail-health { flex-shrink: 0; }
.agent-stage { min-height: 0; min-width: 0; display: grid; grid-template-rows: 72px minmax(0,1fr); }
.stage-head { display: flex; gap: 16px; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--line); padding: 0 24px; background: #fff; }
.stage-head h1 { font-size: 20px; line-height: 1.3; font-weight: 750; }
.stage-actions { display: flex; gap: 10px; align-items: center; }
.refresh-time { font-size: 12px; color: var(--muted); }
.stage-scroll { min-height: 0; overflow: auto; padding: 22px 24px 32px; }
.creation-view { background: #fff; }
.create-surface { width: min(1120px,100%); margin: 0 auto; padding: 20px 8px 32px; display: grid; gap: 28px; }
.create-copy { display: flex; gap: 16px; align-items: center; justify-content: space-between; }
.create-copy h2 { font-size: 24px; line-height: 1.4; font-weight: 650; word-break: keep-all; }
.supported-capabilities { display: flex; align-items: center; flex-wrap: wrap; gap: 16px 24px; margin-top: -8px; padding-bottom: 24px; border-bottom: 1px solid var(--line); }
.capabilities-label { font-size: 13px; color: var(--muted); }
.request-types { display: flex; align-items: center; flex-wrap: wrap; gap: 16px 32px; }
.request-type { display: inline-flex; align-items: center; gap: 9px; cursor: default; }
.request-type strong { font-size: 14px; font-weight: 550; }
.request-type-icon { display: grid; place-items: center; width: 30px; height: 30px; background: #edf3ff; color: #245bdb; border-radius: 6px; font-size: 18px; }
.request-type:nth-child(2) .request-type-icon { background: #e6f5ee; color: #147451; }
.goal-editor { display: grid; gap: 12px; }
.goal-editor > label { font-weight: 600; }
.create-workflow { display: flex; gap: 24px; align-items: center; padding: 24px 0 0 !important; border-top: 1px solid var(--line); }
.create-workflow li { flex: 1; display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 13px; white-space: nowrap; }
.create-workflow .el-icon { display: grid; place-items: center; flex-shrink: 0; width: 32px; height: 32px; border-radius: 6px; background: #f3f5f8; color: #515967; font-size: 16px; }
.skill-switcher { display: flex; gap: 18px; flex-wrap: wrap; }
.skill-switcher span { display: inline-flex; gap: 7px; align-items: center; color: var(--muted); }
.create-surface :deep(.el-textarea__inner) { min-height: 176px !important; padding: 16px; font-size: 14px; line-height: 1.8; border-radius: 6px; background: #fcfcfd; }
.goal-editor :deep(.el-textarea__inner),
.goal-editor :deep(.el-textarea__inner:hover),
.goal-editor :deep(.el-textarea__inner:focus),
.goal-editor :deep(.el-textarea__inner:focus-visible) { border: 1px solid var(--line); background: #fcfcfd; box-shadow: none; outline: none; outline-offset: 0; }
.create-footer { display: flex; gap: 24px; align-items: flex-end; justify-content: space-between; }
.kb-picker { width: min(520px,70%); display: grid; gap: 10px; padding: 0; }
.kb-picker label { font-weight: 600; }
.kb-picker .el-select { width: 100%; }
.kb-picker :deep(.el-select__wrapper) { min-height: 40px; }
.create-footer .el-button { height: 40px; padding-inline: 22px; }
.task-command-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 0 0 18px; }
.task-identity { display: flex; gap: 12px; align-items: center; min-width: 0; }
.task-identity > div { min-width: 0; }
.task-identity p { color: var(--muted); font-size: 12px; }
.task-applicant { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 8px; }
.task-applicant strong { font-weight: 600; color: #354a63; }
.task-identity h2 { font-size: 20px; line-height: 1.45; overflow-wrap: anywhere; }
.command-meta { display: flex; gap: 8px; flex-shrink: 0; }
.status-badge,.risk-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border: 1px solid var(--line); border-radius: 5px; background: #fff; color: var(--muted); font-size: 12px; font-weight: 650; white-space: nowrap; }
.status-badge.running,.status-badge.executing,.status-badge.pending_dispatch,.status-badge.queued { background: #edf3ff; border-color: #c2d6fa; color: #194fbc; }
.status-badge.completed,.risk-badge.low { background: #edf8f2; border-color: #b8dfc9; color: #14613f; }
.status-badge.waiting_input,.status-badge.waiting_approval,.risk-badge.medium { background: #fff8e9; border-color: #e8d19e; color: #855707; }
.status-badge.failed,.status-badge.cancelled,.risk-badge.high { background: #fff1ef; border-color: #efc1bb; color: #a83028; }
.execution-status { display: flex; gap: 12px; align-items: center; padding: 16px 18px; margin: 0 0 16px; border: 1px solid #c7d8f3; border-radius: 6px; background: #edf4ff; color: #214f9d; }
.execution-status > .el-icon { font-size: 22px; flex-shrink: 0; }
.execution-status > div { flex: 1; min-width: 0; }
.execution-status strong { font-size: 15px; }
.execution-status p { font-size: 13px; line-height: 1.65; margin-top: 3px; overflow-wrap: anywhere; }
.execution-status.waiting_approval,.execution-status.waiting_input { color: #855707; background: #fff8e9; border-color: #e8d19e; }
.execution-status.failed { color: #a83028; background: #fff1ef; border-color: #efc1bb; }
.execution-status.completed { color: #14613f; background: #edf8f2; border-color: #b8dfc9; }
.pipeline-strip { display: grid; grid-template-columns: repeat(6,minmax(100px,1fr)); border: 1px solid var(--line); border-radius: 6px; background: #fff; overflow-x: auto; margin-bottom: 20px; }
.pipeline-stage { display: flex; align-items: center; gap: 8px; min-height: 68px; padding: 12px; color: var(--muted); border-right: 1px solid var(--line); }
.pipeline-stage:last-child { border-right: 0; }
.pipeline-stage > span { font-size: 12px; font-variant-numeric: tabular-nums; }
.pipeline-stage div { display: grid; gap: 2px; }
.pipeline-stage strong { font-size: 13px; }
.pipeline-stage.done { color: #14613f; background: #f3faf6; }
.pipeline-stage.current { color: #194fbc; background: #edf3ff; box-shadow: inset 0 -2px #245ddd; }
.execution-status { transition: background .2s ease-out, border-color .2s ease-out; }
.pipeline-stage { transition: background .2s ease-out, color .2s ease-out; }
.pipeline-stage.failed { color: #a83028; background: #fff1ef; }
.task-layout { display: grid; grid-template-columns: minmax(0,1fr) 310px; gap: 24px; align-items: start; }
.task-primary { min-width: 0; padding: 0 24px; background: #fff; }
.work-section { min-width: 0; padding: 24px 0; border-bottom: 1px solid var(--line); }
.work-section:last-child { border-bottom: 0; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 18px; }
.section-title h3 { font-size: 17px; line-height: 1.4; font-weight: 720; }
.section-title > span { font-size: 12px; color: var(--muted); }
.input-required { padding-bottom: 26px; }
.dynamic-form { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 22px 20px; }
.form-field { min-width: 0; display: grid; align-content: start; gap: 8px; }
.form-field-wide { grid-column: 1 / -1; }
.form-field label { font-size: 14px; font-weight: 650; color: #354a63; }
.form-field label.required::after { content: '待补充'; color: #945c00; margin-left: 8px; font-size: 12px; font-weight: 500; }
.input-required .input-confirmation-note { margin: -4px 0 20px; color: #627087; font-size: 12px; line-height: 1.7; }
.inspector-section .material-description { margin: 0 0 12px; }
.dynamic-form :deep(.el-input-number),.dynamic-form :deep(.el-select) { width: 100%; }
.dynamic-form :deep(.el-input__wrapper),.dynamic-form :deep(.el-select__wrapper) { min-height: 40px; }
.item-editor { display: grid; grid-template-columns: minmax(120px,1.5fr) minmax(95px,.8fr) minmax(110px,1fr) minmax(85px,.8fr) 66px; gap: 12px; align-items: start; padding: 14px 0; border-bottom: 1px solid var(--line); }
.item-cell { min-width: 0; display: grid; gap: 8px; }
.item-cell label { font-size: 12px; font-weight: 550; }
.subtotal strong { min-height: 40px; display: flex; align-items: center; font-size: 15px; font-variant-numeric: tabular-nums; }
.item-actions { display: grid; justify-items: center; gap: 5px; padding-top: 26px; }
.text-action { padding: 0; border: 0; background: transparent; color: #194fbc; font-size: 12px !important; cursor: pointer; }
.text-action:disabled { color: var(--muted); }
.amount-note { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: baseline; font-size: 12px; color: var(--muted); }
.readonly-state { min-height: 40px; display: flex; align-items: center; color: var(--muted); }
.section-actions { display: flex; justify-content: flex-end; padding-top: 22px; }
.request-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px 24px; }
.request-grid > div { min-width: 0; }
.request-grid dt { color: var(--muted); font-size: 12px; margin-bottom: 5px; }
.request-grid dd { font-size: 14px; overflow-wrap: anywhere; }
.evidence-list li { display: grid; grid-template-columns: 24px minmax(0,1fr); gap: 12px; padding: 14px 0; border-top: 1px solid var(--line); }
.evidence-list li > span { color: #194fbc; font-size: 12px; }
.evidence-list strong { display: flex; gap: 8px; flex-wrap: wrap; font-size: 14px; }
.evidence-list em { font-size: 12px; font-style: normal; color: #194fbc; }
.evidence-list small { display: block; margin: 4px 0; }
.evidence-list summary,.technical-details summary { cursor: pointer; color: #194fbc; font-size: 12px; }
.evidence-list p { padding-top: 8px; font-size: 14px; color: #354a63; white-space: pre-wrap; overflow-wrap: anywhere; }
.split-section { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 28px; }
.split-section > div { min-width: 0; }
.plan-summary { color: #4e535c; line-height: 1.8; }
.plan-sequence { display: grid; gap: 18px; margin: 18px 0; padding: 0; list-style: none; }
.plan-sequence > li { display: grid; grid-template-columns: 24px minmax(0,1fr); gap: 10px; align-items: start; }
.plan-sequence > li > span { display: grid; place-items: center; width: 24px; height: 24px; border-radius: 50%; background: #edf3ff; color: #245bdb; font-size: 12px; font-weight: 700; }
.plan-sequence strong { font-size: 14px; color: #202733; }
.plan-sequence p { margin-top: 4px; font-size: 14px; color: #4e535c; line-height: 1.75; }
.plan-outcome { display: grid; gap: 5px; margin-top: 16px; font-size: 14px; line-height: 1.75; }
.scope-note { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); color: #596271; font-size: 13px; line-height: 1.75; }
.scope-note strong { display: block; margin-bottom: 4px; color: #354a63; }
.review-next { font-size: 14px; color: #354a63; line-height: 1.75; }
.review-next > strong { display: block; margin-bottom: 4px; }
.review-overview h4,.explanation-details h4 { margin: 18px 0 10px; font-size: 14px; color: #202733; }
.explanation-details > p { font-size: 14px; color: #4e535c; line-height: 1.8; margin-bottom: 8px; overflow-wrap: anywhere; }
.explanation-list { display: grid; gap: 18px; list-style: decimal; padding-left: 22px; }
.explanation-list > li { padding-left: 4px; color: #596271; }
.explanation-list h5 { margin: 0 0 6px; font-size: 14px; color: #202733; }
.explanation-list p { margin: 0 0 6px; font-size: 14px; font-weight: 400; line-height: 1.8; overflow-wrap: anywhere; }
.blocking-title,.blocking-list h5 { color: #a83028 !important; }
.original-explanation { margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--line); }
.original-explanation summary { color: #596271; font-size: 12px; }
.original-explanation p { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13px; line-height: 1.8; margin-top: 10px; }
.policy-check-list li + li { padding-top: 12px; border-top: 1px solid var(--line); }
.policy-check-list small { color: #855707; }
.action-list { display: grid; gap: 8px; margin-top: 14px !important; }
.action-list li { display: flex; align-items: center; gap: 8px; }
.action-list .el-icon { color: #646a73; }
.action-list .el-icon.action-completed { color: #158458; }
.plan-outcome strong { font-size: 14px; color: #354a63; font-weight: 700; }
.plan-outcome { margin-top: 14px !important; color: var(--muted); }
.audit-verdict { display: grid; gap: 12px; }
.audit-verdict > strong { color: #a83028; }
.audit-verdict.passed > strong { color: #14613f; }
.verdict-label { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.verdict-label .el-icon { font-size: 18px; }
.audit-highlights { display: grid; gap: 10px; color: #4e535c; line-height: 1.8; }
.explanation-details { margin-top: 16px; font-size: 13px; }
.explanation-details summary { cursor: pointer; color: #245bdb; }
.explanation-details[open] summary { margin-bottom: 12px; }
.audit-verdict:has(> .audit-explanation[open]) > .blocking-issues,
.audit-verdict:has(> .audit-explanation[open]) > .review-overview { display: none; }
.blocking-issues ul { display: grid; gap: 8px; }
.blocking-issues li { font-weight: 600; line-height: 1.7; }
.blocking-issues li p { margin-top: 4px; font-size: 14px; font-weight: 400; color: #5c3735; line-height: 1.75; }
.audit-highlights p { margin-top: 4px; font-size: 14px; line-height: 1.75; }
.blocking-issues { padding: 12px; background: #fff1ef; border: 1px solid #efc1bb; border-radius: 6px; color: #a83028; }
.blocking-issues > strong { display: block; margin-bottom: 8px; }
.result-section { display: flex; align-items: center; gap: 14px; color: #14613f; }
.result-mark { font-size: 30px; display: grid; place-items: center; }
.result-section h3 { font-size: 18px; }
.task-inspector { min-width: 0; }
.inspector-section { padding: 0 0 22px; margin-bottom: 22px; border-bottom: 1px solid var(--line); }
.inspector-section .section-title { margin-bottom: 12px; }
.inspector-section h3 { font-size: 15px; }
.risk-display { display: flex; justify-content: space-between; gap: 12px; align-items: baseline; padding: 12px 14px; background: #fff; border: 1px solid var(--line); border-radius: 6px; }
.risk-display strong { font-size: 22px; }
.risk-display span { color: var(--muted); font-size: 12px; }
.risk-display.low { color: #14613f; background: #edf8f2; border-color: #b8dfc9; }
.risk-display.medium { color: #855707; background: #fff8e9; border-color: #e8d19e; }
.risk-display.high { color: #a83028; background: #fff1ef; border-color: #efc1bb; }
.policy-decisions { margin-top: 16px; }
.policy-decisions h4 { margin: 0 0 10px; font-size: 14px; }
.policy-decisions .policy-blocked strong { color: #a83028; }
.inspector-note { margin-top: 10px !important; font-size: 13px; color: var(--muted); overflow-wrap: anywhere; }
.approval-required { display: block; margin-top: 12px; color: #855707; font-size: 13px; }
.hidden-file { display: none; }
.upload-control { width: 100%; height: 40px; }
.upload-reason { font-size: 12px; }
.attachment-list { display: grid; gap: 12px; margin-top: 14px; }
.attachment-list > div { display: grid; grid-template-columns: 20px minmax(0,1fr); gap: 8px; align-items: start; }
.attachment-list span { display: grid; min-width: 0; gap: 3px; }
.attachment-list strong { font-size: 13px; overflow-wrap: anywhere; }
.attachment-error { color: #a83028 !important; grid-column: 2; font-size: 12px; overflow-wrap: anywhere; }
.trace-section { max-height: 480px; overflow: auto; }
.trace-list li { display: grid; grid-template-columns: 8px minmax(0,1fr); gap: 10px; padding: 0 0 16px; }
.trace-list li > span { width: 7px; height: 7px; border-radius: 50%; margin-top: 8px; background: #158458; }
.trace-list li.failed > span { background: #c83a32; }
.trace-list li.running > span { background: #245ddd; }
.trace-list li.interrupted > span { background: #8c650d; }
.trace-list div { display: grid; gap: 3px; }
.trace-list strong { font-size: 13px; }
.trace-list small { overflow-wrap: anywhere; }
.inspector-actions { display: grid; gap: 12px; }
.inspector-actions .el-button { margin: 0; }
.technical-details pre { max-height: 200px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
.empty-line { color: var(--muted); font-size: 13px; padding: 6px 0; }
.large-empty { min-height: 180px; display: grid; place-content: center; justify-items: center; gap: 14px; color: var(--muted); }
.large-empty .el-icon { color: #158458; font-size: 32px; }
.list-surface,.settings-surface,.evaluation-surface { max-width: 1280px; margin: 0 auto; }
.list-header { display: flex; align-items: center; justify-content: space-between; padding: 10px 0 16px; border-bottom: 1px solid var(--line); font-weight: 650; }
.evaluation-runs article { display: grid; grid-template-columns: 55px minmax(0,1fr) auto auto; align-items: center; gap: 18px; padding: 20px 0; border-bottom: 1px solid var(--line); }
.evaluation-runs article > div:first-child { display: grid; gap: 5px; }
.evaluation-runs strong { font-size: 14px; overflow-wrap: anywhere; }
.approval-header { gap: 16px; }
.approval-header h2 { flex: 1; font-size: 17px; }
.approval-header small { font-size: 13px; margin-left: 8px; font-weight: 400; }
.approval-pending-count { font-size: 13px; color: var(--muted); }
.approval-pending-count strong { color: #855707; margin-left: 6px; font-variant-numeric: tabular-nums; }
.approval-toolbar { display: grid; grid-template-columns: minmax(240px,1fr) 150px 160px 190px; gap: 16px; align-items: end; padding: 20px 0; }
.approval-toolbar label { display: grid; gap: 7px; font-size: 12px; color: #354a63; }
.approval-toolbar label > span { display: inline-flex; gap: 5px; align-items: center; }
.approval-toolbar :deep(.el-input__wrapper),.approval-toolbar :deep(.el-select__wrapper) { min-height: 40px; }
.approval-toolbar .el-select { width: 100%; }
.approval-results { min-height: 180px; }
.approval-list article { display: grid; grid-template-columns: minmax(0,1fr) minmax(220px,auto); gap: 16px 24px; align-items: start; padding: 22px 16px; border-bottom: 1px solid var(--line); background: #fff; }
.approval-list article.approval-pending { background: #f7faff; }
.approval-content { min-width: 0; }
.approval-row-heading { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: 12px; color: var(--muted); margin-bottom: 10px; }
.approval-row-heading .task-kind { width: 28px; height: 28px; font-size: 16px; }
.approval-id { color: #354a63; font-variant-numeric: tabular-nums; }
.approval-state { color: #515967; font-weight: 600; }
.approval-state.pending { color: #855707; }
.approval-state.approved,.approval-approved { color: #14613f; }
.approval-state.rejected { color: #a83028; }
.approval-title { font-size: 15px; line-height: 1.6; font-weight: 600; overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.approval-facts { font-size: 13px; line-height: 1.7; margin-top: 6px; color: #354a63; overflow-wrap: anywhere; }
.approval-metadata { display: flex; flex-wrap: wrap; gap: 6px 20px; margin-top: 12px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.approval-metadata strong { font-size: 13px; color: #354a63; font-weight: 600; }
.applicant-username { margin-left: 4px; }
.approval-progress { margin-top: 6px; font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.approval-comment { margin-top: 10px; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; white-space: pre-wrap; color: #354a63; }
.approval-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; max-width: 440px; padding-top: 4px; }
.approval-actions .el-button { margin: 0; }
.approval-actions .el-icon { margin-right: 5px; }
.approval-actions .approval-required { flex-basis: 100%; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.approval-surface .el-pagination { display: flex; justify-content: flex-end; margin-top: 22px; }
.settings-surface,.evaluation-surface { width: 100%; align-content: start; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; }
.settings-toolbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 20px; }
.settings-toolbar h2,.evaluation-command h2 { margin: 0; font-size: 20px; line-height: 1.5; }
.settings-toolbar span,.scope-label { color: var(--muted); font-size: 13px; }
.settings-content { background: white; padding: 0 24px; }
.setting-band { display: grid; grid-template-columns: 160px minmax(0,1fr); gap: 24px; padding: 24px 0; border-bottom: 1px solid var(--line); }
.setting-band:last-child { border-bottom: 0; }
.setting-heading h3 { font-size: 16px; margin: 0 0 4px; }
.setting-heading > span { color: var(--muted); font-size: 12px; }
.skill-rows > div { display: grid; grid-template-columns: 34px minmax(0,1fr) auto; gap: 14px; align-items: center; padding: 18px 0; border-bottom: 1px solid var(--line); }
.skill-rows > div:first-child { padding-top: 0; }
.skill-rows > div:last-child { border-bottom: 0; padding-bottom: 0; }
.skill-rows > div > div { display: grid; gap: 4px; }
.skill-rows small { color: var(--muted); font-size: 12px; }
.skill-version { font-size: 12px; color: var(--muted); margin-left: 8px; font-weight: 400; }
.skill-toggle { justify-items: center; min-width: 56px; }
.policy-boundary,.evaluation-boundary { display: flex; gap: 10px; align-items: flex-start; color: #385781; background: #f0f5ff; padding: 12px 16px; font-size: 13px; }
.policy-boundary .el-icon,.evaluation-boundary .el-icon { margin-top: 3px; flex-shrink: 0; }
.policy-group { border: 0; border-top: 1px solid var(--line); padding: 20px 0 0; margin: 24px 0 0; min-width: 0; }
.policy-group legend { padding-right: 12px; font-weight: 650; font-size: 15px; }
.policy-group legend .el-icon { margin-right: 8px; vertical-align: middle; color: var(--brand); }
.policy-editor { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 22px; }
.policy-field { display: grid; gap: 8px; align-content: start; }
.policy-field label { font-weight: 650; }
.policy-field > div { display: grid; gap: 8px; }
.policy-field span { color: var(--muted); font-size: 12px; }
.policy-field :deep(.el-input-number),.policy-field :deep(.el-select) { width: 100%; }
.policy-actions { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; border-top: 1px solid var(--line); padding-top: 20px; margin-top: 24px; }
.policy-actions > span { color: var(--muted); font-size: 13px; }
.evaluation-command { display: grid; grid-template-columns: minmax(0,1fr) auto auto; align-items: center; gap: 20px; padding: 12px 0 24px; border-bottom: 1px solid var(--line); }
.evaluation-summary { display: flex; gap: 24px; }
.evaluation-summary span { display: grid; gap: 4px; font-size: 12px; color: var(--muted); }
.evaluation-summary strong { color: var(--ink); font-size: 20px; }
.evaluation-boundary { margin: 16px 0; }
.evaluation-layout { display: grid; grid-template-columns: 290px minmax(0,1fr); gap: 20px; align-items: start; }
.evaluation-runs { margin-top: 0; background: white; padding: 8px 12px; }
.evaluation-run { width: 100%; display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 10px; align-items: center; text-align: left; cursor: pointer; padding: 16px 12px; background: transparent; border: 0; border-bottom: 1px solid var(--line); color: var(--ink); font: inherit; transition: background 160ms ease-out; }
.evaluation-run > div:first-child { display: grid; gap: 4px; }
.evaluation-run strong { font-size: 14px; }
.evaluation-run small { color: var(--muted); font-size: 12px; }
.evaluation-run .score { grid-column: 1 / -1; display: flex; align-items: baseline; text-align: left; gap: 8px; }
.evaluation-run:hover { background: #f5f7fa; }
.evaluation-run.selected { background: #eaf1ff; }
.evaluation-run:focus-visible { outline: 2px solid var(--brand); outline-offset: -2px; }
.evaluation-empty { min-height: 200px; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 12px; color: var(--muted); text-align: center; }
.evaluation-empty > .el-icon { font-size: 28px; color: #7e92ac; }
.evaluation-empty h3 { margin: 0; font-size: 16px; color: var(--ink); }
.evaluation-progress { display: flex; gap: 10px; align-items: center; color: #245bdb; margin-top: 16px; }
.evaluation-version { color: var(--muted); font-size: 12px; margin-top: 8px; overflow-wrap: anywhere; }
.case-input { font-size: 13px; padding: 12px 0 0; overflow-wrap: anywhere; }
.case-input p { margin: 4px 0; }
.score { display: grid; gap: 3px; text-align: right; }
.score strong { font-size: 20px; }
.evaluation-detail { margin-top: 0; min-width: 0; background: white; padding: 20px; }
.metric-line { display: grid; grid-template-columns: repeat(5,minmax(0,1fr)); gap: 16px; margin: 18px 0; }
.metric-line > div { display: grid; gap: 4px; }
.metric-line span { color: var(--muted); font-size: 12px; }
.metric-line strong { font-size: 22px; }
.evaluation-case { display: grid; grid-template-columns: 90px minmax(0,1fr) auto; gap: 16px; align-items: start; padding: 14px 0; border-bottom: 1px solid var(--line); }
.evaluation-case > div { display: grid; gap: 4px; }
.evaluation-case code { color: #194fbc; font-size: 12px; }
.case-diagnostics summary { color: var(--brand); cursor: pointer; font-size: 13px; }
.case-diagnostics dl { margin: 10px 0; padding: 12px; background: #f5f6f8; }
.case-diagnostics dt { font-size: 13px; font-weight: 650; }
.case-diagnostics dd { font-size: 13px; margin: 4px 0; overflow-wrap: anywhere; }
.case-diagnostics pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f6f8; padding: 12px; font-size: 12px; max-height: 240px; overflow: auto; }
.case-result { color: #a83028; font-weight: 650; }
.case-result.passed { color: #14613f; }
@media (max-width: 1500px) {
  .task-layout { grid-template-columns: minmax(0,1fr) 270px; gap: 18px; }
  .item-editor { grid-template-columns: minmax(100px,1.3fr) minmax(80px,.8fr) minmax(100px,1fr) 80px 42px; gap: 10px; }
  .item-actions .text-action { font-size: 12px; }
  .split-section { grid-template-columns: 1fr; }
  .split-section > div + div { border-top: 1px solid var(--line); padding-top: 22px; }
}
@media (max-width: 1180px) {
  .evaluation-layout { grid-template-columns: 240px minmax(0,1fr); }
  .evaluation-command { grid-template-columns: 1fr auto; }
  .evaluation-summary { grid-row: 2; grid-column: 1 / -1; }
  .metric-line { grid-template-columns: repeat(3,minmax(0,1fr)); }
  .task-layout { grid-template-columns: 1fr; }
  .task-inspector { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 20px; }
  .trace-section { grid-column: 1 / -1; }
  .approval-toolbar { grid-template-columns: repeat(3,minmax(0,1fr)); }
  .approval-search { grid-column: 1 / -1; }
  .approval-list article { grid-template-columns: minmax(0,1fr); }
  .approval-actions { max-width: none; justify-content: flex-start; }
}
@media (max-width: 820px) {
  .approval-toolbar { grid-template-columns: minmax(0,1fr); gap: 12px; }
  .approval-toolbar label { grid-template-columns: 76px minmax(0,1fr); align-items: center; }
  .approval-header { flex-wrap: wrap; gap: 12px; }
  .approval-list article { padding: 18px 12px; }
  .approval-metadata { flex-direction: column; }
  .settings-content { padding: 0 16px; }
  .settings-toolbar { flex-wrap: wrap; }
  .setting-band { gap: 16px; }
  .evaluation-layout { grid-template-columns: 1fr; }
  .evaluation-summary { grid-row: auto; }
  .evaluation-summary { gap: 20px; }
  .agent-workspace { grid-template-columns: minmax(0,1fr); overflow: auto; }
  .agent-rail { min-height: auto; padding-top: 12px; }
  .rail-heading { padding-bottom: 12px; }
  .rail-nav { display: flex; flex-wrap: wrap; padding: 10px 0; }
  .rail-nav button { flex: 1 1 140px; }
  .task-queue { max-height: 210px; }
  .rail-health { padding: 10px 6px; margin-top: 10px; }
  .agent-stage { min-height: 720px; grid-template-rows: auto minmax(0,1fr); }
  .stage-head { min-width: 0; padding: 16px; flex-wrap: wrap; }
  .stage-scroll { min-width: 0; padding: 16px; overflow: visible; }
  .stage-actions { max-width: 100%; flex-wrap: wrap; }
  .refresh-time { display: none; }
  .create-surface { padding: 8px 0 24px; }
  .create-copy h2 { font-size: 21px; }
  .create-copy { align-items: flex-start; }
  .supported-capabilities { gap: 12px; }
  .request-types { gap: 14px; }
  .create-workflow { overflow-x: auto; gap: 18px; padding-bottom: 8px !important; }
  .create-workflow li { flex: 0 0 auto; }
  .create-footer { align-items: stretch; flex-direction: column; }
  .kb-picker { width: 100%; }
  .task-command-bar { flex-direction: column; align-items: flex-start; gap: 12px; }
  .task-identity h2 { font-size: 18px; }
  .pipeline-strip { grid-template-columns: repeat(6,125px); }
  .task-primary { padding: 0 16px; }
  .dynamic-form,.request-grid,.task-inspector,.setting-band,.policy-editor { grid-template-columns: 1fr; }
  .item-editor { grid-template-columns: repeat(2,minmax(0,1fr)); gap: 12px; }
  .item-name { grid-column: 1 / -1; }
  .item-actions { padding-top: 0; display: flex; justify-content: flex-end; align-items: center; }
  .execution-status { flex-wrap: wrap; }
  .execution-status > div { flex-basis: calc(100% - 40px); }
  .execution-status .el-button { margin-left: 34px; }
  .evaluation-command { grid-template-columns: 1fr; }
  .metric-line { grid-template-columns: repeat(2,minmax(0,1fr)); }
  .evaluation-case { grid-template-columns: 75px minmax(0,1fr); }
  .case-result { grid-column: 2; }
  .evaluation-runs article { grid-template-columns: minmax(0,1fr) auto; }
  .score { grid-column: 1 / -1; text-align: left; }
}
@media (prefers-reduced-motion: reduce) {
  .agent-workspace :deep(.is-loading) { animation: none; }
  .agent-workspace * { transition: none !important; }
}
</style>
