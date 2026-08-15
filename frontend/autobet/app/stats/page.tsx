"use client"

import { useState, useEffect, useCallback, useMemo } from "react"
import { BarChart3, RefreshCw, Loader2, Target, CheckCircle2, XCircle, Trophy, Search, ChevronDown } from "lucide-react"
import { HeaderNav } from "@/components/HeaderNav"
import { sortBySportOrder } from "@/lib/sports"

// Bet-type labels are grouped into these buckets purely by label shape (resolve_factor_label()
// in backend/parser_service.py always appends the parameter in parens for Фора/Тотал bets),
// so no factor_id table is needed here.
const MATCH_RESULT_LABELS = new Set(["П1", "П2", "Х", "X", "1X", "12", "X2", "Ничья", "Победа 1", "Победа 2"])

function classifyBetType(label: string): string {
  if (label.startsWith("Фора")) return "Форы"
  if (label.startsWith("Тотал")) return "Тоталы"
  if (MATCH_RESULT_LABELS.has(label)) return "Победа / Ничья"
  return "Другое"
}

const GROUP_ORDER = ["Победа / Ничья", "Форы", "Тоталы", "Другое"]

interface BetTypeStat {
  factor_id: number
  label: string
  judged: number
  correct: number
  incorrect: number
  guess_rate_pct: number
}

interface SportStat {
  sport: string
  judged: number
  correct: number
  incorrect: number
  guess_rate_pct: number
  bet_types: BetTypeStat[]
}

interface OverallStat {
  judged: number
  correct: number
  incorrect: number
  guess_rate_pct: number | null
}

// Two-color угадано/не угадано ratio bar — green share = correct/judged, red share =
// incorrect/judged. Mirrors the plain flex-width bar idiom (no framer-motion needed here,
// this page isn't a live-refreshing list where an animated width transition earns its keep).
function GuessRatioBar({ correct, incorrect }: { correct: number; incorrect: number }) {
  const total = correct + incorrect
  const correctPct = total > 0 ? (correct / total) * 100 : 0
  const incorrectPct = total > 0 ? (incorrect / total) * 100 : 0
  return (
    <div className="w-full h-2.5 rounded-full overflow-hidden bg-neutral-800 border border-neutral-700 flex">
      <div className="h-full bg-[#00b894]" style={{ width: `${correctPct}%` }} />
      <div className="h-full bg-[#d63031]" style={{ width: `${incorrectPct}%` }} />
    </div>
  )
}

export default function StatsPage() {
  const [overall, setOverall] = useState<OverallStat | null>(null)
  const [sports, setSports] = useState<SportStat[]>([])
  const [selectedSport, setSelectedSport] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [headerStats, setHeaderStats] = useState<any>(null)
  const [betTypeSearch, setBetTypeSearch] = useState("")
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())

  const toggleGroup = (group: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      if (next.has(group)) next.delete(group)
      else next.add(group)
      return next
    })
  }

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""

  useEffect(() => {
    fetch(`${API_BASE}/api/stats`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => data && setHeaderStats(data.stats))
      .catch(() => {})
  }, [API_BASE])

  const fetchStatsByType = useCallback(async (isManualRefresh = false) => {
    if (isManualRefresh) setRefreshing(true)
    try {
      const res = await fetch(`${API_BASE}/api/neurobets/stats-by-type`)
      if (res.ok) {
        const data = await res.json()
        setOverall(data.overall || null)
        const sportList: SportStat[] = sortBySportOrder(data.sports || [], (s: SportStat) => s.sport)
        setSports(sportList)
        setSelectedSport((prev) => {
          if (prev && sportList.some((s) => s.sport === prev)) return prev
          const defaultSport = sportList.find((s) => s.sport === "Футбол")
          return defaultSport ? defaultSport.sport : sportList.length > 0 ? sportList[0].sport : null
        })
      }
    } catch (err) {
      // Ignore — page just keeps showing the last known state
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [API_BASE])

  useEffect(() => {
    fetchStatsByType()
  }, [fetchStatsByType])

  const activeSport = useMemo(
    () => sports.find((s) => s.sport === selectedSport) || null,
    [sports, selectedSport]
  )

  const groupedBetTypes = useMemo(() => {
    if (!activeSport) return []
    const query = betTypeSearch.trim().toLowerCase()
    const filtered = query
      ? activeSport.bet_types.filter((bt) => bt.label.toLowerCase().includes(query))
      : activeSport.bet_types

    const groups = new Map<string, BetTypeStat[]>()
    for (const bt of filtered) {
      const group = classifyBetType(bt.label)
      if (!groups.has(group)) groups.set(group, [])
      groups.get(group)!.push(bt)
    }

    return GROUP_ORDER.filter((g) => groups.has(g)).map((group) => {
      const betTypes = groups.get(group)!
      const judged = betTypes.reduce((sum, bt) => sum + bt.judged, 0)
      const correct = betTypes.reduce((sum, bt) => sum + bt.correct, 0)
      const incorrect = betTypes.reduce((sum, bt) => sum + bt.incorrect, 0)
      return {
        group,
        betTypes,
        judged,
        correct,
        incorrect,
        guess_rate_pct: judged > 0 ? (correct / judged) * 100 : 0,
      }
    })
  }, [activeSport, betTypeSearch])

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      <HeaderNav stats={headerStats} />

      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Banner */}
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-neutral-900 via-neutral-900/90 to-neutral-950 border border-neutral-800 p-6 md:p-8 shadow-2xl">
          <div className="absolute -right-10 -bottom-10 w-72 h-72 bg-[#0984e3]/10 rounded-full blur-3xl pointer-events-none" />
          <div className="relative z-10 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-6">
            <div className="space-y-3 max-w-2xl">
              <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#0984e3]/10 border border-[#0984e3]/30 text-[#74b9ff] text-xs font-semibold">
                <BarChart3 className="w-4 h-4 text-[#0984e3]" />
                <span>Разбивка угадывания по видам спорта и типам ставок</span>
              </div>
              <h2 className="text-2xl md:text-3xl font-black tracking-tight text-white">
                📊 Статистика нейросети
              </h2>
              <p className="text-sm text-neutral-300 leading-relaxed">
                Насколько точно вердикт сети (выиграет/проиграет) совпадает с фактическим исходом —
                по каждому виду спорта и по каждому конкретному типу ставки («Фора 2», «Тотал Больше 2.5», «П1» и т.д.).
              </p>
            </div>

            <button
              onClick={() => fetchStatsByType(true)}
              disabled={refreshing}
              className="flex items-center gap-1.5 bg-[#0984e3] hover:bg-[#74b9ff] text-neutral-950 font-bold px-4 py-2 rounded-xl transition text-xs shadow-md shadow-[#0984e3]/20 disabled:opacity-50 shrink-0"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`} />
              Обновить
            </button>
          </div>
        </div>

        {loading ? (
          <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
            <Loader2 className="w-8 h-8 text-[#0984e3] animate-spin mx-auto" />
            <p className="text-sm font-semibold">Загрузка статистики...</p>
          </div>
        ) : !overall || overall.judged === 0 ? (
          <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-12 text-center text-neutral-400 space-y-3">
            <BarChart3 className="w-10 h-10 text-neutral-600 mx-auto" />
            <p className="text-base font-bold text-white">Пока нет ставок с известным исходом и вердиктом сети</p>
            <p className="text-xs text-neutral-400 max-w-md mx-auto">
              Как только матчи с прогнозами завершатся, здесь появится статистика угадывания по видам спорта и типам ставок.
            </p>
          </div>
        ) : (
          <>
            {/* Overall summary */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-neutral-400 font-mono uppercase flex items-center justify-center gap-1">
                  <Target className="w-3 h-3" /> Всего оценено
                </div>
                <div className="text-xl font-black text-white font-mono mt-1">{overall.judged.toLocaleString()}</div>
              </div>
              <div className="bg-neutral-900/90 border border-[#00b894]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#55efc4] font-mono uppercase font-bold flex items-center justify-center gap-1">
                  <CheckCircle2 className="w-3 h-3" /> Угадано
                </div>
                <div className="text-xl font-black text-[#55efc4] font-mono mt-1">{overall.correct.toLocaleString()}</div>
              </div>
              <div className="bg-neutral-900/90 border border-[#d63031]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#ff7675] font-mono uppercase font-bold flex items-center justify-center gap-1">
                  <XCircle className="w-3 h-3" /> Не угадано
                </div>
                <div className="text-xl font-black text-[#ff7675] font-mono mt-1">{overall.incorrect.toLocaleString()}</div>
              </div>
              <div className="bg-neutral-900/90 border border-[#fdcb6e]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#ffeaa7] font-mono uppercase font-bold flex items-center justify-center gap-1">
                  <Trophy className="w-3 h-3" /> Процент угадывания
                </div>
                <div className="text-xl font-black text-[#ffeaa7] font-mono mt-1">
                  {overall.guess_rate_pct != null ? `${overall.guess_rate_pct.toFixed(1)}%` : "—"}
                </div>
              </div>
            </div>

            {/* Sport tabs */}
            <div className="flex flex-wrap items-center gap-2">
              {sports.map((s) => (
                <button
                  key={s.sport}
                  onClick={() => setSelectedSport(s.sport)}
                  className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-xs md:text-sm font-bold whitespace-nowrap transition border ${
                    selectedSport === s.sport
                      ? "bg-gradient-to-r from-[#0984e3] to-[#74b9ff] text-neutral-950 border-transparent shadow-lg shadow-[#0984e3]/20"
                      : "bg-neutral-900/90 border-neutral-800 text-neutral-300 hover:border-neutral-600"
                  }`}
                >
                  {s.sport}
                  <span
                    className={`text-[10px] font-mono px-1.5 py-0.5 rounded-full ${
                      selectedSport === s.sport ? "bg-neutral-950/20 text-neutral-950" : "bg-neutral-800 text-neutral-400"
                    }`}
                  >
                    {s.guess_rate_pct.toFixed(1)}% · {s.judged.toLocaleString()}
                  </span>
                </button>
              ))}
            </div>

            {/* Selected sport: summary + per-bet-type breakdown */}
            {activeSport && (
              <div className="space-y-4">
                <div className="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 md:p-5 flex flex-wrap items-center gap-x-6 gap-y-2">
                  <h3 className="text-base font-bold text-white">{activeSport.sport}</h3>
                  <span className="text-xs text-neutral-400">
                    Оценено: <span className="text-neutral-200 font-mono font-semibold">{activeSport.judged.toLocaleString()}</span>
                  </span>
                  <span className="text-xs text-[#55efc4]">
                    Угадано: <span className="font-mono font-semibold">{activeSport.correct.toLocaleString()}</span>
                  </span>
                  <span className="text-xs text-[#ff7675]">
                    Не угадано: <span className="font-mono font-semibold">{activeSport.incorrect.toLocaleString()}</span>
                  </span>
                  <span className="text-xs text-[#ffeaa7] ml-auto font-bold">
                    {activeSport.guess_rate_pct.toFixed(1)}% угадано
                  </span>
                </div>

                {/* Bet-type search */}
                <div className="relative w-full sm:w-72">
                  <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-neutral-500" />
                  <input
                    type="text"
                    placeholder="Поиск по типу ставки..."
                    value={betTypeSearch}
                    onChange={(e) => setBetTypeSearch(e.target.value)}
                    className="w-full bg-neutral-950 border border-neutral-800 rounded-xl pl-10 pr-4 py-2 text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-[#0984e3] transition"
                  />
                </div>

                {groupedBetTypes.length === 0 ? (
                  <div className="text-center text-sm text-neutral-500 py-8">
                    Ничего не найдено по запросу «{betTypeSearch}»
                  </div>
                ) : (
                  groupedBetTypes.map(({ group, betTypes, judged, correct, incorrect, guess_rate_pct }) => {
                    const isCollapsed = collapsedGroups.has(group)
                    return (
                      <div key={group} className="space-y-3">
                        <button
                          onClick={() => toggleGroup(group)}
                          className="w-full flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-neutral-800 pb-2 text-left hover:border-neutral-600 transition"
                        >
                          <ChevronDown
                            className={`w-4 h-4 text-neutral-400 shrink-0 transition-transform ${isCollapsed ? "-rotate-90" : ""}`}
                          />
                          <h4 className="text-sm font-bold text-white">{group}</h4>
                          <span className="text-[11px] text-neutral-400">
                            Оценено: <span className="text-neutral-200 font-mono font-semibold">{judged.toLocaleString()}</span>
                          </span>
                          <span className="text-[11px] text-[#55efc4]">
                            Угадано: <span className="font-mono font-semibold">{correct.toLocaleString()}</span>
                          </span>
                          <span className="text-[11px] text-[#ff7675]">
                            Не угадано: <span className="font-mono font-semibold">{incorrect.toLocaleString()}</span>
                          </span>
                          <span className="text-[11px] text-[#ffeaa7] ml-auto font-bold">
                            {guess_rate_pct.toFixed(1)}% угадано
                          </span>
                        </button>

                        {!isCollapsed && (
                          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                            {betTypes.map((bt) => (
                              <div
                                key={`${bt.factor_id}-${bt.label}`}
                                className="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 space-y-2.5 backdrop-blur"
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <h4 className="text-sm font-bold text-white leading-snug break-words">{bt.label}</h4>
                                  <span className="text-sm font-black font-mono text-[#ffeaa7] shrink-0">
                                    {bt.guess_rate_pct.toFixed(1)}%
                                  </span>
                                </div>
                                <GuessRatioBar correct={bt.correct} incorrect={bt.incorrect} />
                                <div className="flex items-center justify-between text-[10px] font-mono text-neutral-400 gap-2">
                                  <span className="text-[#55efc4]">🟢 {bt.correct.toLocaleString()}</span>
                                  <span className="text-neutral-500 whitespace-nowrap">Всего: {bt.judged.toLocaleString()}</span>
                                  <span className="text-[#ff7675]">🔴 {bt.incorrect.toLocaleString()}</span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })
                )}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  )
}
