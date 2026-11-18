<template>
  <section class="impact-panel">
    <form class="filter-bar" @submit.prevent="load">
      <label class="filter-item">
        <span>操作批次</span>
        <input v-model="batchInput" placeholder="例如 20261115-03" />
      </label>
      <button class="btn primary" type="submit">计算影响面</button>
      <button class="btn ghost" type="button" @click="load">重新计算</button>
      <span v-if="result" class="impact-status" :class="{ published: published }">
        {{ published ? '已发布' : '预览（未发布）' }} · 批次 {{ result.操作批次 }}
      </span>
    </form>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
    <p v-if="publishMessage" class="result-note">{{ publishMessage }}</p>

    <template v-if="result">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">下游表总数</span>
          <strong class="stat-value">{{ result.下游表总数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">字段级路径</span>
          <strong class="stat-value">{{ result.字段路径总数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">有效血缘边</span>
          <strong class="stat-value">{{ result.有效血缘边数 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">影响结果份数</span>
          <strong class="stat-value">{{ result.唯一结果 ? 1 : result.下游表总数 }}</strong>
        </article>
      </div>

      <div class="result-note">
        <span>
          根表：<code v-for="root in result.根表" :key="root" class="tag">{{ root }}</code>
        </span>
        <span class="scope-check">
          影响面完整性：{{ result.唯一结果 ? '只包含一条完整图结果 ✓' : '存在多条结果，请检查' }}
        </span>
      </div>

      <div v-if="result.冲突信息?.存在冲突" class="alert warn">
        <strong>迁移与回滚版本冲突：{{ result.冲突信息.冲突表 }}</strong>
        <p>
          迁移记录 {{ result.冲突信息.迁移记录 }}（{{ result.冲突信息.迁移版本 }}，{{ result.冲突信息.迁移时间 }}）
          与回滚记录 {{ result.冲突信息.回滚记录 }}（{{ result.冲突信息.回滚版本 }}，{{ result.冲突信息.回滚时间 }}）
          对同一批下游版本给出不同依据。
        </p>
        <p>
          本次沿 <strong>{{ result.冲突信息.采用表链 }}</strong> 展开，{{ result.冲突信息.丢弃表链 }} 已整体排除：
          <code v-for="table in result.冲突信息.丢弃表" :key="table" class="tag muted">{{ table }}</code>
        </p>
        <p class="muted">{{ result.冲突信息.采用依据 }}</p>
      </div>

      <div v-if="result.跳过血缘.length" class="alert">
        <strong>以下血缘记录不参与传播（缺失一侧或字段映射时不做猜测）</strong>
        <table class="data-table compact">
          <thead>
            <tr><th>记录编号</th><th>上游表</th><th>下游表</th><th>跳过原因</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in result.跳过血缘" :key="item.记录编号">
              <td>{{ item.记录编号 }}</td>
              <td>{{ item.上游表名 }}</td>
              <td>{{ item.下游表名 }}</td>
              <td>{{ item.跳过原因 }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="panel-head">
        <h3>下游表清单</h3>
        <div class="page-actions">
          <button v-if="!published" class="btn primary" type="button" @click="publish">发布影响结果</button>
          <button class="btn" type="button" @click="goReview">切换到影响视图</button>
        </div>
      </div>

      <form class="filter-bar" @submit.prevent="searchTables">
        <label class="filter-item">
          <span>按表名检索</span>
          <input v-model="filters.keyword" placeholder="例如 ads_sales" />
        </label>
        <label class="filter-item">
          <span>按字段定位</span>
          <input v-model="filters.field" placeholder="例如 amount" />
        </label>
        <label class="filter-item">
          <span>排序</span>
          <select v-model="filters.sort">
            <option value="depth">按深度</option>
            <option value="name">按表名</option>
          </select>
        </label>
        <label class="filter-item">
          <span>方向</span>
          <select v-model="filters.order">
            <option value="asc">升序</option>
            <option value="desc">降序</option>
          </select>
        </label>
        <label class="filter-item">
          <span>每页</span>
          <select v-model.number="filters.size">
            <option :value="5">5</option>
            <option :value="10">10</option>
            <option :value="20">20</option>
          </select>
        </label>
        <button class="btn" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetTableFilters">重置</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>表名</th><th>深度</th><th>直接上游</th><th>字段映射</th>
            <th>路径起止版本</th><th>字段路径数</th><th>展开</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in tables" :key="row.表名">
            <tr>
              <td>{{ row.表名 }}</td>
              <td>{{ row.深度 }}</td>
              <td>{{ row.直接上游.join('、') }}</td>
              <td class="mapping-cell">{{ row.映射文本 }}</td>
              <td>{{ row.路径起止版本 }}</td>
              <td>{{ row.字段路径数 }}</td>
              <td>
                <button class="link" type="button" @click="toggle(row.表名)">
                  {{ expanded === row.表名 ? '收起' : '查看字段路径' }}
                </button>
              </td>
            </tr>
            <tr v-if="expanded === row.表名" class="path-row">
              <td colspan="7">
                <ul class="path-list">
                  <li v-for="path in row.完整字段路径" :key="path.路径编号">
                    <span class="path-text">{{ path.字段路径 }}</span>
                    <span class="path-version">{{ path.路径起止版本 }}</span>
                  </li>
                </ul>
              </td>
            </tr>
          </template>
          <tr v-if="!tables.length">
            <td colspan="7" class="empty-state">没有匹配的下游表，请调整表名或字段条件</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ tableTotal }} 张下游表 · 第 {{ tablePage }} 页</span>
        <span class="pager">
          <button class="link" type="button" :disabled="tablePage <= 1" @click="changePage(-1)">上一页</button>
          <button class="link" type="button" :disabled="tablePage >= totalPages" @click="changePage(1)">下一页</button>
        </span>
      </footer>

      <div class="panel-head">
        <h3>完整字段级路径</h3>
        <span class="muted">{{ pathTotal }} 条路径，包含起止版本与逐跳字段映射</span>
      </div>
      <form class="filter-bar" @submit.prevent="searchPaths">
        <label class="filter-item">
          <span>按下游表</span>
          <input v-model="pathFilters.table" placeholder="例如 ads_sales" />
        </label>
        <label class="filter-item">
          <span>按字段</span>
          <input v-model="pathFilters.field" placeholder="例如 order_id" />
        </label>
        <button class="btn" type="submit">检索路径</button>
        <button class="btn ghost" type="button" @click="resetPathFilters">重置</button>
      </form>
      <table class="data-table">
        <thead>
          <tr><th>字段路径</th><th>深度</th><th>起止版本</th><th>映射</th></tr>
        </thead>
        <tbody>
          <tr v-for="path in paths" :key="path.路径编号">
            <td class="path-text">{{ path.字段路径 }}</td>
            <td>{{ path.深度 }}</td>
            <td>{{ path.路径起止版本 }}</td>
            <td>{{ path.映射文本 }}</td>
          </tr>
          <tr v-if="!paths.length">
            <td colspan="4" class="empty-state">没有匹配的字段路径</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ pathTotal }} 条字段路径 · 第 {{ pathPage }} 页</span>
        <span class="pager">
          <button class="link" type="button" :disabled="pathPage <= 1" @click="changePathPage(-1)">上一页</button>
          <button class="link" type="button" :disabled="pathPage >= pathTotalPages" @click="changePathPage(1)">下一页</button>
        </span>
      </footer>
    </template>

    <p v-else-if="!errorMessage" class="empty-state">
      输入操作批次后查看下游表清单、字段映射与路径起止版本。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  fetchImpact,
  fetchPaths,
  fetchTables,
  publishImpact,
  type ImpactPath,
  type ImpactResult,
  type ImpactTable,
} from '@/api/impact'
import { useImpactStore } from '@/stores/impact'

const props = withDefaults(defineProps<{ initialBatch?: string }>(), { initialBatch: '' })
const emit = defineEmits<{ (event: 'published', batch: string): void }>()

const router = useRouter()
const store = useImpactStore()

const batchInput = ref(props.initialBatch || store.batch)
const result = ref<ImpactResult | null>(null)
const errorMessage = ref('')
const publishMessage = ref('')
const published = computed(() => result.value?.发布状态 === '已发布')

const tables = ref<ImpactTable[]>([])
const tableTotal = ref(0)
const tablePage = ref(1)
const filters = reactive({ keyword: '', field: '', sort: 'depth', order: 'asc', size: 10 })
const totalPages = computed(() => Math.max(Math.ceil(tableTotal.value / filters.size), 1))

const paths = ref<ImpactPath[]>([])
const pathTotal = ref(0)
const pathPage = ref(1)
const pathFilters = reactive({ table: '', field: '', size: 20 })
const pathTotalPages = computed(() => Math.max(Math.ceil(pathTotal.value / pathFilters.size), 1))
const expanded = ref('')

async function load() {
  const batch = batchInput.value.trim()
  errorMessage.value = ''
  if (!batch) {
    errorMessage.value = '请先输入操作批次'
    return
  }
  try {
    result.value = await fetchImpact(batch)
    store.setBatch(batch)
    expanded.value = ''
    tablePage.value = 1
    pathPage.value = 1
    await Promise.all([loadTables(), loadPaths()])
  } catch (error) {
    result.value = null
    tables.value = []
    paths.value = []
    errorMessage.value = error instanceof Error ? error.message : '影响结果读取失败'
  }
}

async function loadTables() {
  const batch = result.value?.操作批次
  if (!batch) return
  const payload = await fetchTables(batch, {
    keyword: filters.keyword,
    field: filters.field,
    sort: filters.sort,
    order: filters.order,
    page: tablePage.value,
    size: filters.size,
  })
  tables.value = payload.items
  tableTotal.value = payload.total
}

async function loadPaths() {
  const batch = result.value?.操作批次
  if (!batch) return
  const payload = await fetchPaths(batch, {
    table: pathFilters.table,
    field: pathFilters.field,
    page: pathPage.value,
    size: pathFilters.size,
  })
  paths.value = payload.items
  pathTotal.value = payload.total
}

async function searchTables() {
  tablePage.value = 1
  try {
    await loadTables()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '下游表检索失败'
  }
}

async function resetTableFilters() {
  filters.keyword = ''
  filters.field = ''
  filters.sort = 'depth'
  filters.order = 'asc'
  await searchTables()
}

async function searchPaths() {
  pathPage.value = 1
  try {
    await loadPaths()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段路径检索失败'
  }
}

async function resetPathFilters() {
  pathFilters.table = ''
  pathFilters.field = ''
  await searchPaths()
}

async function changePage(step: number) {
  tablePage.value = Math.min(Math.max(tablePage.value + step, 1), totalPages.value)
  await loadTables()
}

async function changePathPage(step: number) {
  pathPage.value = Math.min(Math.max(pathPage.value + step, 1), pathTotalPages.value)
  await loadPaths()
}

function toggle(table: string) {
  expanded.value = expanded.value === table ? '' : table
}

async function publish() {
  const batch = result.value?.操作批次
  if (!batch) return
  errorMessage.value = ''
  publishMessage.value = ''
  try {
    const payload = await publishImpact(batch)
    if (!payload.entry) {
      errorMessage.value = payload.message
      return
    }
    // 同批次已发布时后端会返回既有结果，这里同样展示，保证页面上只有一份清单
    result.value = payload.entry
    store.markPublished(batch)
    emit('published', batch)
    publishMessage.value = payload.message
    await loadTables()
    await loadPaths()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '影响结果发布失败'
  }
}

function goReview() {
  const batch = result.value?.操作批次 || batchInput.value.trim()
  if (batch) store.setBatch(batch)
  void router.push({ path: '/impact', query: batch ? { batch } : {} })
}

onMounted(async () => {
  if (batchInput.value) await load()
})
</script>
