import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = dirname(dirname(fileURLToPath(import.meta.url)))

function assert(condition, message) {
  if (!condition) {
    throw new Error(message)
  }
}

const chatArea = readFileSync(join(root, 'src/components/ChatArea.vue'), 'utf8')
assert(
  chatArea.includes('@keydown.enter.exact.prevent="handleSend"'),
  'Enter should send the message without inserting a newline.',
)
assert(
  !chatArea.includes('@keydown.shift.enter.prevent'),
  'Shift + Enter should keep the browser textarea newline behavior.',
)

assert(
  chatArea.includes('const hasSelectedKb = computed(() => props.selectedKbIds.length > 0)'),
  'Chat input should derive whether at least one knowledge base is selected.',
)
assert(
  chatArea.includes(':disabled="!hasSelectedKb || loading"'),
  'Send button should be disabled until a knowledge base is selected.',
)
assert(
  chatArea.includes("selectedKbIds.length ? currentKb?.name : '请选择知识库'"),
  'Knowledge-base badge should show a choose prompt when no knowledge base is selected.',
)
assert(
  !chatArea.includes("知识库：{{ currentKb?.name || '未选择' }}"),
  'Knowledge-base badge should not use currentKb when selectedKbIds is empty.',
)
assert(
  chatArea.includes("ElMessage.warning('请至少选择一个知识库后再提问')"),
  'Chat input should keep the draft and warn when Enter is pressed without a knowledge base.',
)
const dashboard = readFileSync(join(root, 'src/components/Dashboard.vue'), 'utf8')
assert(
  dashboard.includes('chunk.full_text || chunk.text_preview'),
  'Dashboard chunk expansion should render full_text when the API provides it.',
)

const app = readFileSync(join(root, 'src/App.vue'), 'utf8')
assert(
  app.includes("ElMessage.warning('请至少选择一个知识库后再提问')"),
  'Sending a question with no selected knowledge base should show a clear warning.',
)
assert(
  !app.includes('selectedKbIds.value.length > 0 ? selectedKbIds.value : [currentKb.value?.id ?? 1]'),
  'Sending should not silently fall back to the current or default knowledge base when none is selected.',
)

const docManager = readFileSync(join(root, 'src/components/DocManager.vue'), 'utf8')
assert(
  docManager.includes('type="selection"'),
  'Doc manager should allow selecting multiple documents.',
)
assert(
  docManager.includes('deleteDocuments'),
  'Doc manager should use the batch document delete API.',
)
assert(
  docManager.includes('selectedDocRows'),
  'Doc manager should track selected rows for batch deletion.',
)
assert(
  docManager.includes('批量删除'),
  'Doc manager should expose a batch delete action.',
)
assert(
  docManager.includes('hasProcessingDocs'),
  'Doc manager should keep polling while documents are still processing.',
)
assert(
  docManager.includes('docStatusPercent'),
  'Doc manager should calculate document parsing progress from the current status.',
)
assert(
  docManager.includes('doc-progress'),
  'Doc manager should render a compact progress bar for parsing/loading states.',
)
assert(
  docManager.includes('解析进度'),
  'Doc manager progress UI should expose a clear Chinese label.',
)
assert(
  docManager.includes('failure_reason'),
  'Doc manager should render backend document failure reasons.',
)
assert(
  docManager.includes('失败原因'),
  'Doc manager should label document failure reasons in Chinese.',
)

const chatAttachmentExpectations = [
  ['附件解析中', 'Chat attachments should show a processing state.'],
  ['附件已就绪', 'Chat attachments should show a ready state.'],
  ['附件解析失败', 'Chat attachments should show a failed state.'],
  ['附件仍在解析', 'Chat input should warn before sending while attachments are processing.'],
  ['attach-summary', 'Chat attachments should render extracted summaries.'],
]
for (const [needle, message] of chatAttachmentExpectations) {
  assert(chatArea.includes(needle), message)
}
assert(
  app.includes('attachment_sources') && app.includes('knowledge_base_sources'),
  'Chat stream handling should preserve attachment and knowledge-base source groups.',
)

const messageBubble = readFileSync(join(root, 'src/components/MessageBubble.vue'), 'utf8')
assert(
  messageBubble.includes('.user-bubble') && messageBubble.includes('#e8f1ff'),
  'User message bubbles should use a light, non-black surface.',
)
assert(
  !messageBubble.includes('.user-bubble {\n  background: var(--kb-graphite)'),
  'User message bubbles should not use the black graphite background.',
)
assert(
  messageBubble.includes('知识库来源') && messageBubble.includes('会话附件来源'),
  'Evidence panel should separate knowledge-base and attachment sources.',
)
assert(
  messageBubble.includes('source_type') && messageBubble.includes('attachmentSources'),
  'Evidence panel should group sources by source_type.',
)
assert(
  messageBubble.includes('function formatScore') && messageBubble.includes('Math.min(100'),
  'Evidence score display should clamp fused retrieval scores to 100%.',
)
console.log('UI contract checks passed')
