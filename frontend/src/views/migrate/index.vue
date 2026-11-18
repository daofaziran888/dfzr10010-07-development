<template>
  <section class="page" data-module="migrate">
    <header class="page-head">
      <div>
        <h2>数据迁移管理</h2>
        <p class="page-desc">维护数据迁移的源目标配置、迁移方式与校验规则，为数据迁移管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记迁移任务</button>
        <button class="btn" type="button" @click="exportRows">导出数据迁移管理清单</button>
        <button class="btn" type="button" @click="openImpactView">影响复核工作台</button>
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
        <span>任务编号 / 表名</span>
        <input v-model="filters.keyword" placeholder="按任务编号、源表名或目标表名检索" />
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
            <button class="link" type="button" @click="syncImpact(row)">同步版本依据</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无数据迁移管理数据，可先登记迁移任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条数据迁移管理记录 · 第 {{ page }} 页</span>
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

const ENDPOINT = '/api/migrate'
const columns = ["任务编号", "源表名", "目标表名", "迁移方式", "操作批次", "源版本", "目标版本", "版本依据", "影响清单同步", "任务状态"]
const actions = ["执行迁移", "回滚迁移", "归档任务"]
const statuses = ["待执行", "迁移中", "已完成", "已回滚"]
const stats = [{"label": "迁移任务总数", "value": 0}, {"label": "迁移中任务", "value": 0}, {"label": "已完成任务", "value": 0}]

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
  errorMessage.value = '迁移任务登记入口尚未接入审批流'
}

function openImpactView() {
  const value = (store.batch || '').trim()
  void router.push({ path: '/impact', query: value ? { batch: value } : {} })
}

async function syncImpact(row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  const batch = String(row['操作批次'] || store.batch || '').trim()
  if (!batch) {
    errorMessage.value = '该迁移任务没有操作批次，无法同步版本依据'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/impact`, {
      method: 'POST',
      body: JSON.stringify({ values: { batch } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '版本依据同步失败')
    }
    successMessage.value = payload.message
    store.setBatch(batch)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本依据同步失败'
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
      throw new Error('数据迁移管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据迁移管理操作失败'
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
      throw new Error('迁移任务列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '数据迁移管理列表读取失败'
  }
}

onMounted(reload)
</script>
