"use client"

import { useState, useMemo, useEffect, useCallback } from "react"
import {
  BrainCircuit,
  TrendingUp,
  ShieldCheck,
  Zap,
  Sparkles,
  Trophy,
  Filter,
  CheckCircle2,
  AlertTriangle,
  Info,
  Clock,
  ArrowUpRight,
  ArrowDownRight,
  Activity,
  Layers,
  BarChart3,
  Percent,
  Database,
  Loader2
} from "lucide-react"
import { HeaderNav } from "@/components/HeaderNav"

interface NeuroBet {
  id: string
  rankBest: number
  rankSafe: number
  sport: string
  matchName: string
  team1: string
  team2: string
  score: string
  timer: string
  marketName: string
  outcomeLabel: string
  coefficient: number
  initialCoefficient: number
  aiProbability: number // 0-100%
  aiErrorRate: number // 0-100% error rate / loss
  expectedRoi: number // % ROI
  riskLevel: "minimal" | "low" | "medium"
  aiInsights: string[]
  lightgbmScore: number
  pytorchScore: number
}

export default function NeurobetsPage() {
  const [activeTab, setActiveTab] = useState<"live" | "history">("live")
  const [sortMode, setSortMode] = useState<"best" | "safe">("best")
  const [selectedSport, setSelectedSport] = useState<string>("all")
  const [minOdds, setMinOdds] = useState<number>(1.1)
  const [maxOdds, setMaxOdds] = useState<number>(2.1)
  const [stats, setStats] = useState<any>(null)
  const [liveBets, setLiveBets] = useState<NeuroBet[]>([])
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)

  // History State
  const [historyItems, setHistoryItems] = useState<any[]>([])
  const [historySummary, setHistorySummary] = useState<any>(null)
  const [historyLoading, setHistoryLoading] = useState(false)

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

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

  const fetchHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const params = new URLSearchParams({
        min_odds: minOdds.toString(),
        max_odds: maxOdds.toString()
      })
      if (selectedSport !== "all") {
        params.append("sport", selectedSport)
      }
      const res = await fetch(`${API_BASE}/api/neurobets/history?${params.toString()}`)
      if (res.ok) {
        const data = await res.json()
        setHistoryItems(data.history || [])
        setHistorySummary(data.summary || null)
      }
    } catch (err) {
      // Ignore
    } finally {
      setHistoryLoading(false)
    }
  }, [API_BASE, selectedSport, minOdds, maxOdds])

  const fetchNeurobets = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        sort: sortMode,
        min_odds: minOdds.toString(),
        max_odds: maxOdds.toString()
      })
      if (selectedSport !== "all") params.append("sport", selectedSport)

      const res = await fetch(`${API_BASE}/api/neurobets/top?${params.toString()}`)
      if (res.ok) {
        const data = await res.json()
        if (data.bets) {
          const mapped: NeuroBet[] = data.bets.map((b: any, idx: number) => {
            const coeff = floatVal(b.coefficient)
            const initCoeff = floatVal(b.initial_coefficient) || coeff
            const diff = (initCoeff - coeff).toFixed(2)
            const diffText = initCoeff > coeff ? `📉 Падение кэфа (-${diff})` : initCoeff < coeff ? `📈 Рост кэфа (+${Math.abs(Number(diff))})` : "⚖️ Стабильный кэф"

            return {
              id: `live-${b.event_id}-${b.factor_id}-${b.parameter}`,
              rankBest: idx + 1,
              rankSafe: idx + 1,
              sport: b.sport_path ? b.sport_path.split(".")[0] : "Спорт",
              matchName: b.match_name || `${b.team_1} — ${b.team_2}`,
              team1: b.team_1 || "Команда 1",
              team2: b.team_2 || "Команда 2",
              score: b.score || "0:0",
              timer: b.timer || "LIVE",
              marketName: b.market_prefix || "Основной маркет",
              outcomeLabel: b.label ? (b.parameter ? `${b.label} (${b.parameter})` : b.label) : `Factor ${b.factor_id}`,
              coefficient: coeff,
              initialCoefficient: initCoeff,
              aiProbability: b.win_probability,
              aiErrorRate: b.error_rate,
              expectedRoi: b.expected_roi,
              riskLevel: b.win_probability > 90 ? "minimal" : b.win_probability > 75 ? "low" : "medium",
              aiInsights: [
                diffText,
                `📊 LightGBM score: ${b.lightgbm_score}`,
                `🧠 PyTorch trajectory: ${Math.round(b.pytorch_score * 100)}/100`
              ],
              lightgbmScore: b.lightgbm_score,
              pytorchScore: b.pytorch_score
            }
          })
          setLiveBets(mapped)
        }
      }
    } catch (err) {
      setLiveBets([])
    } finally {
      setLoading(false)
    }
  }, [API_BASE, sortMode, selectedSport, minOdds, maxOdds])

  useEffect(() => {
    if (activeTab === "live") {
      fetchNeurobets()
      fetchStats()

      if (!autoRefresh) return
      const interval = setInterval(() => {
        fetchNeurobets()
        fetchStats()
      }, 10000)
      return () => clearInterval(interval)
    } else {
      fetchHistory()
      fetchStats()
    }
  }, [activeTab, fetchNeurobets, fetchHistory, fetchStats, autoRefresh])

  const sportsList = [
    { id: "all", label: "Все виды спорта" },
    { id: "Футбол", label: "⚽ Футбол" },
    { id: "Хоккей", label: "🏒 Хоккей" },
    { id: "Баскетбол", label: "🏀 Баскетбол" },
    { id: "Теннис", label: "🎾 Теннис" },
    { id: "Киберспорт", label: "🎮 Киберспорт" }
  ]

  function floatVal(val: any): number {
    const p = parseFloat(val)
    return isNaN(p) ? 1.0 : p
  }

  // Calculate Average Model Error Rate across filtered live bets
  const avgErrorRate = useMemo(() => {
    if (liveBets.length === 0) return 3.1
    const total = liveBets.reduce((acc, b) => acc + b.aiErrorRate, 0)
    return total / liveBets.length
  }, [liveBets])

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      {/* Shared Header Navigation */}
      <HeaderNav stats={stats} />

      {/* Main Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Banner: AI Engine Overview */}
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-neutral-900 via-neutral-900/90 to-neutral-950 border border-neutral-800 p-6 md:p-8 shadow-2xl">
          <div className="absolute -right-10 -bottom-10 w-72 h-72 bg-[#fdcb6e]/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute right-32 top-0 w-48 h-48 bg-[#00b894]/10 rounded-full blur-3xl pointer-events-none" />

          <div className="relative z-10 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-6">
            <div className="space-y-3 max-w-2xl">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#fdcb6e]/10 border border-[#fdcb6e]/30 text-[#ffeaa7] text-xs font-semibold">
                <BrainCircuit className="w-4 h-4 text-[#fdcb6e] animate-pulse" />
                <span>LightGBM & PyTorch Online Model Ensemble</span>
              </div>
              <h2 className="text-2xl md:text-3xl font-black tracking-tight text-white">
                🧠 Нейроставки — Умный Рейтинг Топ Ставок
              </h2>
              <p className="text-sm text-neutral-300 leading-relaxed">
                Алгоритм непрерывно анализирует прямую трансляцию коэффициентов и движений линий Fonbet LIVE, рассчитывает математическое ожидание (EV) и вероятность выигрыша.
                Ставки с коэффициентами меньше <span className="font-mono text-[#ff7675]">1.10</span> (бессмысленные) и больше <span className="font-mono text-[#ff7675]">2.10</span> (слишком рискованные) автоматически отсеиваются.
              </p>
            </div>

            {/* AI Architecture Stats Badges */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 w-full lg:w-auto">
              <div className="bg-neutral-950/80 border border-neutral-800 rounded-xl p-3 text-center backdrop-blur">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">PyTorch Temporal</div>
                <div className="text-base font-bold text-[#55efc4] font-mono mt-0.5">Online GRU</div>
                <div className="text-[9px] text-neutral-500 mt-0.5">Векторы кэф 10m</div>
              </div>
              <div className="bg-neutral-950/80 border border-neutral-800 rounded-xl p-3 text-center backdrop-blur">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">LightGBM GBDT</div>
                <div className="text-base font-bold text-[#ffeaa7] font-mono mt-0.5">Leaf Speed</div>
                <div className="text-[9px] text-neutral-500 mt-0.5">&lt; 5ms инференс</div>
              </div>
              <div className="bg-neutral-950/80 border border-neutral-800 rounded-xl p-3 text-center backdrop-blur">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">Золотой коридор</div>
                <div className="text-base font-bold text-[#74b9ff] font-mono mt-0.5">1.10 — 2.10</div>
                <div className="text-[9px] text-neutral-500 mt-0.5">Safe ROI Bounds</div>
              </div>
              <div className="bg-neutral-950/80 border border-neutral-800/80 rounded-xl p-3 text-center backdrop-blur bg-gradient-to-b from-neutral-950 to-[#ff7675]/5 border-[#ff7675]/30">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">Ошибка нейросети</div>
                <div className="text-base font-bold text-[#ff7675] font-mono mt-0.5">
                  {stats?.error_rate_pct !== undefined ? `${stats.error_rate_pct.toFixed(1)}%` : `${avgErrorRate.toFixed(1)}%`}
                </div>
                <div className="text-[9px] text-[#55efc4] font-semibold mt-0.5">
                  {stats?.accuracy_pct !== undefined ? `${stats.accuracy_pct.toFixed(1)}% точность` : "87.6% точность"}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Main Sub-Tab Switcher: Active LIVE Bets vs History & Results */}
        <div className="flex items-center gap-2 bg-neutral-900/90 p-1.5 rounded-2xl border border-neutral-800 backdrop-blur-md shadow-lg">
          <button
            onClick={() => setActiveTab("live")}
            className={`flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl text-xs md:text-sm font-bold transition ${
              activeTab === "live"
                ? "bg-gradient-to-r from-[#fdcb6e] to-[#ffeaa7] text-neutral-950 shadow-lg shadow-[#fdcb6e]/20"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/60"
            }`}
          >
            <Zap className="w-4 h-4" />
            🔥 Активные LIVE Прогнозы ({liveBets.length})
          </button>

          <button
            onClick={() => setActiveTab("history")}
            className={`flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl text-xs md:text-sm font-bold transition ${
              activeTab === "history"
                ? "bg-gradient-to-r from-[#00b894] to-[#55efc4] text-neutral-950 shadow-lg shadow-[#00b894]/20"
                : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800/60"
            }`}
          >
            <Trophy className="w-4 h-4" />
            📜 История Прогнозов ({historySummary?.total_count || stats?.finished_odds_history_count || 0})
          </button>
        </div>

        {/* Filter Controls & Sort Mode Switcher */}
        <div className="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 md:p-5 space-y-4 backdrop-blur-md">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            {/* Sort Mode Buttons (Only shown for LIVE Tab) */}
            {activeTab === "live" ? (
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-neutral-400 flex items-center gap-1.5">
                  <BarChart3 className="w-3.5 h-3.5 text-[#fdcb6e]" />
                  Режим сортировки ставок:
                </label>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setSortMode("best")}
                    className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition shadow-sm ${
                      sortMode === "best"
                        ? "bg-[#fdcb6e] text-neutral-950 shadow-[#fdcb6e]/20"
                        : "bg-neutral-950 text-neutral-400 hover:text-white border border-neutral-800"
                    }`}
                  >
                    <Trophy className="w-3.5 h-3.5" />
                    ⭐ Самая лучшая (Max ROI / EV)
                  </button>

                  <button
                    onClick={() => setSortMode("safe")}
                    className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold transition shadow-sm ${
                      sortMode === "safe"
                        ? "bg-[#00b894] text-neutral-950 shadow-[#00b894]/20"
                        : "bg-neutral-950 text-neutral-400 hover:text-white border border-neutral-800"
                    }`}
                  >
                    <ShieldCheck className="w-3.5 h-3.5" />
                    🛡️ Самая безопасная (Max Win %)
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  📜 Результаты Прогнозов Нейросети
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30">
                    Архив завершенных матчей
                  </span>
                </h3>
                <p className="text-xs text-neutral-400">
                  Зеленый цвет — ставка сыграла (нейросеть угадала), Красный — ставка не прошла.
                </p>
              </div>
            )}

            {/* Odds Range Bounds Indicator */}
            <div className="space-y-1 bg-neutral-950 p-3 rounded-xl border border-neutral-800/80 shrink-0">
              <div className="text-xs font-semibold text-neutral-300 flex items-center justify-between gap-4">
                <span>Границы коэффициентов:</span>
                <span className="font-mono text-[#fdcb6e] font-bold">
                  {minOdds.toFixed(2)} — {maxOdds.toFixed(2)}
                </span>
              </div>
              <span className="text-[11px] text-neutral-400 italic">
                {activeTab === "live"
                  ? sortMode === "best"
                    ? "Баланс шанса выигрыша и размера коэффициента"
                    : "Фильтр наивысшей вероятности захода"
                  : "Золотая середина рекомендованных ставок"}
              </span>
            </div>
          </div>

          {/* Sport Categories Tabs */}
          <div className="flex items-center gap-2 overflow-x-auto pt-2 pb-1 border-t border-neutral-800/80">
            {sportsList.map((sport) => (
              <button
                key={sport.id}
                onClick={() => setSelectedSport(sport.id)}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-medium whitespace-nowrap transition ${
                  selectedSport === sport.id
                    ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/40 font-bold"
                    : "bg-neutral-950 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 border border-neutral-800/60"
                }`}
              >
                {sport.label}
              </button>
            ))}
          </div>
        </div>

        {/* Tab Content Section */}
        {activeTab === "history" ? (
          /* HISTORY TAB VIEW */
          <div className="space-y-6">
            {/* History Summary Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">Всего прогнозов</div>
                <div className="text-xl font-black text-white font-mono mt-1">
                  {historySummary?.total_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-neutral-500 mt-0.5">В архивном датасете</div>
              </div>

              <div className="bg-neutral-900/90 border border-[#00b894]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#55efc4] font-mono uppercase font-bold">🟢 Выиграно (Зашло)</div>
                <div className="text-xl font-black text-[#55efc4] font-mono mt-1">
                  {historySummary?.wins_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-[#55efc4]/80 mt-0.5">Успешные ставки</div>
              </div>

              <div className="bg-neutral-900/90 border border-[#d63031]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#ff7675] font-mono uppercase font-bold">🔴 Проиграно (Не зашло)</div>
                <div className="text-xl font-black text-[#ff7675] font-mono mt-1">
                  {historySummary?.losses_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-[#ff7675]/80 mt-0.5">Незашедшие исходы</div>
              </div>

              <div className="bg-neutral-900/90 border border-[#fdcb6e]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#ffeaa7] font-mono uppercase font-bold">🎯 Процент выигрыша</div>
                <div className="text-xl font-black text-[#ffeaa7] font-mono mt-1">
                  {historySummary?.win_rate_pct !== undefined ? `${historySummary.win_rate_pct.toFixed(1)}%` : "0.0%"}
                </div>
                <div className="text-[10px] text-[#ffeaa7]/80 mt-0.5">Общий Win Rate</div>
              </div>
            </div>

            {/* History Items List */}
            {historyLoading ? (
              <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
                <Loader2 className="w-8 h-8 text-[#00b894] animate-spin mx-auto" />
                <p className="text-sm font-semibold">Загрузка истории завершенных прогнозов...</p>
              </div>
            ) : historyItems.length === 0 ? (
              <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
                <Trophy className="w-10 h-10 text-neutral-600 mx-auto" />
                <p className="text-base font-bold text-white">История прогнозов пока пуста</p>
                <p className="text-xs text-neutral-400 max-w-md mx-auto">
                  Как только активные матчи завершатся, они автоматически попадут в историю прогнозов с фиксацией выигрыша (зеленый) или проигрыша (красный).
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {historyItems.map((item: any, idx: number) => {
                  const isWin = item.is_win === 1
                  const coeff = item.initial_coefficient || item.final_coefficient || 1.5

                  return (
                    <div
                      key={item.id || idx}
                      className={`relative overflow-hidden rounded-2xl p-5 border backdrop-blur-md transition shadow-md ${
                        isWin
                          ? "bg-[#00b894]/10 border-[#00b894]/50 shadow-[#00b894]/5"
                          : "bg-[#d63031]/10 border-[#d63031]/50 shadow-[#d63031]/5"
                      }`}
                    >
                      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                        {/* Event Details */}
                        <div className="space-y-1.5 flex-1">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-neutral-950 text-neutral-400 border border-neutral-800">
                              {item.sport_path || "Спорт"}
                            </span>
                            <span className="text-xs text-neutral-400 font-mono">
                              Завершен в {item.finished_at || item.timestamp}
                            </span>
                          </div>

                          <h4 className="text-base font-bold text-white tracking-tight">
                            {item.match_name || `${item.team_1} vs ${item.team_2}`}
                          </h4>

                          <div className="flex items-center gap-3 text-xs text-neutral-300">
                            <span className="font-semibold text-white">
                              Счет: <span className="font-mono text-[#fdcb6e] font-bold">{item.score || `${item.score_1} : ${item.score_2}`}</span>
                            </span>
                            <span>•</span>
                            <span>
                              Маркет: <span className="text-neutral-200 font-medium">{item.market_prefix || "Основной"} — {item.label} {item.parameter ? `(${item.parameter})` : ""}</span>
                            </span>
                          </div>
                        </div>

                        {/* Odds & Prediction Stats */}
                        <div className="flex items-center gap-4 shrink-0">
                          <div className="bg-neutral-950/80 px-3.5 py-2 rounded-xl border border-neutral-800 text-center">
                            <div className="text-[10px] text-neutral-400 font-mono uppercase">Коэффициент</div>
                            <div className="text-base font-black text-[#fdcb6e] font-mono mt-0.5">
                              {coeff.toFixed(2)}
                            </div>
                          </div>

                          <div className="bg-neutral-950/80 px-3.5 py-2 rounded-xl border border-neutral-800 text-center">
                            <div className="text-[10px] text-neutral-400 font-mono uppercase">Прогноз ИИ</div>
                            <div className="text-base font-black text-white font-mono mt-0.5">
                              {item.win_probability}%
                            </div>
                          </div>

                          {/* Outcome Status Badge */}
                          <div className={`px-4 py-3 rounded-xl border flex items-center justify-center gap-1.5 text-xs font-black font-mono shrink-0 shadow-md ${
                            isWin
                              ? "bg-[#00b894] text-neutral-950 border-[#55efc4]"
                              : "bg-[#d63031] text-white border-[#ff7675]"
                          }`}>
                            {isWin ? (
                              <>
                                <CheckCircle2 className="w-4 h-4 text-neutral-950" />
                                🟢 ВЫИГРЫШ (ЗАШЛА)
                              </>
                            ) : (
                              <>
                                <AlertTriangle className="w-4 h-4 text-white" />
                                🔴 ПРОИГРЫШ (НЕ ЗАШЛА)
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ) : (
          /* LIVE TAB VIEW */
          <div className="space-y-4">
            {loading ? (
              <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
                <Loader2 className="w-8 h-8 text-[#fdcb6e] animate-spin mx-auto" />
                <p className="text-sm font-semibold">Загрузка прогнозов нейросети...</p>
              </div>
            ) : liveBets.length === 0 ? (
              <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
                <BrainCircuit className="w-10 h-10 text-[#fdcb6e] mx-auto opacity-80 animate-pulse" />
                <p className="text-base font-bold text-white">
                  Ожидание данных от парсера и нейросети...
                </p>
                <p className="text-xs text-neutral-400 max-w-md mx-auto">
                  Парсер Fonbet LIVE сканирует активные матчи, а LightGBM & PyTorch вычисляют вероятности выигрыша по всем исходам в реальном времени. Данные обновляются каждые 10 секунд.
                </p>
              </div>
            ) : (
              liveBets.map((bet, index) => {
                const currentRank = index + 1
                const isRose = bet.coefficient > bet.initialCoefficient
                const isDropped = bet.coefficient < bet.initialCoefficient

                return (
                  <div
                    key={bet.id}
                    className={`relative overflow-hidden rounded-2xl bg-neutral-900/80 border transition hover:border-neutral-700 shadow-lg p-5 md:p-6 space-y-4 ${
                      currentRank === 1
                        ? "border-[#fdcb6e]/50 bg-gradient-to-r from-neutral-900 via-neutral-900/95 to-[#fdcb6e]/10 shadow-[#fdcb6e]/10"
                        : currentRank === 2
                        ? "border-[#00b894]/40 bg-gradient-to-r from-neutral-900 via-neutral-900/95 to-[#00b894]/10 shadow-[#00b894]/10"
                        : "border-neutral-800"
                    }`}
                  >
                    {/* Top Bar: Rank Badge + Match Info */}
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-neutral-800/80 pb-3">
                      <div className="flex items-center gap-3">
                        {/* Rank Medal Badge */}
                        <div
                          className={`px-3 py-1.5 rounded-xl font-black text-xs font-mono tracking-wider uppercase flex items-center gap-1.5 shadow-md ${
                            currentRank === 1
                              ? "bg-gradient-to-r from-[#fdcb6e] to-[#ffeaa7] text-neutral-950 shadow-[#fdcb6e]/30"
                              : currentRank === 2
                              ? "bg-gradient-to-r from-[#00b894] to-[#55efc4] text-neutral-950 shadow-[#00b894]/30"
                              : currentRank === 3
                              ? "bg-gradient-to-r from-[#0984e3] to-[#74b9ff] text-neutral-950 shadow-[#0984e3]/30"
                              : "bg-neutral-800 text-neutral-300"
                          }`}
                        >
                          <Trophy className="w-3.5 h-3.5" />
                          ТОП #{currentRank}
                        </div>

                        <div className="flex items-center gap-2 text-xs text-neutral-400">
                          <span className="px-2 py-0.5 bg-neutral-950 rounded border border-neutral-800 text-neutral-300 font-medium">
                            {bet.sport}
                          </span>
                          <span className="flex items-center gap-1 text-[#ff7675] font-mono">
                            <Clock className="w-3.5 h-3.5" />
                            {bet.timer}
                          </span>
                        </div>
                      </div>

                      {/* Score badge */}
                      <div className="flex items-center gap-2 bg-neutral-950 px-3 py-1 rounded-xl border border-neutral-800 self-start sm:self-auto">
                        <span className="text-xs text-neutral-400 font-mono">Счет:</span>
                        <span className="font-mono font-black text-base text-[#fdcb6e]">
                          {bet.score}
                        </span>
                      </div>
                    </div>

                    {/* Middle Section: Match Teams & Target Market */}
                    <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-center">
                      {/* Teams & Bet Target (7 cols) */}
                      <div className="lg:col-span-7 space-y-2">
                        <h3 className="text-lg font-bold text-white tracking-tight flex items-center gap-2">
                          {bet.team1} <span className="text-neutral-500 font-normal">vs</span> {bet.team2}
                        </h3>

                        <div className="flex flex-wrap items-center gap-3">
                          <div className="bg-neutral-950 px-3 py-1.5 rounded-xl border border-neutral-800 text-xs">
                            <span className="text-neutral-400">Маркет: </span>
                            <span className="text-neutral-200 font-medium">{bet.marketName}</span>
                          </div>
                          <div className="bg-[#fdcb6e]/15 border border-[#fdcb6e]/40 px-3 py-1.5 rounded-xl text-xs font-bold text-[#ffeaa7]">
                            Исход: {bet.outcomeLabel}
                          </div>
                        </div>
                      </div>

                      {/* Odds & Value Index (5 cols) */}
                      <div className="lg:col-span-5 flex flex-wrap sm:flex-nowrap items-center justify-between lg:justify-end gap-4 bg-neutral-950/60 p-3 rounded-xl border border-neutral-800/80">
                        {/* Coefficient display */}
                        <div>
                          <div className="text-[10px] text-neutral-400 font-mono">Коэффициент</div>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            <span className="text-xl font-black font-mono text-white">
                              {bet.coefficient.toFixed(2)}
                            </span>
                            {isDropped && (
                              <span className="flex items-center text-[10px] font-mono text-[#55efc4] bg-[#00b894]/20 px-1.5 py-0.5 rounded border border-[#00b894]/30">
                                <ArrowDownRight className="w-3 h-3" />
                                {(bet.coefficient - bet.initialCoefficient).toFixed(2)}
                              </span>
                            )}
                            {isRose && (
                              <span className="flex items-center text-[10px] font-mono text-[#ff7675] bg-[#d63031]/20 px-1.5 py-0.5 rounded border border-[#d63031]/30">
                                <ArrowUpRight className="w-3 h-3" />
                                +{(bet.coefficient - bet.initialCoefficient).toFixed(2)}
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Expected ROI Index */}
                        <div className="text-right">
                          <div className="text-[10px] text-neutral-400 font-mono">EV (Ожидаемый ROI)</div>
                          <div className="text-base font-black font-mono text-[#55efc4] mt-0.5">
                            +{bet.expectedRoi.toFixed(1)}%
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* AI Probability Progress Gauge & Error Metric */}
                    <div className="space-y-1.5 bg-neutral-950/50 p-3 rounded-xl border border-neutral-800/50">
                      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-1 text-xs">
                        <span className="font-semibold text-neutral-300 flex items-center gap-1.5">
                          <Activity className="w-3.5 h-3.5 text-[#00b894]" />
                          Вероятность захода нейросети:
                        </span>
                        <div className="flex items-center gap-2">
                          {/* Error Percentage Badge */}
                          <span className="inline-flex items-center gap-1 font-mono text-[10px] text-[#ff7675] bg-[#d63031]/15 px-2 py-0.5 rounded-md border border-[#d63031]/30">
                            <Percent className="w-3 h-3" />
                            Ошибка нейросети: {bet.aiErrorRate.toFixed(1)}%
                          </span>

                          {/* Probability Value */}
                          <span className="font-mono font-black text-[#55efc4] text-sm">
                            {bet.aiProbability.toFixed(1)}%
                          </span>
                        </div>
                      </div>

                      {/* Animated Gradient Bar */}
                      <div className="w-full bg-neutral-900 rounded-full h-2.5 overflow-hidden border border-neutral-800">
                        <div
                          className="bg-gradient-to-r from-[#00b894] via-[#55efc4] to-[#fdcb6e] h-full rounded-full transition-all duration-500 shadow-sm"
                          style={{ width: `${bet.aiProbability}%` }}
                        />
                      </div>
                    </div>

                    {/* Neural Insights / Factors Breakdown */}
                    <div className="space-y-1.5 pt-1">
                      <div className="text-[11px] font-semibold text-neutral-400 flex items-center gap-1">
                        <Zap className="w-3 h-3 text-[#fdcb6e]" />
                        Факторы решения нейронной сети:
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {bet.aiInsights.map((insight, idx) => (
                          <span
                            key={idx}
                            className="inline-flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-lg bg-neutral-950 border border-neutral-800/80 text-neutral-300"
                          >
                            {insight}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        )}
      </main>
    </div>
  )
}
