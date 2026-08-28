"use client"

import { useState, useMemo, useEffect, useCallback } from "react"
import { motion } from "framer-motion"
import {
  ShieldAlert,
  ShieldCheck,
  Lock,
  User,
  Power,
  RefreshCw,
  Terminal,
  BrainCircuit,
  GraduationCap,
  Activity,
  LogOut,
  AlertCircle,
  CheckCircle2,
  Database,
  Trash2,
  AlertTriangle,
  Wallet,
  Ban,
  FlaskConical,
  Download,
  FileJson,
  ShieldOff,
  Cpu,
  MemoryStick,
  HardDrive,
  CircuitBoard,
  ChevronDown,
} from "lucide-react"
import { HeaderNav } from "@/components/HeaderNav"
import { QualityTrendChart } from "@/components/QualityTrendChart"
import { TrainingTrendChart } from "@/components/TrainingTrendChart"
import { SPORT_NAME_ORDER, UNIVERSE_SPORT_IDS, universeSportOptions } from "@/lib/sports"
import { SportName } from "@/components/SportIcon"
import {
  MARKET_BACKTEST_ALIASES,
  UNIVERSE_MARKET_IDS,
  UNIVERSE_MARKET_OPTIONS,
} from "@/lib/markets"
import {
  adminAuthHeaders,
  clearAdminSession,
  isAdminLoggedIn,
  migrateAdminSessionStorage,
  setAdminSession,
} from "@/lib/adminAuth"

interface AILog {
  timestamp: string
  category: string
  level: string
  message: string
}

function sortBacktestSportRows<T>(rows: T[], getName: (row: T) => string): T[] {
  return [...rows].sort((a, b) => {
    const ai = SPORT_NAME_ORDER.findIndex((n) => n.toLowerCase() === String(getName(a)).toLowerCase())
    const bi = SPORT_NAME_ORDER.findIndex((n) => n.toLowerCase() === String(getName(b)).toLowerCase())
    if (ai === -1 && bi === -1) return String(getName(a)).localeCompare(String(getName(b)), "ru")
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}

function BacktestSliceTable({
  title,
  rows,
  nameHeader,
  nameOf,
}: {
  title: string
  rows: any[] | undefined
  nameHeader: string
  nameOf: (row: any) => string
}) {
  const list = sortBacktestSportRows(
    (Array.isArray(rows) ? rows : []).filter(Boolean),
    nameOf,
  )
  if (!list.length) return null
  return (
    <div>
      <div className="text-[10px] text-neutral-500 uppercase font-mono mb-1.5">{title}</div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono min-w-[560px]">
          <thead>
            <tr className="text-neutral-500 text-left border-b border-neutral-800">
              <th className="py-1.5 pr-3 font-semibold">{nameHeader}</th>
              <th className="py-1.5 pr-3 font-semibold">Оценено</th>
              <th className="py-1.5 pr-3 font-semibold">Ставок</th>
              <th className="py-1.5 pr-3 font-semibold">ROI (текущ.)</th>
              <th className="py-1.5 pr-3 font-semibold">Brier (текущ.)</th>
              <th className="py-1.5 pr-3 font-semibold">Brier (рынок)</th>
            </tr>
          </thead>
          <tbody>
            {list.map((row: any, i: number) => (
              <tr key={nameOf(row) || i} className="border-b border-neutral-900/60">
                <td className="py-1.5 pr-3 text-neutral-200"><SportName sport={nameOf(row)} /></td>
                <td className="py-1.5 pr-3 text-neutral-400">{row.evaluated ?? "—"}</td>
                <td className="py-1.5 pr-3 text-neutral-400">{row.current?.bets ?? "—"}</td>
                <td className={`py-1.5 pr-3 font-bold ${
                  row.current?.roi_pct == null ? "text-neutral-500" : row.current.roi_pct >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"
                }`}>
                  {row.current?.roi_pct != null ? `${row.current.roi_pct}%` : "—"}
                </td>
                <td className="py-1.5 pr-3 text-neutral-400">{row.current?.brier ?? "—"}</td>
                <td className={`py-1.5 pr-3 ${
                  row.current?.brier != null && row.market_brier != null && row.current.brier >= row.market_brier
                    ? "text-[#ff7675]" : "text-neutral-500"
                }`}>
                  {row.market_brier ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

type SliceKpis = {
  roi: number | null
  ciLo: number | null
  wr: number | null
  bets: number | null
  source: "live" | "full" | null
}

function asFiniteNumber(v: unknown): number | null {
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

function stakeCurrentOf(row: any): any {
  if (!row) return {}
  return row.stake_policy?.current || row.current || row
}

function kpisFromBacktestRow(row: any): Omit<SliceKpis, "source"> {
  const s = stakeCurrentOf(row)
  return {
    roi: asFiniteNumber(s.roi_pct),
    ciLo: asFiniteNumber(s.roi_pct_lo ?? s.roi_ci_lo),
    wr: asFiniteNumber(s.win_rate_pct),
    bets: asFiniteNumber(s.bets),
  }
}

function findNamedBacktestRow(rows: any[] | undefined, aliases: string[], nameKeys: string[]) {
  const want = new Set(aliases.map((a) => a.toLowerCase()))
  return (Array.isArray(rows) ? rows : []).find((row) => {
    const name = nameKeys.map((k) => row?.[k]).find((v) => v != null && String(v).trim() !== "")
    return want.has(String(name || "").toLowerCase())
  })
}

function firstBacktestRow(bt: any, listKeys: string[], aliases: string[], nameKeys: string[]) {
  if (!bt) return null
  for (const listKey of listKeys) {
    const row = findNamedBacktestRow(bt[listKey], aliases, nameKeys)
    if (row) return row
  }
  return null
}

function resolveSliceKpis(
  liveBt: any,
  fullBt: any,
  aliases: string[],
  listKeys: string[],
  nameKeys: string[],
): SliceKpis {
  const liveRow = firstBacktestRow(liveBt, listKeys, aliases, nameKeys)
  if (liveRow) return { ...kpisFromBacktestRow(liveRow), source: "live" }
  const fullRow = firstBacktestRow(fullBt, listKeys, aliases, nameKeys)
  if (fullRow) return { ...kpisFromBacktestRow(fullRow), source: "full" }
  return { roi: null, ciLo: null, wr: null, bets: null, source: null }
}

function formatSignedPct(v: number | null): string {
  if (v == null) return "—"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(1)}%`
}

function SliceKpiLine({ kpis }: { kpis: SliceKpis }) {
  if (!kpis.source && kpis.roi == null && kpis.wr == null) {
    return <div className="text-[10px] font-mono text-neutral-600 mt-0.5">нет данных бэктеста</div>
  }
  const roiCls = kpis.roi == null ? "text-neutral-500" : kpis.roi >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"
  const ciCls = kpis.ciLo == null ? "text-neutral-500" : kpis.ciLo > 0 ? "text-[#55efc4]" : "text-neutral-400"
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] font-mono mt-0.5">
      <span className={roiCls}>ROI {formatSignedPct(kpis.roi)}</span>
      <span className={ciCls} title="Нижняя граница 95% CI ROI">CI {formatSignedPct(kpis.ciLo)}</span>
      <span className="text-neutral-400">WR {kpis.wr == null ? "—" : `${kpis.wr.toFixed(1)}%`}</span>
      {kpis.source === "full" && (
        <span className="text-neutral-600" title="Среза нет в live-бэктесте — цифры из полного прогона">полный</span>
      )}
    </div>
  )
}

function formatBytes(n: number | null | undefined): string {
  const bytes = Number(n || 0)
  if (!Number.isFinite(bytes) || bytes < 0) return "0 Б"
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  const digits = i === 0 || v >= 10 ? 0 : 1
  return `${v.toFixed(digits)} ${units[i]}`
}

function loadTone(pct: number | null | undefined): { bar: string; text: string; border: string; bg: string } {
  const p = Number(pct || 0)
  if (p >= 90) {
    return { bar: "bg-[#d63031]", text: "text-[#ff7675]", border: "border-[#d63031]/50", bg: "bg-[#d63031]/10" }
  }
  if (p >= 75) {
    return { bar: "bg-[#fdcb6e]", text: "text-[#ffeaa7]", border: "border-[#fdcb6e]/40", bg: "bg-[#fdcb6e]/10" }
  }
  return { bar: "bg-[#00b894]", text: "text-[#55efc4]", border: "border-neutral-800", bg: "bg-neutral-900/80" }
}

function HardwareMeter({
  icon: Icon,
  label,
  percent,
  value,
  detail,
  unavailable,
}: {
  icon: any
  label: string
  percent: number | null
  value: string
  detail: string
  unavailable?: boolean
}) {
  const tone = unavailable ? {
    bar: "bg-neutral-700", text: "text-neutral-400", border: "border-neutral-800", bg: "bg-neutral-900/80",
  } : loadTone(percent)
  const width = Math.max(0, Math.min(100, Number(percent || 0)))
  return (
    <div className={`rounded-2xl border p-4 backdrop-blur-md shadow-lg ${tone.bg} ${tone.border}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className={`w-9 h-9 rounded-xl border flex items-center justify-center shrink-0 ${tone.border} bg-neutral-950/60`}>
            <Icon className={`w-4 h-4 ${tone.text}`} />
          </div>
          <div className="min-w-0">
            <div className="text-[10px] text-neutral-500 font-mono uppercase leading-none">{label}</div>
            <div className={`text-lg font-black font-mono mt-1 leading-none ${unavailable ? "text-neutral-500" : "text-white"}`}>
              {value}
            </div>
          </div>
        </div>
        {!unavailable && percent != null && (
          <div className={`text-sm font-bold font-mono ${tone.text}`}>{percent.toFixed(0)}%</div>
        )}
      </div>
      <div className="mt-3 h-1.5 rounded-full bg-neutral-950/80 overflow-hidden border border-neutral-800/80">
        <div className={`h-full rounded-full ${tone.bar}`} style={{ width: unavailable ? "0%" : `${width}%` }} />
      </div>
      <p className="text-[10px] text-neutral-500 font-mono mt-2 leading-snug">{detail}</p>
    </div>
  )
}

export default function AdminPage() {
  const [isAuthenticated, setIsAuthenticated] = useState(false)
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loginLoading, setLoginLoading] = useState(false)

  // AI Settings & Logs State
  const [aiEnabled, setAiEnabled] = useState(true)
  const [trainingEnabled, setTrainingEnabled] = useState(true)
  const [qualityGateBypass, setQualityGateBypass] = useState(false)
  const [enabledSports, setEnabledSports] = useState<string[]>([...UNIVERSE_SPORT_IDS])
  const [sportsPanelOpen, setSportsPanelOpen] = useState(false)
  const [enabledMarkets, setEnabledMarkets] = useState<string[]>([...UNIVERSE_MARKET_IDS])
  const [marketsPanelOpen, setMarketsPanelOpen] = useState(false)
  const [liveBacktestSnap, setLiveBacktestSnap] = useState<any>(null)
  const [fullBacktestSnap, setFullBacktestSnap] = useState<any>(null)
  const [logs, setLogs] = useState<AILog[]>([])
  const [logFilter, setLogFilter] = useState<string>("ALL")
  const [triggering, setTriggering] = useState(false)
  const [stats, setStats] = useState<any>(null)
  const [bankroll, setBankroll] = useState<any>(null)

  // Reset Confirmation State
  const [resetModalOpen, setResetModalOpen] = useState(false)
  const [resetType, setResetType] = useState<"live" | "all" | "bankroll-live" | "bankroll-training" | "cancel-bets" | "reset-model" | null>(null)
  const [resetLoading, setResetLoading] = useState(false)
  const [resetSuccessMsg, setResetSuccessMsg] = useState<string | null>(null)
  const [openLiveBetsCount, setOpenLiveBetsCount] = useState(0)
  const [resetProgress, setResetProgress] = useState<{ pct: number; label: string; step: string; active: boolean }>({
    pct: 0, label: "", step: "idle", active: false,
  })

  // Training Health State (overfitting traffic light)
  const [trainingHealth, setTrainingHealth] = useState<any>(null)
  const [trainingRuns, setTrainingRuns] = useState<any[]>([])

  // Backtest State
  const BACKTEST_LIMIT = 80000
  const [backtestRunning, setBacktestRunning] = useState(false)
  const [backtestResult, setBacktestResult] = useState<any>(null)
  const [backtestError, setBacktestError] = useState<string | null>(null)
  const [backtestHistory, setBacktestHistory] = useState<any[]>([])
  const [backtestProgress, setBacktestProgress] = useState<{ pct: number; label: string; step: string; active: boolean; processed: number; total: number }>({
    pct: 0, label: "", step: "idle", active: false, processed: 0, total: 0,
  })
  const [evalPackLoading, setEvalPackLoading] = useState(false)
  const [evalPackError, setEvalPackError] = useState<string | null>(null)
  const [hardware, setHardware] = useState<any>(null)

  // See app/neurobets/page.tsx for why this defaults to "" (same-origin, proxied by
  // next.config.ts) instead of an absolute localhost URL.
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""

  const handleOpenResetModal = (type: "live" | "all" | "bankroll-live" | "bankroll-training" | "cancel-bets" | "reset-model") => {
    setResetType(type)
    setResetProgress({ pct: 0, label: "", step: "idle", active: false })
    setResetModalOpen(true)
  }

  const fetchResetProgress = async () => {
    const res = await fetch(`${API_BASE}/api/admin/reset-progress`, { cache: "no-store" })
    if (!res.ok) return null
    const data = await res.json()
    const p = data.progress || data
    const next = {
      pct: Math.max(0, Math.min(100, Number(p.pct) || 0)),
      label: String(p.label || ""),
      step: String(p.step || "idle"),
      active: Boolean(p.active),
    }
    setResetProgress(next)
    return next
  }

  const handleConfirmReset = async () => {
    if (!resetType) return
    setResetLoading(true)
    setResetSuccessMsg(null)

    try {
      if (resetType === "cancel-bets") {
        const res = await fetch(`${API_BASE}/api/admin/live-bets/cancel-all`, {
          method: "POST",
          headers: adminAuthHeaders(),
        })
        if (!res.ok) throw new Error("Ошибка при отмене ставок")
        const data = await res.json()
        setResetSuccessMsg(data.message || "Ставки отменены")
        setTimeout(() => { fetchBankroll(); fetchOpenLiveBetsCount() }, 300)
      } else if (resetType === "reset-model") {
        setResetProgress({ pct: 1, label: "Начинаю обнуление…", step: "starting", active: true })
        let poll = 0
        try {
          poll = window.setInterval(() => {
            fetchResetProgress().catch(() => {})
          }, 400)

          let postData: { reset_rows?: number; status?: string } | null = null
          try {
            const res = await fetch(`${API_BASE}/api/admin/reset-model`, { method: "POST" })
            if (res.ok) {
              postData = await res.json()
            }
          } catch {
            // 504/network: wipe may still be running — keep polling the progress file.
          }

          let latest: { pct: number; label: string; step: string; active: boolean } | null = null
          let sawThisRun = Boolean(postData?.status === "success")
          if (!postData) {
            const deadline = Date.now() + 180_000
            while (Date.now() < deadline) {
              latest = await fetchResetProgress().catch(() => latest)
              if (latest) {
                if (["starting", "waiting_lock", "wiping", "charts", "archive", "bankroll", "cold_start"].includes(latest.step)) {
                  sawThisRun = true
                }
                if (sawThisRun && latest.step === "error") {
                  throw new Error(latest.label || "Ошибка при обнулении нейросети")
                }
                if (sawThisRun && latest.step === "done") break
              }
              await new Promise((r) => setTimeout(r, 400))
            }
          }

          if (!(postData?.status === "success" || (sawThisRun && latest?.step === "done"))) {
            throw new Error("Ошибка при обнулении нейросети")
          }

          setResetProgress({ pct: 100, label: "Готово", step: "done", active: false })
          const rows = postData?.reset_rows
          setResetSuccessMsg(
            rows != null
              ? `Нейросеть обнулена. Очищено trained_count у ${rows} завершённых ставок. Графики обучения/бэктеста и оба банка сброшены — cold-start по архиву запущен (live-ставки возобновятся после него).`
              : "Нейросеть обнулена. Cold-start по архиву запущен — live-ставки возобновятся после завершения cold-start."
          )
          await new Promise((r) => setTimeout(r, 700))
          setTimeout(() => {
            fetchAILogs()
            fetchAISettings()
            fetchBankroll()
            fetchOpenLiveBetsCount()
            fetchTrainingRuns()
            fetchBacktestHistory()
            fetchTrainingHealth()
          }, 300)
        } finally {
          if (poll) window.clearInterval(poll)
        }
      } else if (resetType === "bankroll-live" || resetType === "bankroll-training") {
        const account = resetType === "bankroll-live" ? "live" : "training"
        const res = await fetch(`${API_BASE}/api/admin/bankroll/reset`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account })
        })
        if (!res.ok) throw new Error("Ошибка при сбросе банка")
        setResetSuccessMsg(
          account === "live" ? "Боевой банк сброшен до 1000 ₽" : "Обучающий банк сброшен до 1000 ₽"
        )
        setTimeout(fetchBankroll, 300)
      } else {
        const endpoint = resetType === "live" ? `${API_BASE}/api/admin/reset-db/live` : `${API_BASE}/api/admin/reset-db/all`
        const res = await fetch(endpoint, { method: "POST" })
        if (!res.ok) throw new Error("Ошибка при обнулении БД")
        const data = await res.json()
        setResetSuccessMsg(data.message || "БД успешно обнулена")
        setTimeout(() => {
          fetchStats()
          fetchAILogs()
          fetchBankroll()
        }, 500)
      }
    } catch (err: any) {
      alert(err.message || "Ошибка обнуления")
    } finally {
      setResetLoading(false)
      setResetModalOpen(false)
    }
  }

  // Check existing session (persists in localStorage across browser restarts)
  useEffect(() => {
    migrateAdminSessionStorage()
    if (isAdminLoggedIn()) {
      setIsAuthenticated(true)
    }
  }, [])

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoginLoading(true)
    setLoginError(null)

    try {
      const res = await fetch(`${API_BASE}/api/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password })
      })

      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || "Ошибка авторизации")
      }

      setAdminSession()
      setIsAuthenticated(true)
    } catch (err: any) {
      setLoginError(err.message || "Неверное имя пользователя или пароль")
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLogout = () => {
    clearAdminSession()
    setIsAuthenticated(false)
  }

  const fetchAISettings = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/ai-settings`)
      if (res.ok) {
        const data = await res.json()
        if (data.settings) {
          setAiEnabled(data.settings.ai_enabled)
          setTrainingEnabled(data.settings.training_enabled)
          setQualityGateBypass(Boolean(data.settings.quality_gate_bypass))
          if (Array.isArray(data.settings.enabled_sports)) {
            setEnabledSports(data.settings.enabled_sports.map((s: string) => String(s).toLowerCase()))
          }
          if (Array.isArray(data.settings.enabled_markets)) {
            setEnabledMarkets(data.settings.enabled_markets.map((m: string) => String(m).toLowerCase()))
          }
        }
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchAILogs = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/ai-logs`)
      if (res.ok) {
        const data = await res.json()
        const next = Array.isArray(data.logs) ? data.logs : null
        if (!next) return
        // Keep the last snapshot if the proxy timed out on a busy training pass
        // and returned an empty placeholder — wiping the console looks "frozen".
        setLogs((prev) => (next.length > 0 || prev.length === 0 ? next : prev))
      }
    } catch (err) {
      // Ignore — keep last snapshot
    }
  }, [API_BASE])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/db-overview`, { cache: "no-store" })
      if (res.ok) {
        const data = await res.json()
        setStats(data.stats)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchBankroll = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/neurobets/bankroll`)
      if (res.ok) {
        const data = await res.json()
        setBankroll(data)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchTrainingHealth = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/training-health`)
      if (res.ok) {
        const data = await res.json()
        setTrainingHealth(data.health || null)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchHardware = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/hardware`, { cache: "no-store" })
      if (res.ok) {
        const data = await res.json()
        setHardware(data)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchTrainingRuns = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/training-runs`)
      if (res.ok) {
        const data = await res.json()
        setTrainingRuns(data.runs || [])
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchOpenLiveBetsCount = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/neurobets/live-bets?status=open&limit=1`)
      if (res.ok) {
        const data = await res.json()
        setOpenLiveBetsCount(typeof data.total === "number" ? data.total : 0)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchBacktestProgress = async () => {
    const res = await fetch(`${API_BASE}/api/admin/backtest/progress`, { cache: "no-store" })
    if (!res.ok) return null
    const data = await res.json()
    const p = data.progress || data
    const next = {
      pct: Math.max(0, Math.min(100, Number(p.pct) || 0)),
      label: String(p.label || ""),
      step: String(p.step || "idle"),
      active: Boolean(p.active),
      processed: Math.max(0, Number(p.processed) || 0),
      total: Math.max(0, Number(p.total) || 0),
    }
    setBacktestProgress(next)
    return next
  }

  const fetchBacktestHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/admin/backtest/history`)
      if (res.ok) {
        const data = await res.json()
        setBacktestHistory(data.runs || [])
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchBacktestSlices = useCallback(async () => {
    const load = async (mode: "live" | "full") => {
      const res = await fetch(`${API_BASE}/api/admin/backtest/latest?mode=${mode}`, { cache: "no-store" })
      if (!res.ok) return null
      const data = await res.json()
      return data.backtest || null
    }
    try {
      const [live, full] = await Promise.all([load("live"), load("full")])
      if (live) setLiveBacktestSnap(live)
      if (full) setFullBacktestSnap(full)
    } catch {
      // Keep last snapshot
    }
  }, [API_BASE])

  const handleRunBacktest = async (mode: "live" | "full" = "live") => {
    setBacktestRunning(true)
    setBacktestError(null)
    setBacktestProgress({ pct: 2, label: "Отправляю запрос…", step: "request", active: true, processed: 0, total: BACKTEST_LIMIT })
    let poll = 0
    try {
      poll = window.setInterval(() => {
        fetchBacktestProgress().catch(() => {})
      }, 400)

      let postData: any = null
      try {
        const res = await fetch(`${API_BASE}/api/admin/backtest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ limit: BACKTEST_LIMIT, mode }),
        })
        if (res.ok) {
          postData = await res.json()
        }
      } catch {
        // 504/network: backtest may still be running — keep polling the progress file.
      }

      if (postData?.status === "success") {
        setBacktestResult(postData)
        setBacktestProgress({ pct: 100, label: "Готово", step: "done", active: false, processed: postData.samples_evaluated || 0, total: postData.samples_requested || BACKTEST_LIMIT })
        if (mode === "live") fetchBacktestHistory()
        fetchBacktestSlices()
        return
      }
      if (postData?.status === "no_data") throw new Error("Недостаточно завершённых ставок для бэктеста")
      if (postData?.status === "skipped_cold_start") {
        throw new Error("Идёт cold-start — бэктест запустится, когда walk закончится")
      }
      if (postData && postData.status !== "success") throw new Error("Бэктест завершился с ошибкой")

      let latest: { pct: number; label: string; step: string; active: boolean; processed: number; total: number } | null = null
      let started = false
      const deadline = Date.now() + 600_000
      while (Date.now() < deadline) {
        latest = await fetchBacktestProgress().catch(() => latest)
        if (latest && !["idle", "done"].includes(latest.step)) {
          started = true
        }
        if (latest?.step === "error") {
          throw new Error(latest.label || "Ошибка при запуске бэктеста")
        }
        if (latest?.step === "done" || (started && (latest?.pct ?? 0) >= 100)) {
          break
        }
        await new Promise((r) => setTimeout(r, 400))
      }

      if (latest?.step === "done" || (started && (latest?.pct ?? 0) >= 100)) {
        const res = await fetch(`${API_BASE}/api/admin/backtest/latest?mode=${mode}`, { cache: "no-store" })
        if (!res.ok) throw new Error("Бэктест завершился, но результат не удалось загрузить")
        const data = await res.json()
        const bt = data.backtest
        if (bt?.status === "success") {
          setBacktestResult(bt)
          setBacktestProgress({ pct: 100, label: "Готово", step: "done", active: false, processed: bt.samples_evaluated || 0, total: bt.samples_requested || BACKTEST_LIMIT })
          if (mode === "live") fetchBacktestHistory()
          fetchBacktestSlices()
          return
        }
        throw new Error("Бэктест завершился, но результат не найден")
      }

      throw new Error("Превышено время ожидания бэктеста (10 мин)")
    } catch (err: any) {
      setBacktestError(err.message || "Ошибка при запуске бэктеста")
    } finally {
      if (poll) window.clearInterval(poll)
      setBacktestRunning(false)
    }
  }

  const downloadBacktestJson = () => {
    if (!backtestResult) return
    const blob = new Blob([JSON.stringify(backtestResult, null, 2)], { type: "application/json" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `backtest_${(backtestResult.generated_at || Date.now().toString()).replace(/[:.]/g, "-")}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const downloadEvalPack = async () => {
    setEvalPackLoading(true)
    setEvalPackError(null)
    try {
      const res = await fetch(`${API_BASE}/api/ai/eval-pack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_backtest: true, limit: BACKTEST_LIMIT }),
      })
      if (!res.ok) throw new Error("Не удалось собрать пакет")
      const data = await res.json()
      if (data.latest_backtest?.status === "success") {
        setBacktestResult(data.latest_backtest)
        fetchBacktestHistory()
        fetchBacktestSlices()
      }
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      const stamp = (data.generated_at || new Date().toISOString()).replace(/[:.]/g, "-")
      a.download = `neurobet_eval_pack_${stamp}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err: any) {
      setEvalPackError(err.message || "Ошибка выгрузки пакета")
    } finally {
      setEvalPackLoading(false)
    }
  }

  useEffect(() => {
    if (!isAuthenticated) return
    fetchAISettings()
    fetchAILogs()
    fetchStats()
    fetchBankroll()
    fetchOpenLiveBetsCount()
    fetchBacktestHistory()
    fetchBacktestSlices()
    fetchTrainingHealth()
    fetchTrainingRuns()
    fetchHardware()

    const interval = setInterval(() => {
      fetchAILogs()
      fetchBankroll()
      fetchOpenLiveBetsCount()
      fetchTrainingHealth()
      fetchHardware()
    }, 3000)

    const statsInterval = setInterval(fetchStats, 15000)

    // Backtest history changes less often than logs (scheduled every 30 min, plus
    // occasional manual runs) — a separate, slower interval instead of piling it
    // into the 3s one above avoids re-fetching an unchanged JSON file on every tick.
    const backtestInterval = setInterval(() => {
      fetchBacktestHistory()
      fetchBacktestSlices()
    }, 30000)

    // Training passes fire more often than backtests (every couple of minutes when
    // data allows) but far less often than logs/stats — a middle-ground interval.
    const trainingRunsInterval = setInterval(fetchTrainingRuns, 15000)

    return () => {
      clearInterval(interval)
      clearInterval(statsInterval)
      clearInterval(backtestInterval)
      clearInterval(trainingRunsInterval)
    }
  }, [isAuthenticated, fetchAISettings, fetchAILogs, fetchStats, fetchBankroll, fetchOpenLiveBetsCount, fetchBacktestHistory, fetchBacktestSlices, fetchTrainingHealth, fetchTrainingRuns, fetchHardware])

  const toggleAISetting = async (
    key: "ai_enabled" | "training_enabled" | "quality_gate_bypass",
    currentValue: boolean,
  ) => {
    const newValue = !currentValue
    if (key === "ai_enabled") setAiEnabled(newValue)
    if (key === "training_enabled") setTrainingEnabled(newValue)
    if (key === "quality_gate_bypass") setQualityGateBypass(newValue)

    try {
      const res = await fetch(`${API_BASE}/api/admin/ai-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: newValue })
      })
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      const data = await res.json()
      if (data.status !== "success") {
        throw new Error(data.message || "settings save failed")
      }
      setTimeout(fetchAISettings, 300)
    } catch (err) {
      if (key === "ai_enabled") setAiEnabled(currentValue)
      if (key === "training_enabled") setTrainingEnabled(currentValue)
      if (key === "quality_gate_bypass") setQualityGateBypass(currentValue)
    }
  }

  const toggleSportEnabled = async (sportId: string) => {
    const key = sportId.toLowerCase()
    const has = enabledSports.some((s) => s.toLowerCase() === key)
    const next = has
      ? enabledSports.filter((s) => s.toLowerCase() !== key)
      : [...enabledSports, key]
    const prev = enabledSports
    setEnabledSports(next)
    try {
      const res = await fetch(`${API_BASE}/api/admin/ai-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled_sports: next }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.status !== "success") throw new Error(data.message || "settings save failed")
      if (Array.isArray(data.settings?.enabled_sports)) {
        setEnabledSports(data.settings.enabled_sports.map((s: string) => String(s).toLowerCase()))
      }
    } catch {
      setEnabledSports(prev)
    }
  }

  const toggleMarketEnabled = async (marketId: string) => {
    const key = marketId.toLowerCase()
    const has = enabledMarkets.some((m) => m.toLowerCase() === key)
    const next = has
      ? enabledMarkets.filter((m) => m.toLowerCase() !== key)
      : [...enabledMarkets, key]
    const prev = enabledMarkets
    setEnabledMarkets(next)
    try {
      const res = await fetch(`${API_BASE}/api/admin/ai-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled_markets: next }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      if (data.status !== "success") throw new Error(data.message || "settings save failed")
      if (Array.isArray(data.settings?.enabled_markets)) {
        setEnabledMarkets(data.settings.enabled_markets.map((m: string) => String(m).toLowerCase()))
      }
    } catch {
      setEnabledMarkets(prev)
    }
  }

  const handleManualScrape = async () => {
    setTriggering(true)
    try {
      await fetch(`${API_BASE}/api/trigger-scrape`, { method: "POST" })
      setTimeout(() => {
        fetchAILogs()
        fetchStats()
        setTriggering(false)
      }, 2000)
    } catch (err) {
      setTriggering(false)
    }
  }

  const filteredLogs = useMemo(() => {
    if (logFilter === "ALL") return logs
    return logs.filter((l) => l.category === logFilter)
  }, [logs, logFilter])

  // Login View
  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-neutral-950 text-neutral-100 flex items-center justify-center p-4 font-sans antialiased">
        <div className="w-full max-w-md bg-neutral-900 border border-neutral-800 rounded-3xl p-8 space-y-6 shadow-2xl relative overflow-hidden">
          <div className="absolute -top-12 -right-12 w-40 h-40 bg-[#fdcb6e]/10 rounded-full blur-2xl pointer-events-none" />

          <div className="text-center space-y-2">
            <div className="w-14 h-14 rounded-2xl bg-gradient-to-tr from-[#fdcb6e] to-[#ffeaa7] flex items-center justify-center mx-auto shadow-lg shadow-[#fdcb6e]/20 p-2">
              <img src="/logo.svg" alt="Нейроставки" className="w-full h-full object-contain" />
            </div>
            <h1 className="text-2xl font-black tracking-tight text-white mt-3">
              Панель Управления Админа
            </h1>
            <p className="text-xs text-neutral-400">
              Вход в админ-панель управления нейросетью и обучением
            </p>
          </div>

          {loginError && (
            <div className="bg-[#d63031]/15 border border-[#d63031]/40 rounded-xl px-4 py-2.5 text-xs text-[#ff7675] flex items-center gap-2">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              <span>{loginError}</span>
            </div>
          )}

          <form onSubmit={handleLogin} className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-neutral-300 flex items-center gap-1.5">
                <User className="w-3.5 h-3.5 text-[#fdcb6e]" />
                Логин
              </label>
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Введите логин..."
                required
                className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#fdcb6e] transition font-mono"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-neutral-300 flex items-center gap-1.5">
                <Lock className="w-3.5 h-3.5 text-[#fdcb6e]" />
                Пароль
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Введите пароль..."
                required
                className="w-full bg-neutral-950 border border-neutral-800 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#fdcb6e] transition font-mono"
              />
            </div>

            <button
              type="submit"
              disabled={loginLoading}
              className="w-full bg-gradient-to-r from-[#fdcb6e] to-[#ffeaa7] text-neutral-950 font-bold py-3 rounded-xl transition shadow-lg shadow-[#fdcb6e]/20 hover:opacity-90 disabled:opacity-50 text-sm mt-2"
            >
              {loginLoading ? "Авторизация..." : "Войти в Панель"}
            </button>
          </form>
        </div>
      </div>
    )
  }

  // Dashboard View
  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      {/* Shared Header Navigation */}
      <HeaderNav stats={stats} />

      {/* Admin Content */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Top Header Bar */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-neutral-900/80 border border-neutral-800 rounded-2xl p-5 backdrop-blur-md">
          <div className="flex items-center gap-3">
            <div className="relative w-11 h-11 rounded-xl bg-[#00b894]/15 border border-[#00b894]/30 flex items-center justify-center shrink-0">
              <motion.span
                className="absolute inset-0 rounded-xl bg-[#00b894]/25"
                animate={{ scale: [1, 1.3, 1], opacity: [0, 0.5, 0] }}
                transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
              />
              <ShieldCheck className="relative w-5 h-5 text-[#55efc4]" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                Админ-Панель Управления ИИ
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30">
                  Пользователь: diflector
                </span>
              </h2>
              <p className="text-xs text-neutral-400">
                Управление инференсом, онлайн-обучением и мониторинг логов в реальном времени
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleManualScrape}
              disabled={triggering}
              className="flex items-center gap-1.5 bg-[#0984e3] hover:bg-[#74b9ff] text-white font-bold px-3.5 py-2 rounded-xl transition text-xs shadow-md shadow-[#0984e3]/20 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${triggering ? "animate-spin" : ""}`} />
              Спарсить & Обновить ИИ
            </button>

            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 font-bold px-3.5 py-2 rounded-xl transition text-xs border border-neutral-700"
            >
              <LogOut className="w-3.5 h-3.5 text-[#ff7675]" />
              Выход
            </button>
          </div>
        </div>

        {/* Host hardware — CPU / RAM / disk / GPU, polled every 3s with logs */}
        {(() => {
          const cpu = hardware?.cpu
          const mem = hardware?.memory
          const disk = hardware?.disk
          const gpuWrap = hardware?.gpu
          const gpu = Array.isArray(gpuWrap?.gpus) && gpuWrap.gpus.length > 0 ? gpuWrap.gpus[0] : null
          const load = Array.isArray(cpu?.load_avg) ? cpu.load_avg : null
          const cores = cpu?.cores_logical || cpu?.cores_physical
          const gpuUnavailable = !hardware || !gpuWrap?.available || !gpu
          return (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
              <HardwareMeter
                icon={Cpu}
                label="Процессор"
                percent={cpu?.percent ?? null}
                value={cpu ? `${Number(cpu.percent || 0).toFixed(0)}%` : "—"}
                detail={
                  cpu
                    ? `${cores || "?"} ядер${load ? ` · load ${load.map((n: number) => n.toFixed(2)).join(" / ")}` : ""}`
                    : "ждём снимок нагрузки…"
                }
              />
              <HardwareMeter
                icon={MemoryStick}
                label="Оперативная память"
                percent={mem?.percent ?? null}
                value={mem ? formatBytes(mem.used_bytes) : "—"}
                detail={mem ? `${formatBytes(mem.used_bytes)} из ${formatBytes(mem.total_bytes)}` : "ждём снимок памяти…"}
              />
              <HardwareMeter
                icon={HardDrive}
                label="Жёсткий диск"
                percent={disk?.percent ?? null}
                value={disk ? formatBytes(disk.used_bytes) : "—"}
                detail={
                  disk
                    ? `${formatBytes(disk.used_bytes)} из ${formatBytes(disk.total_bytes)}${disk.path ? ` · ${disk.path}` : ""}`
                    : "ждём снимок диска…"
                }
              />
              <HardwareMeter
                icon={CircuitBoard}
                label="Видеокарта"
                percent={gpuUnavailable ? null : (gpu.util_percent ?? gpu.memory?.percent ?? 0)}
                value={
                  gpuUnavailable
                    ? "нет GPU"
                    : gpu.util_percent != null
                      ? `${Number(gpu.util_percent).toFixed(0)}%`
                      : formatBytes(gpu.memory?.used_bytes)
                }
                detail={
                  gpuUnavailable
                    ? (gpuWrap?.reason || "CUDA/nvidia-smi не видны из контейнера")
                    : `${gpu.name || "GPU"}${gpu.memory ? ` · VRAM ${formatBytes(gpu.memory.used_bytes)} / ${formatBytes(gpu.memory.total_bytes)}` : ""}${gpu.temperature_c != null ? ` · ${Number(gpu.temperature_c).toFixed(0)}°C` : ""}`
                }
                unavailable={gpuUnavailable}
              />
            </div>
          )
        })()}

        {/* Training Health Status Block — overfitting traffic light */}
        {(() => {
          const health = trainingHealth?.status || "unknown"
          const signals = trainingHealth?.signals || {}
          const cfg: Record<string, { bg: string; border: string; text: string; icon: any; title: string; blink: boolean }> = {
            ok: {
              bg: "bg-[#00b894]/10", border: "border-[#00b894]/50", text: "text-[#55efc4]",
              icon: ShieldCheck, title: "Обучение в норме — переобучения не видно", blink: false,
            },
            warning: {
              bg: "bg-[#fdcb6e]/10", border: "border-[#fdcb6e]/50", text: "text-[#ffeaa7]",
              icon: AlertTriangle, title: "Есть тревожный признак — присмотритесь", blink: false,
            },
            danger: {
              bg: "bg-[#d63031]/15", border: "border-[#d63031]/60", text: "text-[#ff7675]",
              icon: ShieldAlert, title: "Похоже на переобучение — рекомендуется остановить обучение", blink: true,
            },
            disabled: {
              bg: "bg-neutral-900/60", border: "border-neutral-800", text: "text-neutral-500",
              icon: Power, title: "Обучение выключено вручную — статус не отслеживается", blink: false,
            },
            unknown: {
              bg: "bg-neutral-900/60", border: "border-neutral-800", text: "text-neutral-400",
              icon: Activity, title: "Статус обучения пока неизвестен", blink: false,
            },
          }
          const c = cfg[health] || cfg.unknown
          const Icon = c.icon
          const isDisabled = health === "disabled"
          const s1 = signals.low_epoch_streak
          const s2 = signals.backtest_brier_not_beating_market
          const s3 = signals.backtest_roi_not_improving
          const s4 = signals.val_loss_trending_up
          const s5 = signals.checkpoint_reject_streak

          return (
            <div className={`rounded-2xl border p-5 backdrop-blur-md shadow-lg transition ${c.bg} ${c.border} ${c.blink ? "animate-pulse" : ""}`}>
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center border ${c.border} ${c.bg}`}>
                    <Icon className={`w-6 h-6 ${c.text}`} />
                  </div>
                  <div>
                    <h3 className={`text-base font-black ${c.text}`}>{c.title}</h3>
                    <p className="text-xs text-neutral-400 mt-0.5">
                      {isDisabled
                        ? "Включите тумблер \"Обучение Нейросети\" ниже, чтобы возобновить отслеживание."
                        : "Статус обучения нейросети — по стрику коротких эпох и тренду бэктеста. Обновляется каждые 3с."}
                    </p>
                  </div>
                </div>

                {!isDisabled && (
                <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono">
                  {trainingHealth?.archive_coverage && (
                    <span className={`px-2.5 py-1.5 rounded-full border ${
                      trainingHealth.archive_coverage.cold_start?.active
                        ? "bg-[#fdcb6e]/20 border-[#fdcb6e]/50 text-[#ffeaa7]"
                        : trainingHealth.archive_coverage.catch_up
                        ? "bg-[#0984e3]/20 border-[#0984e3]/50 text-[#74b9ff]"
                        : "bg-neutral-950 border-neutral-800 text-neutral-400"
                    }`}>
                      {trainingHealth.archive_coverage.cold_start?.active
                        ? `cold-start ${trainingHealth.archive_coverage.cold_start.epoch}/${trainingHealth.archive_coverage.cold_start.epochs_total} · архив ${Math.round((trainingHealth.archive_coverage.trained_ratio || 0) * 100)}%`
                        : `архив ${Math.round((trainingHealth.archive_coverage.trained_ratio || 0) * 100)}%`}
                      {!trainingHealth.archive_coverage.cold_start?.active && (
                        trainingHealth.archive_coverage.catch_up
                          ? ` · догон каждые ${trainingHealth.archive_coverage.train_every_cycles}`
                          : ` · каждые ${trainingHealth.archive_coverage.train_every_cycles}`
                      )}
                    </span>
                  )}
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s1?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    best_epoch ≤ {s1?.threshold ?? "—"}: {s1?.streak ?? 0} подряд{" "}
                    {s1?.active ? <AlertCircle className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} /> : <CheckCircle2 className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} />}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s2?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    Brier ≥ рынка ({s2?.runs_checked ?? 0}/{s2?.runs_needed ?? "—"} бэктестов){" "}
                    {s2?.active ? <AlertCircle className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} /> : <CheckCircle2 className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} />}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s3?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    ROI не растёт ({s3?.runs_checked ?? 0}/{s3?.runs_needed ?? "—"} бэктестов){" "}
                    {s3?.active ? <AlertCircle className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} /> : <CheckCircle2 className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} />}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s4?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    val_loss растёт ({s4?.runs_checked ?? 0}/{s4?.runs_needed ?? "—"} проходов){" "}
                    {s4?.active ? <AlertCircle className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} /> : <CheckCircle2 className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} />}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s5?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    checkpoint отклонён: {s5?.streak ?? 0}/{s5?.threshold ?? "—"} подряд{" "}
                    {s5?.active ? <AlertCircle className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} /> : <CheckCircle2 className="w-3 h-3 inline-block align-[-2px]" strokeWidth={1.75} />}
                  </span>
                </div>
                )}
              </div>

              {!isDisabled && trainingRuns.length > 0 && (
                <div className="mt-4 pt-4 border-t border-neutral-800/60">
                  <div className="text-[10px] text-neutral-500 uppercase font-mono mb-2">
                    Тренд обучения по проходам (val_loss / val_guess_rate)
                  </div>
                  <TrainingTrendChart history={trainingRuns} />
                </div>
              )}
            </div>
          )
        })()}

        {/* Live quality gate — always visible, not only after an in-page backtest */}
        {(() => {
          const gate = trainingHealth?.quality_gate
          if (!gate) {
            return (
              <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5 text-sm text-neutral-500">
                Quality gate — ждём данные с ai_service…
              </div>
            )
          }
          if (gate.enabled === false) {
            return (
              <div className="rounded-2xl border border-neutral-800 bg-neutral-900/60 p-5">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl border border-neutral-700 bg-neutral-950 flex items-center justify-center">
                    <FlaskConical className="w-5 h-5 text-neutral-500" />
                  </div>
                  <div>
                    <h3 className="text-sm font-black text-neutral-400">Quality gate выключен</h3>
                    <p className="text-xs text-neutral-500 mt-0.5">
                      NEURALBET_LIVE_QUALITY_GATE=0 — live-ставки не блокируются по бэктесту.
                    </p>
                  </div>
                </div>
              </div>
            )
          }
          const passed = Boolean(gate.pass)
          const bypassed = Boolean(gate.bypass) || qualityGateBypass
          const metrics = gate.metrics || {}
          const fmt = (v: unknown, digits = 1) =>
            v == null || Number.isNaN(Number(v)) ? "—" : Number(v).toFixed(digits)
          const liveAllowed = passed || bypassed
          return (
            <div className={`rounded-2xl border p-5 backdrop-blur-md shadow-lg ${
              liveAllowed
                ? bypassed && !passed
                  ? "bg-[#fdcb6e]/10 border-[#fdcb6e]/50"
                  : "bg-[#00b894]/10 border-[#00b894]/50"
                : "bg-[#d63031]/10 border-[#d63031]/50"
            }`}>
              <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
                <div className="flex items-center gap-3.5">
                  <div className={`w-12 h-12 rounded-2xl flex items-center justify-center border ${
                    liveAllowed
                      ? bypassed && !passed
                        ? "border-[#fdcb6e]/50 bg-[#fdcb6e]/10"
                        : "border-[#00b894]/50 bg-[#00b894]/10"
                      : "border-[#d63031]/50 bg-[#d63031]/10"
                  }`}>
                    {bypassed && !passed
                      ? <ShieldOff className="w-6 h-6 text-[#ffeaa7]" />
                      : passed
                      ? <ShieldCheck className="w-6 h-6 text-[#55efc4]" />
                      : <Ban className="w-6 h-6 text-[#ff7675]" />}
                  </div>
                  <div>
                    <h3 className={`text-base font-black ${
                      bypassed && !passed
                        ? "text-[#ffeaa7]"
                        : passed
                        ? "text-[#55efc4]"
                        : "text-[#ff7675]"
                    }`}>
                      Quality gate — {
                        bypassed && !passed
                          ? "bypass ON — live разрешены несмотря на fail"
                          : passed
                          ? "пройден, live разрешены"
                          : "блокирует live-ставки"
                      }
                    </h3>
                    <p className="text-xs text-neutral-400 mt-0.5">
                      Тот же OOS-чек, что решает, открывать ли virtual live. Обновляется вместе со статусом обучения.
                    </p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-[10px] font-mono">
                  {bypassed && (
                    <span className="px-2.5 py-1.5 rounded-full border bg-[#fdcb6e]/15 border-[#fdcb6e]/40 text-[#ffeaa7]">
                      bypass
                    </span>
                  )}
                  <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                    срез: {gate.eval_slice ?? "—"}
                  </span>
                  <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                    {metrics.bets ?? "—"} ставок
                  </span>
                  <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                    ROI {fmt(metrics.roi_pct)}% · CI lo {fmt(metrics.roi_pct_lo)}%
                  </span>
                  <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                    Brier {fmt(metrics.brier, 4)} / рынок {fmt(metrics.market_brier, 4)}
                  </span>
                  {metrics.consecutive_passes != null && (
                    <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                      pass {metrics.consecutive_passes}/{metrics.consecutive_required ?? "—"}
                    </span>
                  )}
                  {metrics.age_hours != null && (
                    <span className="px-2.5 py-1.5 rounded-full border bg-neutral-950 border-neutral-800 text-neutral-300">
                      возраст {fmt(metrics.age_hours)}ч
                    </span>
                  )}
                </div>
              </div>
              {!passed && (gate.reasons?.length ?? 0) > 0 && (
                <div className={`mt-3 pt-3 border-t border-neutral-800/60 text-xs font-mono ${
                  bypassed ? "text-[#ffeaa7]" : "text-[#ff7675]"
                }`}>
                  {(gate.reasons as string[]).join("; ")}
                  {bypassed ? " — игнорируется (bypass)" : ""}
                </div>
              )}
            </div>
          )
        })()}

        {/* Training Database Size & Disk Metrics Block */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-5 backdrop-blur-md shadow-lg flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            <div className="w-12 h-12 rounded-2xl bg-[#0984e3]/15 border border-[#0984e3]/30 flex items-center justify-center text-[#74b9ff] text-2xl">
              <Database className="w-6 h-6 text-[#0984e3]" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                Обучающая База Данных & Диск SQLite
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#0984e3]/20 text-[#74b9ff] border border-[#0984e3]/30">
                  autobet.db
                </span>
              </h3>
              <p className="text-xs text-neutral-400">
                Завершенные матчи переносятся из LIVE в архивную таблицу для обучения LightGBM & PyTorch
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 overflow-x-auto max-w-full">
            <div className="bg-neutral-950 px-4 py-2 rounded-xl border border-neutral-800 text-center">
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Размер БД на диске</div>
              <div className="text-sm font-black text-[#fdcb6e] font-mono mt-0.5">
                {stats?.db_size_formatted || "0 MB"}
              </div>
            </div>

            <div className="bg-neutral-950 px-4 py-2 rounded-xl border border-neutral-800 text-center">
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Завершенных игр</div>
              <div className="text-sm font-black text-[#55efc4] font-mono mt-0.5">
                {stats?.finished_events_count?.toLocaleString() || 0}
              </div>
            </div>

            <div className="bg-neutral-950 px-4 py-2 rounded-xl border border-neutral-800 text-center">
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Записей кэф в датасете</div>
              <div className="text-sm font-black text-[#74b9ff] font-mono mt-0.5">
                {stats?.finished_odds_history_count?.toLocaleString() || 0}
              </div>
            </div>

            <div className="bg-neutral-950 px-4 py-2 rounded-xl border border-neutral-800 text-center">
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Не рассчитано</div>
              <div className="text-sm font-black text-neutral-300 font-mono mt-0.5">
                {stats?.unresolved_bets_count?.toLocaleString() || 0}
              </div>
            </div>

            <div className="bg-neutral-950 px-4 py-2 rounded-xl border border-neutral-800 text-center">
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Активных LIVE</div>
              <div className="text-sm font-black text-[#ff7675] font-mono mt-0.5">
                {stats?.live_events_count || 0}
              </div>
            </div>
          </div>
        </div>

        {/* DB Reset Control Panel */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-5 backdrop-blur-md shadow-lg flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#d63031]/20 border border-[#d63031]/30 flex items-center justify-center text-[#ff7675]">
              <AlertTriangle className="w-5 h-5" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-white">
                Сброс и Обнуление Баз Данных
              </h3>
              <p className="text-xs text-neutral-400">
                Управление оперативной LIVE базой и обучающим архивом с обязательным подтверждением
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 w-full sm:w-auto">
            <button
              onClick={() => handleOpenResetModal("live")}
              className="flex-1 sm:flex-initial flex items-center justify-center gap-1.5 bg-[#fdcb6e]/15 hover:bg-[#fdcb6e]/25 text-[#ffeaa7] border border-[#fdcb6e]/40 font-bold px-4 py-2.5 rounded-xl transition text-xs shadow-md"
            >
              <Trash2 className="w-3.5 h-3.5 text-[#fdcb6e]" />
              Обнулить LIVE БД
            </button>

            <button
              onClick={() => handleOpenResetModal("all")}
              className="flex-1 sm:flex-initial flex items-center justify-center gap-1.5 bg-[#d63031]/20 hover:bg-[#d63031]/35 text-[#ff7675] border border-[#d63031]/50 font-bold px-4 py-2.5 rounded-xl transition text-xs shadow-md"
            >
              <Trash2 className="w-3.5 h-3.5 text-[#ff7675]" />
              Обнулить ВСЕ БД (Полный Сброс)
            </button>

            <button
              onClick={() => handleOpenResetModal("reset-model")}
              className="flex-1 sm:flex-initial flex items-center justify-center gap-1.5 bg-[#a29bfe]/15 hover:bg-[#a29bfe]/25 text-[#a29bfe] border border-[#a29bfe]/40 font-bold px-4 py-2.5 rounded-xl transition text-xs shadow-md"
            >
              <BrainCircuit className="w-3.5 h-3.5 text-[#a29bfe]" />
              Обнулить Нейросеть
            </button>
          </div>
        </div>

        {/* Bankroll Control Panel */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-5 backdrop-blur-md shadow-lg space-y-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-[#fdcb6e]/15 border border-[#fdcb6e]/30 flex items-center justify-center text-[#ffeaa7]">
              <Wallet className="w-5 h-5" />
            </div>
            <div className="flex-1">
              <h3 className="text-sm font-bold text-white">Банкроллы Нейросети</h3>
              <p className="text-xs text-neutral-400">
                Боевой банк — реальные симулированные ставки бота. Обучающий банк — влияет только на процесс обучения, к реальным ставкам отношения не имеет.
                Оба банка автоматически сбрасываются на 1000 ₽ при обнулении.
              </p>
            </div>

            <button
              onClick={() => handleOpenResetModal("cancel-bets")}
              disabled={openLiveBetsCount === 0}
              className="flex items-center gap-1.5 bg-[#d63031]/15 hover:bg-[#d63031]/25 text-[#ff7675] border border-[#d63031]/40 font-bold px-3.5 py-2 rounded-xl transition text-xs shadow-md disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
            >
              <Ban className="w-3.5 h-3.5" />
              Отменить ставки нейросети ({openLiveBetsCount})
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {(["live", "training"] as const).map((key) => {
              const acc = bankroll?.accounts?.[key]
              return (
                <div key={key} className="bg-neutral-950 border border-neutral-800 rounded-xl p-4 flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[10px] text-neutral-400 font-mono uppercase">
                      {key === "live" ? "Боевой" : "Обучающий"}
                    </div>
                    <div className="text-lg font-black text-white font-mono">
                      {acc ? Number(acc.balance).toFixed(1) : "—"} ₽
                    </div>
                    <div className="text-[10px] text-neutral-500 mt-0.5">
                      Банкротств: {acc?.ruin_count ?? 0}
                    </div>
                  </div>
                  <button
                    onClick={() => handleOpenResetModal(key === "live" ? "bankroll-live" : "bankroll-training")}
                    className="flex items-center gap-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 font-bold px-3 py-2 rounded-xl transition text-xs border border-neutral-700 shrink-0"
                  >
                    <RefreshCw className="w-3.5 h-3.5 text-[#fdcb6e]" />
                    Сбросить
                  </button>
                </div>
              )
            })}
          </div>
        </div>

        {/* Backtest Panel */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-5 backdrop-blur-md shadow-lg space-y-4">
          <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-[#a29bfe]/15 border border-[#a29bfe]/30 flex items-center justify-center text-[#a29bfe] shrink-0">
                <FlaskConical className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-white">Бэктест Модели</h3>
                <p className="text-xs text-neutral-400 max-w-xl">
                  Пересчитывает текущими весами вердикт и вероятность по последним завершённым ставкам и сравнивает
                  с тем, что реально предсказывалось тогда, и с голым рынком (1/кэф). Ничего не обучает и не меняет в модели.
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => handleRunBacktest("live")}
                disabled={backtestRunning || evalPackLoading}
                className="flex items-center gap-1.5 bg-[#a29bfe] hover:opacity-90 text-neutral-950 font-bold px-3.5 py-2 rounded-xl transition text-xs shadow-md shadow-[#a29bfe]/20 disabled:opacity-50"
              >
                <FlaskConical className={`w-3.5 h-3.5 ${backtestRunning ? "animate-pulse" : ""}`} />
                {backtestRunning ? "Считаю..." : "Запустить бэктест"}
              </button>
              <button
                onClick={() => handleRunBacktest("full")}
                disabled={backtestRunning || evalPackLoading}
                className="flex items-center gap-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 font-bold px-3 py-2 rounded-xl transition text-xs border border-neutral-700 disabled:opacity-50"
                title="Отладка по всем ALLOWED_SPORTS. Не обновляет quality gate и Brier."
              >
                <FlaskConical className="w-3.5 h-3.5 text-neutral-400" />
                Полный бэктест (все виды)
              </button>

              {backtestResult && (
                <button
                  onClick={downloadBacktestJson}
                  className="flex items-center gap-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 font-bold px-3 py-2 rounded-xl transition text-xs border border-neutral-700"
                >
                  <Download className="w-3.5 h-3.5 text-[#a29bfe]" />
                  JSON
                </button>
              )}
              <button
                onClick={downloadEvalPack}
                disabled={evalPackLoading || backtestRunning}
                className="flex items-center gap-1.5 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 font-bold px-3 py-2 rounded-xl transition text-xs border border-neutral-700 disabled:opacity-50"
                title="Бэктест + фильтры + ROI + здоровье обучения + логи — один JSON для агента"
              >
                <FileJson className={`w-3.5 h-3.5 text-[#fdcb6e] ${evalPackLoading ? "animate-pulse" : ""}`} />
                {evalPackLoading ? "Собираю пакет..." : "Пакет для агента"}
              </button>
            </div>
          </div>
          <p className="text-[11px] text-neutral-500 leading-relaxed">
            «Запустить бэктест» считает только включённые виды и обновляет quality gate / Brier.
            «Полный бэктест» — отладка по всем видам, гейт не трогает.
          </p>

          {evalPackError && (
            <div className="bg-[#d63031]/15 border border-[#d63031]/40 rounded-xl p-3 text-xs text-[#ff7675] flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{evalPackError}</span>
            </div>
          )}

          {backtestError && (
            <div className="bg-[#d63031]/15 border border-[#d63031]/40 rounded-xl p-3 text-xs text-[#ff7675] flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{backtestError}</span>
            </div>
          )}

          {backtestRunning && (
            <div className="space-y-2">
              <div className="flex items-center justify-between gap-3 text-[10px] font-mono uppercase tracking-wide text-neutral-400">
                <span className="truncate text-left">{backtestProgress.label || "Бэктест…"}</span>
                <span className="shrink-0 text-[#74b9ff]">{backtestProgress.pct}%</span>
              </div>
              <div className="h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                <div
                  className="h-full rounded-full bg-[#a29bfe] transition-all duration-300"
                  style={{ width: `${backtestProgress.pct}%` }}
                />
              </div>
              {backtestProgress.total > 0 && (
                <div className="text-[10px] text-neutral-500 font-mono">
                  {backtestProgress.processed.toLocaleString()} / {backtestProgress.total.toLocaleString()}
                </div>
              )}
            </div>
          )}

          {backtestResult && (
            <div className="space-y-4">
              <div className="text-[11px] text-neutral-500 font-mono">
                {backtestResult.mode === "full" ? "полный · " : backtestResult.mode === "live" ? "live · " : ""}
                {backtestResult.samples_evaluated?.toLocaleString()} ставок · {backtestResult.date_range?.from} → {backtestResult.date_range?.to} · заняло {backtestResult.duration_seconds}с ·
                {" "}blend_weight {backtestResult.config?.blend_weight} · market_weight {backtestResult.config?.market_weight} · порог {backtestResult.config?.decision_threshold} · макс. кэф {backtestResult.config?.max_bet_coeff}
              </div>

              {backtestResult.agent_review && (
                <div className="rounded-xl border border-neutral-800 bg-neutral-950 px-3.5 py-3 space-y-2">
                  <div className="text-[10px] text-neutral-400 uppercase font-mono">Agent review</div>
                  <div className="text-sm text-neutral-200 font-mono">
                    {backtestResult.agent_review.summary?.one_liner ?? "—"}
                  </div>
                  {backtestResult.agent_review.funnel && (
                    <div className="text-[11px] text-neutral-500 font-mono">
                      Воронка: {backtestResult.agent_review.funnel.verdict_positive?.toLocaleString()} verdict →{" "}
                      {backtestResult.agent_review.funnel.stake_candidates} candidate →{" "}
                      {backtestResult.agent_review.funnel.final_bets} ставок
                    </div>
                  )}
                  {(backtestResult.agent_review.flags?.length ?? 0) > 0 && (
                    <ul className="text-[11px] font-mono space-y-1">
                      {backtestResult.agent_review.flags.map((f: any, i: number) => (
                        <li key={i} className={
                          f.severity === "block" ? "text-[#ff7675]" :
                          f.severity === "warning" ? "text-[#fdcb6e]" : "text-neutral-400"
                        }>
                          [{f.severity}] {f.message}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {backtestResult.quality_gate && (
                <div className={`rounded-xl border px-3.5 py-3 text-xs font-mono ${
                  backtestResult.quality_gate.pass
                    ? "bg-[#00b894]/10 border-[#00b894]/40 text-[#55efc4]"
                    : "bg-[#d63031]/10 border-[#d63031]/40 text-[#ff7675]"
                }`}>
                  <div className="font-bold uppercase text-[10px] tracking-wide">
                    Quality gate — {backtestResult.quality_gate.pass ? "пройден" : "блокирует live-ставки"}
                  </div>
                  <div className="mt-1 text-neutral-300">
                    Срез: {backtestResult.quality_gate.eval_slice ?? "—"}
                    {backtestResult.quality_gate.metrics?.bets != null && (
                      <> · {backtestResult.quality_gate.metrics.bets} ставок · ROI {backtestResult.quality_gate.metrics.roi_pct ?? "—"}% · CI lo {backtestResult.quality_gate.metrics.roi_pct_lo ?? "—"}%</>
                    )}
                  </div>
                  {!backtestResult.quality_gate.pass && (backtestResult.quality_gate.reasons?.length ?? 0) > 0 && (
                    <div className="mt-1.5 text-[#ff7675]">
                      {(backtestResult.quality_gate.reasons as string[]).join("; ")}
                    </div>
                  )}
                  {backtestResult.quality_gate.metrics?.consecutive_passes != null && (
                    <div className="mt-1 text-neutral-500">
                      Consecutive passes: {backtestResult.quality_gate.metrics.consecutive_passes}/
                      {backtestResult.quality_gate.metrics.consecutive_required ?? "—"}
                    </div>
                  )}
                </div>
              )}

              {backtestResult.overall?.stake_policy?.current && (
                <div className="text-[11px] text-neutral-500 font-mono">
                  Flat {backtestResult.overall.stake_policy.current.flat_bets ?? backtestResult.overall.stake_policy.current.bets} ставок · ROI {backtestResult.overall.stake_policy.current.roi_pct ?? "—"}%
                  {backtestResult.overall.stake_policy.current.kelly_bets != null && (
                    <> · Kelly {backtestResult.overall.stake_policy.current.kelly_bets} ставок · bankroll ROI {backtestResult.overall.stake_policy.current.bankroll_roi_pct ?? "—"}%</>
                  )}
                </div>
              )}

              {backtestResult.policy_ablation_oos && (
                <div className="rounded-xl border border-neutral-800 bg-neutral-950 px-3.5 py-2">
                  <div className="text-[10px] text-neutral-400 uppercase font-mono mb-1.5">Policy ablation (OOS)</div>
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-[11px] font-mono">
                    {Object.entries(backtestResult.policy_ablation_oos as Record<string, any>).map(([name, m]) => (
                      <div key={name} className="text-neutral-400">
                        <span className="text-neutral-300">{name}</span>: {m.bets} bets · ROI {m.roi_pct ?? "—"}% · CI lo {m.roi_pct_lo ?? "—"}%
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                {([
                  { key: "current", label: "Текущая модель (сейчас)" },
                  { key: "historical", label: "Как было предсказано тогда" },
                ] as const).map(({ key, label }) => {
                  const d = backtestResult.overall?.[key]
                  return (
                    <div key={key} className="bg-neutral-950 border border-neutral-800 rounded-xl p-3.5">
                      <div className="text-[10px] text-neutral-400 uppercase font-mono">{label}</div>
                      {d ? (
                        <>
                          <div className="text-lg font-black text-white font-mono mt-1">
                            {d.accuracy_pct != null ? `${d.accuracy_pct}%` : "—"}
                            <span className="text-[10px] text-neutral-500 font-normal ml-1">точность</span>
                          </div>
                          <div className={`text-sm font-bold font-mono ${
                            d.roi_pct == null ? "text-neutral-500" : d.roi_pct >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"
                          }`}>
                            ROI {d.roi_pct != null ? `${d.roi_pct}%` : "—"}
                          </div>
                          <div className="text-[10px] text-neutral-500 mt-0.5">
                            {d.bets} ставок · Brier {d.brier}
                          </div>
                        </>
                      ) : (
                        <div className="text-xs text-neutral-500 mt-1.5">нет данных</div>
                      )}
                    </div>
                  )
                })}

                <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3.5">
                  <div className="text-[10px] text-neutral-400 uppercase font-mono">Рынок (1/кэф, без модели)</div>
                  <div className="text-sm font-bold font-mono text-neutral-300 mt-1">
                    Brier {backtestResult.overall?.market_brier ?? "—"}
                  </div>
                  <div className="text-[10px] text-neutral-500 mt-0.5">
                    базовая линия — калибровка без вердикта/ставок
                  </div>
                </div>
              </div>

              <BacktestSliceTable
                title="По видам спорта"
                rows={backtestResult.by_sport}
                nameHeader="Вид"
                nameOf={(row) => String(row.sport || "")}
              />
              <BacktestSliceTable
                title="Walk-forward по видам (OOS)"
                rows={backtestResult.walk_forward_by_sport}
                nameHeader="Вид"
                nameOf={(row) => String(row.sport || "")}
              />

              <div className="overflow-x-auto">
                <div className="text-[10px] text-neutral-500 uppercase font-mono mb-1.5">По коэффициенту</div>
                <table className="w-full text-xs font-mono min-w-[560px]">
                  <thead>
                    <tr className="text-neutral-500 text-left border-b border-neutral-800">
                      <th className="py-1.5 pr-3 font-semibold">Коэффициент</th>
                      <th className="py-1.5 pr-3 font-semibold">Оценено</th>
                      <th className="py-1.5 pr-3 font-semibold">Ставок</th>
                      <th className="py-1.5 pr-3 font-semibold">ROI (текущ.)</th>
                      <th className="py-1.5 pr-3 font-semibold">Brier (текущ.)</th>
                      <th className="py-1.5 pr-3 font-semibold">Brier (рынок)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(backtestResult.by_coefficient || []).map((row: any) => (
                      <tr key={row.bucket} className="border-b border-neutral-900/60">
                        <td className="py-1.5 pr-3 text-neutral-300">{row.bucket}</td>
                        <td className="py-1.5 pr-3 text-neutral-400">{row.evaluated}</td>
                        <td className="py-1.5 pr-3 text-neutral-400">{row.current?.bets ?? "—"}</td>
                        <td className={`py-1.5 pr-3 font-bold ${
                          row.current?.roi_pct == null ? "text-neutral-500" : row.current.roi_pct >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"
                        }`}>
                          {row.current?.roi_pct != null ? `${row.current.roi_pct}%` : "—"}
                        </td>
                        <td className="py-1.5 pr-3 text-neutral-400">{row.current?.brier ?? "—"}</td>
                        <td className={`py-1.5 pr-3 ${
                          row.current?.brier != null && row.market_brier != null && row.current.brier >= row.market_brier
                            ? "text-[#ff7675]" : "text-neutral-500"
                        }`}>
                          {row.market_brier ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {backtestHistory.length > 0 && (
            <div className="pt-2 border-t border-neutral-800/80">
              <div className="text-[10px] text-neutral-500 uppercase font-mono mb-2">
                Динамика качества модели по прогонам бэктеста (авто каждый час в :00 МСК + ручные запуски)
              </div>
              <QualityTrendChart history={backtestHistory} />
            </div>
          )}

          {backtestHistory.length > 0 && (
            <div className="pt-2 border-t border-neutral-800/80">
              <div className="text-[10px] text-neutral-500 uppercase font-mono mb-2">История запусков (последние {backtestHistory.length})</div>
              <div className="overflow-x-auto">
                <table className="w-full text-[11px] font-mono min-w-[520px]">
                  <thead>
                    <tr className="text-neutral-500 text-left border-b border-neutral-800">
                      <th className="py-1 pr-3 font-semibold">Когда</th>
                      <th className="py-1 pr-3 font-semibold">Ставок</th>
                      <th className="py-1 pr-3 font-semibold">Точность</th>
                      <th className="py-1 pr-3 font-semibold">ROI</th>
                      <th className="py-1 pr-3 font-semibold">Brier</th>
                      <th className="py-1 pr-3 font-semibold">Brier рынка</th>
                    </tr>
                  </thead>
                  <tbody>
                    {backtestHistory.slice(0, 10).map((run: any, i: number) => {
                      const cur = run.overall?.current
                      return (
                        <tr key={i} className="border-b border-neutral-900/60 text-neutral-400">
                          <td className="py-1 pr-3">{run.generated_at}</td>
                          <td className="py-1 pr-3">{run.samples_evaluated}</td>
                          <td className="py-1 pr-3">{cur?.accuracy_pct != null ? `${cur.accuracy_pct}%` : "—"}</td>
                          <td className={cur?.roi_pct == null ? "" : cur.roi_pct >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"}>
                            {cur?.roi_pct != null ? `${cur.roi_pct}%` : "—"}
                          </td>
                          <td className="py-1 pr-3">{cur?.brier ?? "—"}</td>
                          <td className="py-1 pr-3">{run.overall?.market_brier ?? "—"}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {resetSuccessMsg && (
          <div className="bg-[#00b894]/15 border border-[#00b894]/40 rounded-xl p-3.5 text-xs text-[#55efc4] flex items-center gap-2 animate-in fade-in">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-[#00b894]" />
            <span>{resetSuccessMsg}</span>
          </div>
        )}

        {/* Toggle Switches Controls */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* AI Inference Toggle */}
          <div className={`p-5 rounded-2xl border transition shadow-lg backdrop-blur-md overflow-hidden ${
            aiEnabled ? "bg-neutral-900/90 border-[#00b894]/40" : "bg-neutral-900/50 border-[#d63031]/40"
          }`}>
            <div className="flex items-start gap-3">
              <div className={`size-12 shrink-0 rounded-2xl flex items-center justify-center ${
                aiEnabled ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
              }`}>
                <BrainCircuit className="size-6" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-base font-bold text-white flex flex-wrap items-center gap-2 min-w-0">
                    Нейросеть (Inference)
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full shrink-0 ${
                      aiEnabled ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                    }`}>
                      {aiEnabled ? "ВКЛЮЧЕНА" : "ОТКЛЮЧЕНА"}
                    </span>
                  </h3>
                  <button
                    onClick={() => toggleAISetting("ai_enabled", aiEnabled)}
                    className={`relative w-14 h-8 rounded-full transition-colors duration-300 p-1 flex items-center shrink-0 ${
                      aiEnabled ? "bg-[#00b894]" : "bg-neutral-800 border border-neutral-700"
                    }`}
                  >
                    <div className={`size-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                      aiEnabled ? "translate-x-6" : "translate-x-0"
                    }`} />
                  </button>
                </div>
                <p className="text-xs text-neutral-400 mt-1 leading-relaxed">
                  Просчет вероятностей и ROI для всех LIVE ставок в реальном времени
                </p>
              </div>
            </div>
          </div>

          {/* AI Retraining Toggle */}
          <div className={`p-5 rounded-2xl border transition shadow-lg backdrop-blur-md overflow-hidden ${
            trainingEnabled ? "bg-neutral-900/90 border-[#fdcb6e]/40" : "bg-neutral-900/50 border-[#d63031]/40"
          }`}>
            <div className="flex items-start gap-3">
              <div className={`size-12 shrink-0 rounded-2xl flex items-center justify-center ${
                trainingEnabled ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
              }`}>
                <GraduationCap className="size-6" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-base font-bold text-white flex flex-wrap items-center gap-2 min-w-0">
                    Обучение Нейросети
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full shrink-0 ${
                      trainingEnabled ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                    }`}>
                      {trainingEnabled ? "ВКЛЮЧЕНО" : "ОТКЛЮЧЕНО"}
                    </span>
                  </h3>
                  <button
                    onClick={() => toggleAISetting("training_enabled", trainingEnabled)}
                    className={`relative w-14 h-8 rounded-full transition-colors duration-300 p-1 flex items-center shrink-0 ${
                      trainingEnabled ? "bg-[#fdcb6e]" : "bg-neutral-800 border border-neutral-700"
                    }`}
                  >
                    <div className={`size-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                      trainingEnabled ? "translate-x-6" : "translate-x-0"
                    }`} />
                  </button>
                </div>
                <p className="text-xs text-neutral-400 mt-1 leading-relaxed">
                  Фоновое дообучение PyTorch & LightGBM на завершенных матчах архива
                </p>
              </div>
            </div>
          </div>

          {/* Quality gate bypass */}
          <div className={`p-5 rounded-2xl border transition shadow-lg backdrop-blur-md overflow-hidden ${
            qualityGateBypass ? "bg-neutral-900/90 border-[#fdcb6e]/40" : "bg-neutral-900/50 border-neutral-800"
          }`}>
            <div className="flex items-start gap-3">
              <div className={`size-12 shrink-0 rounded-2xl flex items-center justify-center ${
                qualityGateBypass
                  ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30"
                  : "bg-neutral-800 text-neutral-400 border border-neutral-700"
              }`}>
                <ShieldOff className="size-6" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-base font-bold text-white flex flex-wrap items-center gap-2 min-w-0">
                    Bypass quality gate
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full shrink-0 ${
                      qualityGateBypass
                        ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30"
                        : "bg-neutral-800 text-neutral-400 border border-neutral-700"
                    }`}>
                      {qualityGateBypass ? "ВКЛЮЧЕН" : "ВЫКЛ"}
                    </span>
                  </h3>
                  <button
                    onClick={() => toggleAISetting("quality_gate_bypass", qualityGateBypass)}
                    className={`relative w-14 h-8 rounded-full transition-colors duration-300 p-1 flex items-center shrink-0 ${
                      qualityGateBypass ? "bg-[#fdcb6e]" : "bg-neutral-800 border border-neutral-700"
                    }`}
                  >
                    <div className={`size-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                      qualityGateBypass ? "translate-x-6" : "translate-x-0"
                    }`} />
                  </button>
                </div>
                <p className="text-xs text-neutral-400 mt-1 leading-relaxed">
                  Снимает quality gate для live-ставок (только для отладки)
                </p>
              </div>
            </div>
          </div>

          <div className="mt-4 bg-neutral-900/90 border border-neutral-800 rounded-2xl overflow-hidden">
            <button
              type="button"
              onClick={() => setSportsPanelOpen((v) => !v)}
              className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left"
            >
              <div>
                <h3 className="text-sm font-bold text-white">Виды спорта (live)</h3>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Потолок для инференса, UI и live-бэктеста. Обучение всегда на всех 5 видах.
                  ROI / CI / WR — из последнего live-прогона; если вида нет в live (выключен) — из полного бэктеста.
                </p>
              </div>
              <ChevronDown className={`w-4 h-4 text-neutral-400 shrink-0 transition-transform ${sportsPanelOpen ? "rotate-180" : ""}`} />
            </button>
            {sportsPanelOpen && (
              <div className="px-5 pb-5 space-y-2 border-t border-neutral-800 pt-3">
                {universeSportOptions([...UNIVERSE_SPORT_IDS]).map((sport) => {
                  const on = enabledSports.some((s) => s.toLowerCase() === sport.id.toLowerCase())
                  const kpis = resolveSliceKpis(
                    liveBacktestSnap,
                    fullBacktestSnap,
                    [sport.id],
                    ["walk_forward_by_sport", "by_sport"],
                    ["sport"],
                  )
                  return (
                    <div key={sport.id} className="flex items-center justify-between gap-3 py-1.5">
                      <div className="min-w-0">
                        <span className="text-sm text-neutral-200">
                          <SportName sport={sport.id} />
                        </span>
                        <SliceKpiLine kpis={kpis} />
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleSportEnabled(sport.id)}
                        className={`relative w-12 h-7 rounded-full transition-colors duration-300 p-0.5 flex items-center shrink-0 ${
                          on ? "bg-[#00b894]" : "bg-neutral-800 border border-neutral-700"
                        }`}
                      >
                        <div className={`size-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                          on ? "translate-x-5" : "translate-x-0"
                        }`} />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="mt-4 bg-neutral-900/90 border border-neutral-800 rounded-2xl overflow-hidden">
            <button
              type="button"
              onClick={() => setMarketsPanelOpen((v) => !v)}
              className="w-full flex items-center justify-between gap-3 px-5 py-4 text-left"
            >
              <div>
                <h3 className="text-sm font-bold text-white">Рынки (live)</h3>
                <p className="text-xs text-neutral-400 mt-0.5">
                  Потолок для инференса, UI и live-бэктеста. Обучение и полный бэктест всегда на всех рынках вселенной.
                  ROI / CI / WR — из последнего live-прогона; если рынка нет в live (выключен) — из полного бэктеста.
                </p>
              </div>
              <ChevronDown className={`w-4 h-4 text-neutral-400 shrink-0 transition-transform ${marketsPanelOpen ? "rotate-180" : ""}`} />
            </button>
            {marketsPanelOpen && (
              <div className="px-5 pb-5 space-y-2 border-t border-neutral-800 pt-3">
                {UNIVERSE_MARKET_OPTIONS.map((market) => {
                  const on = enabledMarkets.some((m) => m.toLowerCase() === market.id.toLowerCase())
                  const kpis = resolveSliceKpis(
                    liveBacktestSnap,
                    fullBacktestSnap,
                    MARKET_BACKTEST_ALIASES[market.id] || [market.id],
                    ["oos_by_market", "by_market"],
                    ["market"],
                  )
                  return (
                    <div key={market.id} className="flex items-center justify-between gap-3 py-1.5">
                      <div className="min-w-0">
                        <span className="text-sm text-neutral-200">{market.label}</span>
                        <SliceKpiLine kpis={kpis} />
                      </div>
                      <button
                        type="button"
                        onClick={() => toggleMarketEnabled(market.id)}
                        className={`relative w-12 h-7 rounded-full transition-colors duration-300 p-0.5 flex items-center shrink-0 ${
                          on ? "bg-[#00b894]" : "bg-neutral-800 border border-neutral-700"
                        }`}
                      >
                        <div className={`size-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                          on ? "translate-x-5" : "translate-x-0"
                        }`} />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        {/* Live AI Logs Console */}
        <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-5 md:p-6 space-y-4 backdrop-blur-md shadow-2xl">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-800 pb-4">
            <div className="flex items-center gap-2.5">
              <Terminal className="w-5 h-5 text-[#fdcb6e]" />
              <h3 className="text-base font-bold text-white tracking-tight">
                Консоль Логов Нейросети (Live Stream)
              </h3>
              <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-neutral-950 text-neutral-400 border border-neutral-800">
                {filteredLogs.length} записей
              </span>
            </div>

            {/* Filter Tabs — segmented control with a sliding indicator, same language used
                across the other pages' filter/sort toggles */}
            <div className="inline-flex flex-wrap items-center gap-1 bg-neutral-950 p-1 rounded-xl border border-neutral-800">
              {["ALL", "INFERENCE", "TRAINING", "BANKROLL", "SYSTEM"].map((f) => (
                <button
                  key={f}
                  onClick={() => setLogFilter(f)}
                  className={`relative px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    logFilter === f ? "text-white" : "text-neutral-400 hover:text-neutral-200"
                  }`}
                >
                  {logFilter === f && (
                    <motion.div
                      layoutId="logFilterIndicator"
                      layoutDependency={logFilter}
                      className="absolute inset-0 rounded-lg bg-neutral-700 shadow-sm"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <span className="relative z-10">{f}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Terminal Box */}
          <div className="bg-neutral-950 border border-neutral-800/80 rounded-xl p-4 font-mono text-xs max-h-96 overflow-y-auto space-y-2 shadow-inner">
            {filteredLogs.length === 0 ? (
              <div className="text-neutral-500 text-center py-8 italic">
                Логи нейросети отсутствуют или ещё не поступили.
              </div>
            ) : (
              filteredLogs.map((log: AILog, i: number) => {
                const isTraining = log.category === "TRAINING"
                const isInference = log.category === "INFERENCE"
                const isBankroll = log.category === "BANKROLL"
                const isWarning = log.level === "WARNING"

                return (
                  <div
                    key={`${log.timestamp}-${log.category}-${i}`}
                    className="flex items-start gap-3 py-1 border-b border-neutral-900/60 last:border-0 hover:bg-neutral-900/40 px-2 rounded transition"
                  >
                    <span className="text-neutral-500 whitespace-nowrap">{log.timestamp}</span>
                    <span
                      className={`px-1.5 py-0.2 text-[10px] rounded-full uppercase font-bold shrink-0 ${
                        isTraining
                          ? "bg-[#55efc4]/20 text-[#55efc4] border border-[#00b894]/40"
                          : isInference
                          ? "bg-[#ffeaa7]/20 text-[#ffeaa7] border border-[#fdcb6e]/40"
                          : isBankroll
                          ? "bg-[#fd79a8]/20 text-[#fd79a8] border border-[#e84393]/40"
                          : "bg-[#74b9ff]/20 text-[#74b9ff] border border-[#0984e3]/40"
                      }`}
                    >
                      {log.category}
                    </span>
                    <span
                      className={`break-all ${
                        isWarning
                          ? "text-[#ff7675] font-semibold"
                          : isTraining
                          ? "text-[#55efc4]"
                          : isInference
                          ? "text-neutral-200"
                          : isBankroll
                          ? "text-[#fd79a8]"
                          : "text-neutral-300"
                      }`}
                    >
                      {log.message}
                    </span>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </main>

      <footer className="border-t border-neutral-900 bg-neutral-950 py-4 px-6 text-center text-xs text-neutral-500">
        Нейроставки &copy; 2026 — AI прогнозы ставок
      </footer>

      {/* Confirmation Modal */}
      {resetModalOpen && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-neutral-900 border border-neutral-800 rounded-3xl p-6 max-w-md w-full space-y-5 shadow-2xl relative overflow-hidden animate-in fade-in zoom-in-95 duration-200">
            <div className="w-12 h-12 rounded-2xl bg-[#d63031]/20 border border-[#d63031]/40 flex items-center justify-center text-[#ff7675] mx-auto">
              <AlertTriangle className="w-6 h-6" />
            </div>

            <div className="text-center space-y-2">
              <h3 className="text-lg font-bold text-white">
                {resetType === "live" && "Обнулить LIVE Базу Данных?"}
                {resetType === "all" && "Обнулить ВСЕ Базы Данных?"}
                {resetType === "bankroll-live" && "Сбросить Боевой Банк?"}
                {resetType === "bankroll-training" && "Сбросить Обучающий Банк?"}
                {resetType === "cancel-bets" && `Отменить ${openLiveBetsCount} открытых ставок?`}
                {resetType === "reset-model" && "Обнулить Нейросеть?"}
              </h3>
              <p className="text-xs text-neutral-300">
                {resetType === "live" &&
                  "Будут удалены все текущие лайв-события, свежие коэффициенты и сохраненные предсказания AI. Все открытые ставки бота будут аннулированы с возвратом суммы. Архив обучающих игр сохраняется."}
                {resetType === "all" &&
                  "ВНИМАНИЕ! Будет полностью очищена оперативная LIVE база, весь архив обучающих завершенных матчей и удалены веса модели нейросети!"}
                {resetType === "bankroll-live" &&
                  "Баланс боевого банка (реальные симулированные ставки бота) будет сброшен до 1000 ₽. Открытые ставки и история не удаляются."}
                {resetType === "bankroll-training" &&
                  "Баланс обучающего банка будет сброшен до 1000 ₽. Это не влияет на веса модели, только на счёт, используемый в обучающем лоссе."}
                {resetType === "cancel-bets" &&
                  "Все текущие открытые ставки бота будут отменены (не засчитаны как выигрыш/проигрыш), а поставленная сумма полностью вернётся на боевой баланс."}
                {resetType === "reset-model" &&
                  "Веса PyTorch будут переинициализированы случайно, бустер LightGBM удалён, blend/market weight и порог решения сброшены к дефолтам, файлы чекпоинтов на диске удалены. Графики обучения (val_loss) и бэктеста очистятся. Оба банка (live и training) сбросятся на стартовый баланс, открытые ставки и журнал банка удалятся. У всех завершённых ставок в архиве trained_count обнулится до 0 — обучение начнётся заново на уже накопленных исторических данных (архив finished_bets НЕ удаляется). Первые 2 прохода cold-start идут по всему архиву (до 200 эпох с early stopping), затем обычные циклы по 10k."}
              </p>
            </div>

            <div className="bg-[#d63031]/10 border border-[#d63031]/30 rounded-xl p-3 text-xs text-[#ff7675] font-semibold text-center">
              Данное действие необратимо! Вы уверены?
            </div>

            {resetType === "reset-model" && resetLoading && (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3 text-[10px] font-mono uppercase tracking-wide text-neutral-400">
                  <span className="truncate text-left">{resetProgress.label || "Обнуление…"}</span>
                  <span className="shrink-0 text-[#74b9ff]">{resetProgress.pct}%</span>
                </div>
                <div className="h-1.5 rounded-full bg-neutral-800 overflow-hidden">
                  <div
                    className="h-full rounded-full bg-[#0984e3] transition-all duration-300"
                    style={{ width: `${resetProgress.pct}%` }}
                  />
                </div>
              </div>
            )}

            <div className="flex items-center gap-3">
              <button
                onClick={() => setResetModalOpen(false)}
                disabled={resetLoading}
                className="flex-1 bg-neutral-800 hover:bg-neutral-700 text-neutral-200 font-bold py-2.5 rounded-xl transition text-xs border border-neutral-700"
              >
                Отмена
              </button>

              <button
                onClick={handleConfirmReset}
                disabled={resetLoading}
                className="flex-1 bg-gradient-to-r from-[#d63031] to-[#ff7675] text-white font-bold py-2.5 rounded-xl transition text-xs shadow-lg shadow-[#d63031]/20 hover:opacity-90 disabled:opacity-50"
              >
                {resetLoading
                  ? (resetType === "cancel-bets" ? "Отмена ставок..." : resetType === "reset-model" ? "Обнуляю нейросеть..." : "Обнуление...")
                  : (resetType === "cancel-bets" ? "Подтвердить отмену" : "Подтвердить обнуление")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
