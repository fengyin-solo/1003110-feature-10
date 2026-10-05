/** 冲击地压分级看板：与后端 /api/rockburst/board 等接口对应的只读类型。 */

export interface LevelMeta {
  code: string
  name: string
  index: number
  tone: string
}

export interface GradeRow {
  id: number
  station_id: number
  date: string
  level_code: string
  level_name: string
  level_index: number
  max_energy: number | null
  max_stress: number | null
  rule_id: number
  rule_version: string
  basis_note: string
  closed: boolean
  updated_at: string
}

export interface Station {
  id: number
  code: string
  name: string
  area: string
}

export interface ReliefRecord {
  id: number
  station_id: number
  station_code: string
  ts: string
  measure: string
  operator: string
}

export interface ReadingRecord {
  id: number
  station_id: number
  ts: string
  energy_j: number | null
  stress_mpa: number | null
  source: string
}

export interface AlertItem {
  id: number
  station_id: number
  station_code: string
  station_name: string
  area: string
  date: string
  from_level: string
  to_level: string
  from_index: number
  to_index: number
  reason: string
  rule_version: string
  acked: boolean
  acked_by: string | null
  acked_at: string | null
  created_at: string
}

export interface StationView {
  station: Station
  grade: GradeRow | null
  stale: boolean
  yesterday: GradeRow | null
  day_before: GradeRow | null
  raised: boolean
  last_relief: ReliefRecord | null
}

export interface AreaGroup {
  area: string
  max_level_index: number
  stations: StationView[]
}

export interface RuleVersion {
  id: number
  version: string
  name: string
  effective_date: string
  energy_thresholds: number[]
  stress_thresholds: number[]
  basis: string
  created_at: string
}

export interface BoardPayload {
  as_of: string
  closed_through: string | null
  next_close_day: string | null
  rule: RuleVersion
  levels: LevelMeta[]
  counts: Array<LevelMeta & { count: number }>
  areas: AreaGroup[]
  open_alert_count: number
  alerts: AlertItem[]
}

export interface StationDetail extends StationView {
  as_of: string
  history: GradeRow[]
  readings: ReadingRecord[]
  reliefs: ReliefRecord[]
  alerts: AlertItem[]
}

export interface DayItem {
  date: string
  closed: boolean
  closed_at: string | null
  rule_version: string
}

export interface CalendarPayload {
  as_of: string
  closed_through: string | null
  next_close_day: string | null
  days: DayItem[]
}
