import { defineStore } from 'pinia'

const STORAGE_KEY = 'lakehouse.impact.batch'

/**
 * 影响视图的批次上下文：血缘列表、回滚记录和影响复核工作台共用同一个操作批次，
 * 这样切换页面后看到的仍然是同一批下游表清单。
 */
export const useImpactStore = defineStore('impact', {
  state: () => ({
    batch: sessionStorage.getItem(STORAGE_KEY) ?? '',
    lastPublishedBatch: '',
  }),
  getters: {
    hasBatch: (state) => state.batch.trim().length > 0,
  },
  actions: {
    setBatch(batch: string) {
      this.batch = batch.trim()
      if (this.batch) {
        sessionStorage.setItem(STORAGE_KEY, this.batch)
      } else {
        sessionStorage.removeItem(STORAGE_KEY)
      }
    },
    markPublished(batch: string) {
      this.setBatch(batch)
      this.lastPublishedBatch = batch.trim()
    },
  },
})
