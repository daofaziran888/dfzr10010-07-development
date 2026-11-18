/** 影响传播相关的接口封装：批次、下游表、字段路径、发布与复核。 */
import { fetchJson, request } from '@/api/client'

export type ImpactTable = {
  表名: string
  深度: number
  直接上游: string[]
  上游版本: string
  下游版本: string
  路径起止版本: string
  字段路径数: number
  映射文本: string
  血缘链: string[][]
  完整字段路径: ImpactPath[]
}

export type ImpactPath = {
  路径编号: string
  根表: string
  下游表: string
  深度: number
  血缘链: string[]
  字段路径: string
  起始字段: string
  结束字段: string
  起始版本: string
  结束版本: string
  路径起止版本: string
  映射文本: string
}

export type ImpactBatch = {
  操作批次: string
  血缘记录数: number
  有效血缘边数: number
  下游表总数: number
  字段路径总数: number
  发布状态: string
  复核状态: string
  存在冲突: boolean
  最近操作时间: string
}

export type ImpactConflict = {
  存在冲突: boolean
  冲突表?: string
  迁移记录?: string
  回滚记录?: string
  迁移版本?: string
  回滚版本?: string
  迁移时间?: string
  回滚时间?: string
  采用表链?: string
  丢弃表链?: string
  丢弃表?: string[]
  采用依据?: string
}

export type ImpactVersionBasis = {
  来源模块: string
  记录编号: string
  表名: string
  版本: string
  操作时间: string
  采用状态: string
  同步时间: string
}

export type ImpactResult = {
  操作批次: string
  发布状态: string
  发布时间: string
  根表: string[]
  下游表总数: number
  字段路径总数: number
  下游表清单: ImpactTable[]
  字段路径清单: ImpactPath[]
  跳过血缘: { 记录编号: string; 上游表名: string; 下游表名: string; 跳过原因: string }[]
  冲突信息: ImpactConflict
  版本依据: ImpactVersionBasis[]
  消费记录: { 模块: string; 记录编号: string; 表名: string; 版本: string; 同步时间: string }[]
  复核状态: string
  复核意见: string
  复核时间: string
  完整性说明: string
  唯一结果: boolean
  有效血缘边数: number
}

export type Page<T> = { items: T[]; total: number; page: number; size: number }

export function fetchBatches(keyword = '', page = 1, size = 100) {
  const query = new URLSearchParams({ page: String(page), size: String(size) })
  if (keyword) query.set('keyword', keyword)
  return fetchJson<Page<ImpactBatch>>(`/api/impact/batches?${query.toString()}`)
}

export function fetchImpact(batch: string, preview = true) {
  const query = new URLSearchParams({ preview: String(preview) })
  return fetchJson<ImpactResult>(`/api/impact/records/${encodeURIComponent(batch)}?${query.toString()}`)
}

export function fetchTables(
  batch: string,
  params: { keyword?: string; field?: string; sort?: string; order?: string; page?: number; size?: number },
) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') query.set(key, String(value))
  })
  return fetchJson<Page<ImpactTable>>(
    `/api/impact/records/${encodeURIComponent(batch)}/tables?${query.toString()}`,
  )
}

export function fetchPaths(
  batch: string,
  params: { table?: string; field?: string; page?: number; size?: number },
) {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== '') query.set(key, String(value))
  })
  return fetchJson<Page<ImpactPath>>(
    `/api/impact/records/${encodeURIComponent(batch)}/paths?${query.toString()}`,
  )
}

export async function publishImpact(batch: string) {
  const response = await request(`/api/impact/records/${encodeURIComponent(batch)}/publish`, {
    method: 'POST',
  })
  return (await response.json()) as { ok: boolean; message: string; entry: ImpactResult | null }
}

export async function reviewImpact(batch: string, action: string, remark = '') {
  const response = await request(`/api/impact/records/${encodeURIComponent(batch)}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action, remark } }),
  })
  return (await response.json()) as { ok: boolean; message: string; entry: ImpactResult | null }
}
