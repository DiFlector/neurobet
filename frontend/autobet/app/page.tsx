"use client"

import { useEffect, useState, useCallback } from "react"
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

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

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

  const sportCategories = [
    { id: "all", label: "Все виды спорта" },
    { id: "Футбол", label: "⚽ Футбол" },
    { id: "Хоккей", label: "🏒 Хоккей" },
    { id: "Баскетбол", label: "🏀 Баскетбол" },
    { id: "Теннис", label: "🎾 Теннис" },
    { id: "Киберспорт", label: "🎮 Киберспорт" },
  ]

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      <HeaderNav
        stats={stats}
        matchesCount={matches.length}
        triggeringScrape={triggeringScrape}
        onManualTrigger={handleManualTrigger}
      />

      {/* Main Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Controls Bar: Search & Sport Filters */}
        <div className="bg-neutral-900/60 border border-neutral-800 rounded-2xl p-4 space-y-4 backdrop-blur-md">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            {/* Search Input */}
            <div className="relative w-full md:w-80">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-neutral-500" />
              <input
                type="text"
                placeholder="Поиск по названию команды или матча..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full bg-neutral-950 border border-neutral-800 rounded-xl pl-10 pr-4 py-2 text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-[#fdcb6e] transition"
              />
            </div>

            {/* Controls right: Safe Mode & Auto-refresh */}
            <div className="flex flex-wrap items-center gap-3 w-full md:w-auto justify-end">
              {/* Safe Mode Toggle Button */}
              <button
                onClick={() => setSafeMode(!safeMode)}
                className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all shadow-md ${
                  safeMode
                    ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894] shadow-[#00b894]/10"
                    : "bg-neutral-950 text-neutral-300 border border-neutral-800 hover:bg-neutral-800"
                }`}
              >
                {safeMode ? (
                  <>
                    <ShieldCheck className="w-4 h-4 text-[#00b894]" />
                    <span>Безопасный режим (1.1 - 2.1)</span>
                  </>
                ) : (
                  <>
                    <ShieldAlert className="w-4 h-4 text-[#fdcb6e]" />
                    <span>Показывать все коэффициенты</span>
                  </>
                )}
              </button>

              {/* Auto-refresh toggle */}
              <label className="flex items-center gap-2 cursor-pointer text-xs font-medium text-neutral-300 bg-neutral-950 px-3 py-2 rounded-xl border border-neutral-800">
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                  className="rounded bg-neutral-950 border-neutral-800 text-[#00b894] focus:ring-[#00b894]"
                />
                Авто-обновление 10с
              </label>
            </div>
          </div>

          {/* Sport Tabs */}
          <div className="flex items-center gap-2 overflow-x-auto pb-1 custom-scrollbar">
            {sportCategories.map((sport) => (
              <button
                key={sport.id}
                onClick={() => setSelectedSport(sport.id)}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-semibold whitespace-nowrap transition-all ${
                  selectedSport === sport.id
                    ? "bg-[#fdcb6e] text-neutral-950 shadow-md shadow-[#fdcb6e]/20"
                    : "bg-neutral-950 text-neutral-400 hover:text-white hover:bg-neutral-800 border border-neutral-800"
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
            <AlertCircle className="w-8 h-8 text-[#ff7675] mx-auto" />
            <h3 className="text-base font-bold text-[#ff7675]">Ошибка взаимодействия с бэкендом</h3>
            <p className="text-xs text-[#ff7675]/80">{error}</p>
            <p className="text-xs text-neutral-400">
              Убедитесь, что бэкенд запущен в Docker или локально на порту 8000.
            </p>
          </div>
        ) : loading ? (
          <div className="py-20 text-center space-y-3">
            <RefreshCw className="w-8 h-8 text-[#fdcb6e] animate-spin mx-auto" />
            <p className="text-sm font-medium text-neutral-400">Загрузка LIVE матчей...</p>
          </div>
        ) : matches.length === 0 ? (
          <div className="bg-neutral-900/40 border border-neutral-800/80 rounded-2xl py-16 text-center space-y-3">
            <Filter className="w-10 h-10 text-neutral-600 mx-auto" />
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
        Fonbet Live Odds Scraper System &copy; 2026. Standard neutral dark theme & Flat UI Colors US Palette accents.
      </footer>
    </div>
  )
}
