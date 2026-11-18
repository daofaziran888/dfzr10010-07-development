<template>
  <section class="page" data-module="lineage">
    <header class="page-head">
      <div>
        <h2>湖仓血缘追踪管理</h2>
        <p class="page-desc">维护湖仓血缘的上游下游追溯、字段映射与批次记录，为湖仓血缘追踪提供可追溯的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记血缘记录</button>
        <button class="btn" type="button" @click="exportRows">导出湖仓血缘追踪清单</button>
      </div>
    </header>

    <div class="view-switch">
      <button class="switch-item active" type="button">血缘列表</button>
      <button class="switch-item" type="button" @click="openImpactView()">影响视图</button>
      <span class="muted">
        当前影响批次：<strong>{{ store.batch || '未选择' }}</strong>
      </span>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号 / 表名</span>
        <input v-model="filters.keyword" placeholder="按记录编号、上游表名或下游表名检索" />
      </label>
      <label class="filter-item">
        <span>操作批次</span>
        <input v-model="filters.batch" placeholder="例如 20261115-03" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column" :class="{ 'mapping-cell': column === '映射文本' }">
            {{ row[column] ?? '—' }}
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="openBatch(row)">查看影响</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无湖仓血缘追踪数据，可先登记血缘记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条湖仓血缘追踪记录 · 第 {{ page }} 页</span>
      <span class="pager">
        <button class="link" type="button" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
        <button class="link" type="button" :disabled="page >= totalPages" @click="changePage(1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div class="panel-head">
      <h3>按操作批次查看影响面</h3>
      <span class="muted">同一批次只发布一份下游表清单，回滚记录与复核工作台共用这份清单</span>
    </div>
    <table class="data-table">
      <thead>
        <tr>
          <th>操作批次</th><th>血缘记录</th><th>有效血缘边</th><th>下游表</th>
          <th>字段路径</th><th>发布状态</th><th>复核状态</th><th>版本冲突</th><th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="batch in batches" :key="batch.操作批次">
          <td>{{ batch.操作批次 }}</td>
          <td>{{ batch.血缘记录数 }}</td>
          <td>{{ batch.有效血缘边数 }}</td>
          <td>{{ batch.下游表总数 }}</td>
          <td>{{ batch.字段路径总数 }}</td>
          <td>{{ batch.发布状态 }}</td>
          <td>{{ batch.复核状态 }}</td>
          <td>{{ batch.存在冲突 ? '迁移/回滚冲突' : '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openImpactView(batch.操作批次)">切换到影响视图</button>
          </td>
        </tr>
        <tr v-if="!batches.length">
          <td colspan="9" class="empty-state">暂无带操作批次的血缘记录</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'
import { fetchBatches, type ImpactBatch } from '@/api/impact'
import { useImpactStore } from '@/stores/impact'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/lineage'
const columns = ["记录编号", "上游表名", "下游表名", "转换类型", "映射文本", "上游版本", "下游版本", "操作时间", "操作批次", "记录状态"]
const actions = ["关联血缘", "标记过期", "清理记录"]
const statuses = ["已记录", "已关联", "已过期", "已清理"]
const stats = [{"label": "血缘记录总数", "value": 0}, {"label": "已关联记录", "value": 0}, {"label": "已过期记录", "value": 0}]

const router = useRouter()
const store = useImpactStore()

const rows = ref<Row[]>([])
const batches = ref<ImpactBatch[]>([])
const total = ref(0)
const page = ref(1)
const size = 20
const totalPages = computed(() => Math.max(Math.ceil(total.value / size), 1))
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})

function resetFilters() {
  filters.value = {}
  page.value = 1
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '血缘记录登记入口尚未接入审批流'
}

function openImpactView(batch?: string) {
  const value = (batch || store.batch || '').trim()
  if (value) store.setBatch(value)
  void router.push({ path: '/impact', query: value ? { batch: value } : {} })
}

function openBatch(row: Row) {
  const batch = String(row['操作批次'] || '').trim()
  if (!batch) {
    errorMessage.value = '该血缘记录没有操作批次，无法展开下游影响'
    return
  }
  openImpactView(batch)
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('湖仓血缘追踪动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '湖仓血缘追踪操作失败'
  }
}

async function loadBatches() {
  try {
    const payload = await fetchBatches()
    batches.value = payload.items
  } catch {
    batches.value = []
  }
}

async function changePage(step: number) {
  page.value = Math.min(Math.max(page.value + step, 1), totalPages.value)
  await reload()
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams({ page: String(page.value), size: String(size) })
  Object.entries(filters.value).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('血缘记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (payload.items?.length) {
      const batch = String(payload.items[0]['操作批次'] || '').trim()
      if (batch && !store.batch) store.setBatch(batch)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '湖仓血缘追踪列表读取失败'
  }
}

onMounted(async () => {
  await Promise.all([reload(), loadBatches()])
})
</script>
