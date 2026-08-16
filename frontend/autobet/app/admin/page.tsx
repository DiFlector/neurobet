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
  Download
} from "lucide-react"
import { HeaderNav } from "@/components/HeaderNav"
import { QualityTrendChart } from "@/components/QualityTrendChart"
import { TrainingTrendChart } from "@/components/TrainingTrendChart"

interface AILog {
  timestamp: string
  category: string
  level: string
  message: string
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

  // Training Health State (overfitting traffic light)
  const [trainingHealth, setTrainingHealth] = useState<any>(null)
  const [trainingRuns, setTrainingRuns] = useState<any[]>([])

  // Backtest State
  const [backtestRunning, setBacktestRunning] = useState(false)
  const [backtestResult, setBacktestResult] = useState<any>(null)
  const [backtestError, setBacktestError] = useState<string | null>(null)
  const [backtestHistory, setBacktestHistory] = useState<any[]>([])

  // See app/neurobets/page.tsx for why this defaults to "" (same-origin, proxied by
  // next.config.ts) instead of an absolute localhost URL.
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""

  const handleOpenResetModal = (type: "live" | "all" | "bankroll-live" | "bankroll-training" | "cancel-bets" | "reset-model") => {
    setResetType(type)
    setResetModalOpen(true)
  }

  const handleConfirmReset = async () => {
    if (!resetType) return
    setResetLoading(true)
    setResetSuccessMsg(null)

    try {
      if (resetType === "cancel-bets") {
        const res = await fetch(`${API_BASE}/api/admin/live-bets/cancel-all`, { method: "POST" })
        if (!res.ok) throw new Error("Ошибка при отмене ставок")
        const data = await res.json()
        setResetSuccessMsg(data.message || "Ставки отменены")
        setTimeout(() => { fetchBankroll(); fetchOpenLiveBetsCount() }, 300)
      } else if (resetType === "reset-model") {
        const res = await fetch(`${API_BASE}/api/admin/reset-model`, { method: "POST" })
        if (!res.ok) throw new Error("Ошибка при обнулении нейросети")
        const data = await res.json()
        setResetSuccessMsg(
          `Нейросеть обнулена. Очищено trained_count у ${data.reset_rows ?? 0} завершённых ставок — обучение начнётся заново на существующем архиве.`
        )
        setTimeout(() => { fetchAILogs(); fetchAISettings() }, 300)
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

  // Check existing session
  useEffect(() => {
    const token = sessionStorage.getItem("admin_token")
    if (token === "diflector-admin-secret-token") {
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

      sessionStorage.setItem("admin_token", "diflector-admin-secret-token")
      setIsAuthenticated(true)
    } catch (err: any) {
      setLoginError(err.message || "Неверное имя пользователя или пароль")
    } finally {
      setLoginLoading(false)
    }
  }

  const handleLogout = () => {
    sessionStorage.removeItem("admin_token")
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
        setLogs(data.logs || [])
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stats`)
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
      const res = await fetch(`${API_BASE}/api/neurobets/live-bets?limit=200`)
      if (res.ok) {
        const data = await res.json()
        const openCount = (data.items || []).filter((b: any) => b.status === "open").length
        setOpenLiveBetsCount(openCount)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

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

  const handleRunBacktest = async () => {
    setBacktestRunning(true)
    setBacktestError(null)
    try {
      const res = await fetch(`${API_BASE}/api/admin/backtest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // 15000 -> 40000: at current archive growth (~570k resolved bets) 15k samples
        // span under an hour of matches — one late-night table-tennis-heavy slice, not
        // a representative view. 40k covers several hours across sports; the run takes
        // ~10s instead of ~3s, well within the proxy's 300s budget.
        body: JSON.stringify({ limit: 40000 })
      })
      if (!res.ok) throw new Error("Ошибка при запуске бэктеста")
      const data = await res.json()
      if (data.status === "no_data") throw new Error("Недостаточно завершённых ставок для бэктеста")
      if (data.status !== "success") throw new Error("Бэктест завершился с ошибкой")
      setBacktestResult(data)
      fetchBacktestHistory()
    } catch (err: any) {
      setBacktestError(err.message || "Ошибка при запуске бэктеста")
    } finally {
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

  useEffect(() => {
    if (!isAuthenticated) return
    fetchAISettings()
    fetchAILogs()
    fetchStats()
    fetchBankroll()
    fetchOpenLiveBetsCount()
    fetchBacktestHistory()
    fetchTrainingHealth()
    fetchTrainingRuns()

    const interval = setInterval(() => {
      fetchAILogs()
      fetchStats()
      fetchBankroll()
      fetchOpenLiveBetsCount()
      fetchTrainingHealth()
    }, 3000)

    // Backtest history changes far less often than the rest (4x/day via the scheduler,
    // plus occasional manual runs) — a separate, slower interval instead of piling it
    // into the 3s one above avoids re-fetching an unchanged 180-entry JSON file on
    // every tick for no reason. Still automatic: without this, a scheduled backtest
    // (or one run from another admin tab) would never show up here short of a manual
    // page reload.
    const backtestInterval = setInterval(fetchBacktestHistory, 30000)

    // Training passes fire more often than backtests (every couple of minutes when
    // data allows) but far less often than logs/stats — a middle-ground interval.
    const trainingRunsInterval = setInterval(fetchTrainingRuns, 15000)

    return () => {
      clearInterval(interval)
      clearInterval(backtestInterval)
      clearInterval(trainingRunsInterval)
    }
  }, [isAuthenticated, fetchAISettings, fetchAILogs, fetchStats, fetchBankroll, fetchOpenLiveBetsCount, fetchBacktestHistory, fetchTrainingHealth, fetchTrainingRuns])

  const toggleAISetting = async (key: "ai_enabled" | "training_enabled", currentValue: boolean) => {
    const newValue = !currentValue
    if (key === "ai_enabled") setAiEnabled(newValue)
    if (key === "training_enabled") setTrainingEnabled(newValue)

    try {
      await fetch(`${API_BASE}/api/admin/ai-settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: newValue })
      })
      setTimeout(fetchAISettings, 300)
    } catch (err) {
      // Revert if error
      if (key === "ai_enabled") setAiEnabled(currentValue)
      if (key === "training_enabled") setTrainingEnabled(currentValue)
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

        {/* Training Health Status Block — overfitting traffic light */}
        {(() => {
          const health = trainingHealth?.status || "unknown"
          const signals = trainingHealth?.signals || {}
          const cfg: Record<string, { bg: string; border: string; text: string; icon: any; title: string; blink: boolean }> = {
            ok: {
              bg: "bg-[#00b894]/10", border: "border-[#00b894]/50", text: "text-[#55efc4]",
              icon: ShieldCheck, title: "✅ Обучение в норме — переобучения не видно", blink: false,
            },
            warning: {
              bg: "bg-[#fdcb6e]/10", border: "border-[#fdcb6e]/50", text: "text-[#ffeaa7]",
              icon: AlertTriangle, title: "⚠️ Есть тревожный признак — присмотритесь", blink: false,
            },
            danger: {
              bg: "bg-[#d63031]/15", border: "border-[#d63031]/60", text: "text-[#ff7675]",
              icon: ShieldAlert, title: "🔴 Похоже на переобучение — рекомендуется остановить обучение", blink: true,
            },
            disabled: {
              bg: "bg-neutral-900/60", border: "border-neutral-800", text: "text-neutral-500",
              icon: Power, title: "⏸️ Обучение выключено вручную — статус не отслеживается", blink: false,
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
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s1?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    best_epoch ≤ {s1?.threshold ?? "—"}: {s1?.streak ?? 0} подряд {s1?.active ? "🔴" : "✓"}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s2?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    Brier ≥ рынка ({s2?.runs_checked ?? 0}/{s2?.runs_needed ?? "—"} бэктестов) {s2?.active ? "🔴" : "✓"}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s3?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    ROI не растёт ({s3?.runs_checked ?? 0}/{s3?.runs_needed ?? "—"} бэктестов) {s3?.active ? "🔴" : "✓"}
                  </span>
                  <span className={`px-2.5 py-1.5 rounded-full border ${
                    s4?.active ? "bg-[#d63031]/20 border-[#d63031]/50 text-[#ff7675]" : "bg-neutral-950 border-neutral-800 text-neutral-400"
                  }`}>
                    val_loss растёт ({s4?.runs_checked ?? 0}/{s4?.runs_needed ?? "—"} проходов) {s4?.active ? "🔴" : "✓"}
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
              <div className="text-[10px] text-neutral-400 font-mono uppercase">Не рассчитано (⚪)</div>
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
                onClick={handleRunBacktest}
                disabled={backtestRunning}
                className="flex items-center gap-1.5 bg-[#a29bfe] hover:opacity-90 text-neutral-950 font-bold px-3.5 py-2 rounded-xl transition text-xs shadow-md shadow-[#a29bfe]/20 disabled:opacity-50"
              >
                <FlaskConical className={`w-3.5 h-3.5 ${backtestRunning ? "animate-pulse" : ""}`} />
                {backtestRunning ? "Считаю..." : "Запустить бэктест"}
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
            </div>
          </div>

          {backtestError && (
            <div className="bg-[#d63031]/15 border border-[#d63031]/40 rounded-xl p-3 text-xs text-[#ff7675] flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{backtestError}</span>
            </div>
          )}

          {backtestResult && (
            <div className="space-y-4">
              <div className="text-[11px] text-neutral-500 font-mono">
                {backtestResult.samples_evaluated?.toLocaleString()} ставок · {backtestResult.date_range?.from} → {backtestResult.date_range?.to} · заняло {backtestResult.duration_seconds}с ·
                {" "}blend_weight {backtestResult.config?.blend_weight} · market_weight {backtestResult.config?.market_weight} · порог {backtestResult.config?.decision_threshold} · макс. кэф {backtestResult.config?.max_bet_coeff}
              </div>

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

              <div className="overflow-x-auto">
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
                Динамика качества модели по прогонам бэктеста (авто в 00:00 / 06:00 / 12:00 / 18:00 МСК + ручные запуски)
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
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* AI Inference Toggle */}
          <div className={`p-6 rounded-2xl border transition shadow-lg backdrop-blur-md ${
            aiEnabled ? "bg-neutral-900/90 border-[#00b894]/40" : "bg-neutral-900/50 border-[#d63031]/40"
          }`}>
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3.5">
                <div className={`w-12 h-12 rounded-2xl flex items-center justify-center text-xl font-bold ${
                  aiEnabled ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                }`}>
                  <BrainCircuit className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white flex items-center gap-2">
                    Нейросеть (Inference)
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${
                      aiEnabled ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                    }`}>
                      {aiEnabled ? "ВКЛЮЧЕНА" : "ОТКЛЮЧЕНА"}
                    </span>
                  </h3>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    Просчет вероятностей и ROI для всех LIVE ставок в реальном времени
                  </p>
                </div>
              </div>

              {/* Toggle Switch */}
              <button
                onClick={() => toggleAISetting("ai_enabled", aiEnabled)}
                className={`relative w-14 h-8 rounded-full transition-colors duration-300 p-1 flex items-center ${
                  aiEnabled ? "bg-[#00b894]" : "bg-neutral-800 border border-neutral-700"
                }`}
              >
                <div className={`w-6 h-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                  aiEnabled ? "translate-x-6" : "translate-x-0"
                }`} />
              </button>
            </div>
          </div>

          {/* AI Retraining Toggle */}
          <div className={`p-6 rounded-2xl border transition shadow-lg backdrop-blur-md ${
            trainingEnabled ? "bg-neutral-900/90 border-[#fdcb6e]/40" : "bg-neutral-900/50 border-[#d63031]/40"
          }`}>
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3.5">
                <div className={`w-12 h-12 rounded-2xl flex items-center justify-center text-xl font-bold ${
                  trainingEnabled ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                }`}>
                  <GraduationCap className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-base font-bold text-white flex items-center gap-2">
                    Обучение Нейросети
                    <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full ${
                      trainingEnabled ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30" : "bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/30"
                    }`}>
                      {trainingEnabled ? "ВКЛЮЧЕНО" : "ОТКЛЮЧЕНО"}
                    </span>
                  </h3>
                  <p className="text-xs text-neutral-400 mt-0.5">
                    Фоновое дообучение PyTorch & LightGBM на завершенных матчах архива
                  </p>
                </div>
              </div>

              {/* Toggle Switch */}
              <button
                onClick={() => toggleAISetting("training_enabled", trainingEnabled)}
                className={`relative w-14 h-8 rounded-full transition-colors duration-300 p-1 flex items-center ${
                  trainingEnabled ? "bg-[#fdcb6e]" : "bg-neutral-800 border border-neutral-700"
                }`}
              >
                <div className={`w-6 h-6 rounded-full bg-white transition-transform duration-300 shadow-md ${
                  trainingEnabled ? "translate-x-6" : "translate-x-0"
                }`} />
              </button>
            </div>
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
                    key={i}
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
                  "Веса PyTorch будут переинициализированы случайно, бустер LightGBM удалён, blend/market weight и порог решения сброшены к дефолтам, файлы чекпоинтов на диске удалены. У всех завершённых ставок в архиве trained_count обнулится до 0 — обучение начнётся заново, но на уже накопленных исторических данных (архив finished_bets НЕ удаляется)."}
              </p>
            </div>

            <div className="bg-[#d63031]/10 border border-[#d63031]/30 rounded-xl p-3 text-xs text-[#ff7675] font-semibold text-center">
              ⚠️ Данное действие необратимо! Вы уверены?
            </div>

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
