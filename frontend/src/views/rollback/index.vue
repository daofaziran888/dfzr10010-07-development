<template>
  <section class="page" data-module="rollback">
    <header class="page-head">
      <div>
        <h2>数据回滚恢复管理</h2>
        <p class="page-desc">维护数据回滚的版本追踪、影响评估与恢复计时，为数据回滚恢复提供可审计的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记回滚记录</button>
        <button class="btn" type="button" @click="exportRows">导出数据回滚恢复清单</button>
        <button class="btn" type="button" @click="openImpactView()">影响复核工作台</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>记录编号 / 表名</span>
        <input v-model="filters.keyword" placeholder="按记录编号或表名称检索" />
      </label>
      <label class="filter-item">
        <span>操作批次</span>
        <input v-model="filters.batch" placeholder="例如 20261115-04" />
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
          <td v-for="column in columns" :key="column" :class="{ 'mapping-cell': column === '受影响表' }">
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
            <button class="link" type="button" @click="bindImpact(row)">关联下游表清单</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据回滚恢复数据，可先登记回滚记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据回滚恢复记录 · 第 {{ page }} 页</span>
      <span class="pager">
        <button class="link" type="button" :disabled="page <= 1" @click="changePage(-1)">上一页</button>
        <button class="link" type="button" :disabled="page >= totalPages" @click="changePage(1)">下一页</button>
      </span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-if="successMessage" class="result-note">{{ successMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'
import { useImpactStore } from '@/stores/impact'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/rollback'
const columns = ["记录编号", "表名称", "回滚版本", "操作批次", "受影响表", "下游表数量", "清单同步状态", "回滚时间", "回滚状态"]
const actions = ["执行回滚", "确认恢复", "归档记录"]
const statuses = ["待回滚", "回滚中", "已回滚", "已恢复"]
const stats = [{"label": "回滚记录总数", "value": 0}, {"label": "回滚中记录", "value": 0}, {"label": "已回滚记录", "value": 0}]

const router = useRouter()
const store = useImpactStore()

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = 20
const totalPages = computed(() => Math.max(Math.ceil(total.value / size), 1))
const errorMessage = ref('')
const successMessage = ref('')
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
  errorMessage.value = '回滚记录登记入口尚未接入审批流'
}

function openImpactView(batch?: string) {
  const value = (batch || store.batch || '').trim()
  if (value) store.setBatch(value)
  void router.push({ path: '/impact', query: value ? { batch: value } : {} })
}

async function bindImpact(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  const batch = String(row['操作批次'] || store.batch || '').trim()
  if (!batch) {
    errorMessage.value = '该回滚记录没有操作批次，无法关联下游表清单'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/impact`, {
      method: 'POST',
      body: JSON.stringify({ values: { batch } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '回滚清单关联失败')
    }
    successMessage.value = `${payload.message}；下游 ${payload.entry?.回滚记录?.下游表数量 ?? 0} 张表`
    store.setBatch(batch)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '回滚清单关联失败'
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('数据回滚恢复动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复操作失败'
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
      throw new Error('回滚记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据回滚恢复列表读取失败'
  }
}

onMounted(reload)
</script>
