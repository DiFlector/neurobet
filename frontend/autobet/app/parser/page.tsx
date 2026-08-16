"use client"

import { useEffect, useState, useCallback } from "react"
import { motion } from "framer-motion"
import {
  Search,
  RefreshCw,
  Database,
  Filter,
  AlertCircle,
  Radio,
  ShieldCheck,
  ShieldAlert
} from "lucide-react"
import { MatchCard } from "@/components/MatchCard"
import { HeaderNav } from "@/components/HeaderNav"
import { SPORT_FILTER_OPTIONS } from "@/lib/sports"

interface MatchData {
  event_id: number
  sport_id: number
  sport_path: string
  match_name: string
  team_1: string
  team_2: string
  score_1: number
  score_2: number
  score: string
  timer: string
  is_live: number
  sub_markets: Array<{ sub_event_id: number; market_name: string; odds_count: number }>
  total_odds_count: number
  odds: Array<{
    factor_id: number
    market_prefix: string
    label: string
    parameter: string
    coefficient: number
  }>
}

interface StatsData {
  live_events_count: number
  total_events_count: number
  total_odds_history_count: number
  last_updated_at: string
  db_size_bytes?: number
  db_size_formatted?: string
}

export default function Page() {
  const [matches, setMatches] = useState<MatchData[]>([])
  const [stats, setStats] = useState<StatsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [search, setSearch] = useState("")
  const [selectedSport, setSelectedSport] = useState("all")
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [triggeringScrape, setTriggeringScrape] = useState(false)
  const [safeMode, setSafeMode] = useState(true)

  // See app/neurobets/page.tsx for why this defaults to "" (same-origin, proxied by
  // next.config.ts) instead of an absolute localhost URL.
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""

  const fetchMatches = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (selectedSport !== "all") params.append("sport", selectedSport)
      if (search.trim()) params.append("search", search.trim())

      const res = await fetch(`${API_BASE}/api/matches?${params.toString()}`)
      if (!res.ok) throw new Error("Failed to fetch live matches")
      const data = await res.json()
      setMatches(data.matches || [])
      setError(null)
    } catch (err: any) {
      setError(err.message || "Failed to connect to backend API")
    } finally {
      setLoading(false)
    }
  }, [API_BASE, selectedSport, search])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/stats`)
      if (res.ok) {
        const data = await res.json()
        setStats(data.stats)
      }
    } catch (err) {
      // Ignore stats error
    }
  }, [API_BASE])

  useEffect(() => {
    fetchMatches()
    fetchStats()
  }, [fetchMatches, fetchStats])

  // Auto-refresh every 10 seconds
  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(() => {
      fetchMatches()
      fetchStats()
    }, 10000)
    return () => clearInterval(interval)
  }, [autoRefresh, fetchMatches, fetchStats])

  const handleManualTrigger = async () => {
    setTriggeringScrape(true)
    try {
      await fetch(`${API_BASE}/api/trigger-scrape`, { method: "POST" })
      setTimeout(() => {
        fetchMatches()
        fetchStats()
        setTriggeringScrape(false)
      }, 2000)
    } catch (err) {
      setTriggeringScrape(false)
    }
  }

  const sportCategories = SPORT_FILTER_OPTIONS

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      <HeaderNav
        stats={stats}
        matchesCount={matches.length}
      />

      {/* Main Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Controls Bar: Search & Sport Filters */}
        <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-4 space-y-4 backdrop-blur-md">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            {/* Search Input — full width row on its own, matches the neurobets page pattern */}
            <div className="relative flex-1 min-w-[220px]">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-neutral-500" />
              <input
                type="text"
                placeholder="Поиск по названию команды или матча..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full bg-neutral-950 border border-neutral-800 rounded-full pl-10 pr-4 py-2.5 text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-[#fdcb6e] transition"
              />
            </div>

            {/* Controls right: Safe Mode segmented toggle, Auto-refresh, manual trigger */}
            <div className="self-start lg:self-auto flex flex-wrap items-center gap-2 shrink-0">
              {/* Safe Mode — segmented control with a sliding indicator, same language as the neurobets page */}
              <div className="relative inline-flex items-center gap-1 bg-neutral-950 p-1 rounded-xl border border-neutral-800">
                <button
                  onClick={() => setSafeMode(true)}
                  className={`relative flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold transition-colors ${
                    safeMode ? "text-neutral-950" : "text-neutral-400 hover:text-white"
                  }`}
                >
                  {safeMode && (
                    <motion.div
                      layoutId="safeModeIndicator"
                      layoutDependency={safeMode}
                      className="absolute inset-0 rounded-lg bg-[#00b894] shadow-sm shadow-[#00b894]/20"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <ShieldCheck className="relative z-10 w-3.5 h-3.5" />
                  <span className="relative z-10">Безопасный (1.10–2.10)</span>
                </button>
                <button
                  onClick={() => setSafeMode(false)}
                  className={`relative flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-bold transition-colors ${
                    !safeMode ? "text-neutral-950" : "text-neutral-400 hover:text-white"
                  }`}
                >
                  {!safeMode && (
                    <motion.div
                      layoutId="safeModeIndicator"
                      layoutDependency={safeMode}
                      className="absolute inset-0 rounded-lg bg-[#fdcb6e] shadow-sm shadow-[#fdcb6e]/20"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <ShieldAlert className="relative z-10 w-3.5 h-3.5" />
                  <span className="relative z-10">Все коэффициенты</span>
                </button>
              </div>

              {/* Auto-refresh toggle */}
              <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-neutral-300 bg-neutral-950 px-3.5 py-2.5 rounded-xl border border-neutral-800 hover:border-neutral-700 transition-colors">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="rounded bg-neutral-950 border-neutral-800 text-[#00b894] focus:ring-[#00b894]"
                />
                Авто-обновление 10с
              </label>

              {/* Manual Scrape Trigger */}
              <button
                onClick={handleManualTrigger}
                disabled={triggeringScrape}
                className="flex items-center gap-1.5 bg-gradient-to-r from-[#fdcb6e] to-[#ffeaa7] hover:brightness-105 text-neutral-950 font-bold px-3.5 py-2.5 rounded-xl transition text-xs shadow-md shadow-[#fdcb6e]/20 disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${triggeringScrape ? "animate-spin" : ""}`} />
                Спарсить
              </button>
            </div>
          </div>

          {/* Sport Tabs */}
          <div className="flex flex-wrap items-center gap-2 pt-3 border-t border-neutral-800/80">
            {sportCategories.map((sport) => (
              <button
                key={sport.id}
                onClick={() => setSelectedSport(sport.id)}
                className={`px-3.5 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-colors border ${
                  selectedSport === sport.id
                    ? "bg-[#fdcb6e] text-neutral-950 border-[#fdcb6e] font-bold shadow-sm shadow-[#fdcb6e]/20"
                    : "bg-neutral-950 text-neutral-400 hover:bg-neutral-800 hover:text-neutral-200 border-neutral-800/60"
                }`}
              >
                {sport.label}
              </button>
            ))}
          </div>
        </div>

        {/* Content Section */}
        {error ? (
          <div className="bg-[#d63031]/10 border border-[#d63031]/30 rounded-2xl p-6 text-center space-y-2">
            <div className="w-12 h-12 rounded-full bg-[#d63031]/15 border border-[#d63031]/30 flex items-center justify-center mx-auto">
              <AlertCircle className="w-5 h-5 text-[#ff7675]" />
            </div>
            <h3 className="text-base font-bold text-[#ff7675]">Ошибка взаимодействия с бэкендом</h3>
            <p className="text-xs text-[#ff7675]/80">{error}</p>
            <p className="text-xs text-neutral-400">
              Убедитесь, что бэкенд запущен в Docker или локально на порту 8000.
            </p>
          </div>
        ) : loading ? (
          <div className="py-20 text-center space-y-3">
            <div className="w-12 h-12 rounded-full bg-[#fdcb6e]/10 border border-[#fdcb6e]/30 flex items-center justify-center mx-auto">
              <RefreshCw className="w-5 h-5 text-[#fdcb6e] animate-spin" />
            </div>
            <p className="text-sm font-medium text-neutral-400">Загрузка LIVE матчей...</p>
          </div>
        ) : matches.length === 0 ? (
          <div className="bg-neutral-900/40 border border-neutral-800/80 rounded-2xl py-16 text-center space-y-3">
            <div className="w-14 h-14 rounded-full bg-neutral-800/60 border border-neutral-700 flex items-center justify-center mx-auto">
              <Filter className="w-6 h-6 text-neutral-500" />
            </div>
            <h3 className="text-base font-bold text-neutral-300">Матчи не найдены</h3>
            <p className="text-xs text-neutral-500">
              Попробуйте изменить поисковый запрос или выбрать другой вид спорта.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {matches.map((match) => (
              <MatchCard
                key={match.event_id}
                eventId={match.event_id}
                sportPath={match.sport_path}
                matchName={match.match_name}
                team1={match.team_1}
                team2={match.team_2}
                score1={match.score_1}
                score2={match.score_2}
                score={match.score}
                timer={match.timer}
                isLive={Boolean(match.is_live)}
                totalOddsCount={match.total_odds_count}
                odds={match.odds || []}
                subMarkets={match.sub_markets || []}
                safeMode={safeMode}
              />
            ))}
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-neutral-900 bg-neutral-950 py-4 px-6 text-center text-xs text-neutral-500">
        Нейроставки &copy; 2026 — AI прогнозы ставок
      </footer>
    </div>
  )
}
