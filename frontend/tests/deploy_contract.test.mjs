import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import assert from 'node:assert/strict'

const root = join(import.meta.dirname, '..', '..')

const guidePath = join(root, '线上部署说明.md')
const composePath = join(root, 'deploy', 'docker-compose.prod.yml')
const mysqlComposePath = join(root, 'deploy', 'docker-compose.mysql.yml')
const nginxPath = join(root, 'deploy', 'nginx', 'kb-workbench.conf')
const systemdPath = join(root, 'deploy', 'systemd', 'kb-workbench-backend.service')
const envPath = join(root, 'deploy', 'env.production.example')
const requirementsPath = join(root, 'requirements.txt')
const envExamplePath = join(root, '.env.example')
const configPath = join(root, 'backend', 'app', 'config.py')
const milvusStorePath = join(root, 'backend', 'app', 'core', 'milvus_store.py')

// 线上部署说明.md 只在源码仓维护，公开仓不包含，因此单独判断
const hasGuide = existsSync(guidePath)

for (const file of [composePath, mysqlComposePath, nginxPath, systemdPath, envPath, requirementsPath, envExamplePath, configPath, milvusStorePath]) {
  assert.ok(existsSync(file), `${file} should exist for production deployment.`)
}

if (hasGuide) {
  const guide = readFileSync(guidePath, 'utf8')
  assert.ok(guide.includes('2核4G + Zilliz Cloud Free'), 'Deployment guide should document the low-cost Zilliz Cloud deployment model.')
  assert.ok(guide.includes('2核4G') && guide.includes('4核8G'), 'Deployment guide should include current low-cost and safer server specs.')
  assert.ok(guide.includes('不要开放 3306、19530、8001 到公网'), 'Deployment guide should warn against exposing internal service ports.')
  assert.ok(guide.includes('admin / admin123') && guide.includes('employee / employee123'), 'Deployment guide should include demo login accounts.')
  // 只校验 token 格式（username:password），不把任何真实库名写进断言
  assert.ok(/VECTOR_TOKEN=\S+:\S+/.test(guide), 'Deployment guide should show the Zilliz username:password token format.')
  assert.ok(guide.includes('当前线上方案只需要启动 MySQL'), 'Deployment guide should warn that the current low-cost deployment runs only MySQL locally.')
}

const compose = readFileSync(composePath, 'utf8')
assert.ok(compose.includes('mysql:8') && compose.includes('milvusdb/milvus'), 'Compose should provision MySQL and Milvus.')
assert.ok(compose.includes('127.0.0.1:3306:3306') && compose.includes('127.0.0.1:19530:19530'), 'Compose should bind database ports to localhost only.')
assert.ok(compose.includes('MYSQL_ROOT_PASSWORD: ${DB_PASSWORD:?set DB_PASSWORD in .env}'), 'Compose should reuse the deployment DB password from .env.')

const mysqlCompose = readFileSync(mysqlComposePath, 'utf8')
assert.ok(mysqlCompose.includes('mysql:8'), 'Low-cost compose should provision MySQL.')
assert.ok(mysqlCompose.includes('127.0.0.1:3306:3306'), 'Low-cost MySQL should bind only to localhost.')
assert.ok(!mysqlCompose.includes('milvusdb/milvus') && !mysqlCompose.includes('minio/minio') && !mysqlCompose.includes('coreos/etcd'), 'Low-cost compose should not start Milvus, MinIO, or etcd.')

const nginx = readFileSync(nginxPath, 'utf8')
assert.ok(nginx.includes('try_files $uri $uri/ /index.html'), 'Nginx should support Vue history routing.')
assert.ok(nginx.includes('proxy_pass http://127.0.0.1:8001'), 'Nginx should proxy /api to the local FastAPI backend.')
assert.ok(nginx.includes('root /var/www/kb-workbench/dist;'), 'Nginx should serve the production frontend dist path.')

const systemd = readFileSync(systemdPath, 'utf8')
assert.ok(systemd.includes('WorkingDirectory=/opt/langchain-chat-master/backend'), 'Systemd service should run from the backend directory.')
assert.ok(systemd.includes('--host 127.0.0.1 --port 8001'), 'Systemd should keep FastAPI bound to localhost.')
assert.ok(systemd.includes('Restart=always'), 'Systemd should restart the backend automatically.')

const env = readFileSync(envPath, 'utf8')
assert.ok(env.includes('AUTH_SECRET_KEY='), 'Production env template should require a strong auth secret.')
assert.ok(!env.includes('sk-你的'), 'Production env template should not include fake sk-style secrets.')
assert.ok(env.includes('MILVUS_TOKEN='), 'Production env template should expose MILVUS_TOKEN for Zilliz Cloud.')
assert.ok(env.includes('cloud.zilliz.com.cn'), 'Production env template should include a Zilliz Cloud endpoint example.')
assert.ok(env.includes('MILVUS_DB_NAME='), 'Production env template should expose MILVUS_DB_NAME.')
// 模板只能出现占位符，不能出现真实实例地址
assert.ok(!/MILVUS_URI=https:\/\/in\d+-[0-9a-f]+\./.test(env), 'Production env template should not leak a real Zilliz instance endpoint.')

const envExample = readFileSync(envExamplePath, 'utf8')
assert.ok(envExample.includes('MILVUS_TOKEN='), '.env.example should include optional MILVUS_TOKEN.')

const config = readFileSync(configPath, 'utf8')
assert.ok(config.includes('MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "")'), 'Backend config should read MILVUS_TOKEN.')

const milvusStore = readFileSync(milvusStorePath, 'utf8')
assert.ok(milvusStore.includes('MILVUS_TOKEN'), 'Milvus store should import and use MILVUS_TOKEN.')
assert.ok(milvusStore.includes('token=MILVUS_TOKEN'), 'MilvusClient should pass token when connecting to Zilliz Cloud.')
assert.ok(milvusStore.includes('MilvusClient(MILVUS_URI)'), 'Milvus store should keep the local Milvus connection path.')

const requirements = readFileSync(requirementsPath, 'utf8').toLowerCase()
assert.ok(requirements.includes('pymysql'), 'Production MySQL deployment should install the pymysql driver used by DATABASE_URL.')

console.log('Deployment contract checks passed')
