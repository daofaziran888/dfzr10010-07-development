<template>
  <section class="page" data-module="impact">
    <header class="page-head">
      <div>
        <h2>影响复核工作台</h2>
        <p class="page-desc">
          按操作批次查看发布后的下游表清单、字段级路径与路径起止版本，并核对迁移记录、回滚记录及消费模块同步的版本依据。
        </p>
      </div>
      <div class="page-actions">
        <input v-model="batchQuery" class="batch-input" placeholder="操作批次，例如 20261115-03" />
        <button class="btn primary" type="button" @click="selectBatch(batchQuery)">查看批次</button>
        <button class="btn" type="button" @click="exportResult">导出影响结果</button>
      </div>
    </header>

    <div v-if="batches.length" class="batch-list">
      <button
        v-for="item in batches"
        :key="item.操作批次"
        class="batch-chip"
        :class="{ active: item.操作批次 === activeBatch }"
        type="button"
        @click="selectBatch(item.操作批次)"
      >
        <span class="chip-batch">{{ item.操作批次 }}</span>
        <span class="chip-meta">
          {{ item.下游表总数 }} 表 · {{ item.发布状态 }}
          <em v-if="item.存在冲突">版本冲突</em>
        </span>
      </button>
    </div>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <ImpactPanel v-if="activeBatch" :key="activeBatch" :initial-batch="activeBatch" @published="onPublished" />

    <template v-if="result">
      <div class="panel-head">
        <h3>影响复核</h3>
        <span class="muted">
          复核状态：<strong>{{ result.复核状态 || '待复核' }}</strong>
          <template v-if="result.复核时间">· {{ result.复核时间 }}</template>
        </span>
      </div>
      <form class="filter-bar" @submit.prevent="submitReview('确认复核')">
        <label class="filter-item wide">
          <span>复核意见</span>
          <input v-model="reviewRemark" placeholder="说明影响面是否与预期一致" />
        </label>
        <button class="btn primary" type="submit" :disabled="!published">确认复核</button>
        <button class="btn" type="button" :disabled="!published" @click="submitReview('退回复核')">退回复核</button>
      </form>
      <p v-if="!published" class="muted">影响结果尚未发布，先在上方发布后再复核。</p>
      <p v-if="reviewMessage" class="result-note">{{ reviewMessage }}</p>
      <p v-if="result.复核意见" class="muted">上次意见：{{ result.复核意见 }}</p>

      <div class="panel-head">
        <h3>版本依据</h3>
        <span class="muted">迁移记录、回滚记录与其它消费模块同步的版本口径，均属于同一操作批次</span>
      </div>
      <table class="data-table">
        <thead>
          <tr><th>来源模块</th><th>记录编号</th><th>表名</th><th>版本</th><th>采用状态</th><th>同步时间</th></tr>
        </thead>
        <tbody>
          <tr v-for="item in result.版本依据" :key="`${item.来源模块}-${item.记录编号}-${item.表名}-${item.版本}`">
            <td>{{ item.来源模块 }}</td>
            <td>{{ item.记录编号 || '—' }}</td>
            <td>{{ item.表名 }}</td>
            <td>{{ item.版本 }}</td>
            <td>{{ item.采用状态 }}</td>
            <td>{{ item.同步时间 }}</td>
          </tr>
          <tr v-if="!result.版本依据.length">
            <td colspan="6" class="empty-state">该批次暂无版本依据，可在迁移或回滚记录上同步。</td>
          </tr>
        </tbody>
      </table>
    </template>

    <p v-else-if="!activeBatch" class="empty-state">
      选择或输入操作批次后查看完整字段级路径；批次 20261115-03 中 ods_orders 经 dws_order_daily 到 ads_sales 有 order_id、amount、dt 三条字段级路径。
    </p>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ImpactPanel from '@/components/ImpactPanel.vue'
import { fetchBatches, fetchImpact, reviewImpact, type ImpactBatch, type ImpactResult } from '@/api/impact'
import { useImpactStore } from '@/stores/impact'

const route = useRoute()
const router = useRouter()
const store = useImpactStore()

const batches = ref<ImpactBatch[]>([])
const activeBatch = ref('')
const batchQuery = ref('')
const result = ref<ImpactResult | null>(null)
const errorMessage = ref('')
const reviewRemark = ref('')
const reviewMessage = ref('')
const published = ref(false)

async function loadBatches() {
  try {
    const payload = await fetchBatches()
    batches.value = payload.items
  } catch {
    batches.value = []
  }
}

async function selectBatch(batch: string) {
  const value = batch.trim()
  if (!value) {
    errorMessage.value = '请输入操作批次'
    return
  }
  errorMessage.value = ''
  reviewMessage.value = ''
  activeBatch.value = value
  batchQuery.value = value
  store.setBatch(value)
  if (route.query.batch !== value) {
    void router.replace({ path: '/impact', query: { batch: value } })
  }
  try {
    result.value = await fetchImpact(value)
    published.value = result.value.发布状态 === '已发布'
    reviewRemark.value = ''
  } catch (error) {
    result.value = null
    published.value = false
    errorMessage.value = error instanceof Error ? error.message : '影响结果读取失败'
  }
  await loadBatches()
}

async function onPublished(batch: string) {
  await selectBatch(batch)
}

async function submitReview(action: string) {
  if (!result.value) return
  reviewMessage.value = ''
  try {
    const payload = await reviewImpact(result.value.操作批次, action, reviewRemark.value)
    reviewMessage.value = payload.message
    if (payload.entry) {
      result.value = payload.entry
      published.value = true
    }
    await loadBatches()
  } catch (error) {
    reviewMessage.value = error instanceof Error ? error.message : '复核动作失败'
  }
}

function exportResult() {
  if (!result.value) return
  window.open(`/api/impact/export?batch=${encodeURIComponent(result.value.操作批次)}`, '_blank')
}

onMounted(async () => {
  await loadBatches()
  const initial = String(route.query.batch || store.batch || '').trim()
  if (initial) {
    await selectBatch(initial)
  }
})
</script>
