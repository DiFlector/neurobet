"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { Radio, Database, RefreshCw, Cpu, ShieldCheck } from "lucide-react"

interface StatsData {
  live_events_count: number
  total_events_count: number
  total_odds_history_count: number
  last_updated_at: string
  db_size_bytes?: number
  db_size_formatted?: string
}

interface HeaderNavProps {
  stats?: StatsData | null
  matchesCount?: number
  triggeringScrape?: boolean
  onManualTrigger?: () => void
}

export function HeaderNav({
  stats,
  matchesCount = 0,
  triggeringScrape = false,
  onManualTrigger
}: HeaderNavProps) {
  const pathname = usePathname()

  return (
    <header className="sticky top-0 z-40 bg-neutral-900/90 backdrop-blur-md border-b border-neutral-800">
      <div className="max-w-7xl mx-auto px-4 py-3 flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Left: Brand & Navigation Links */}
        <div className="flex flex-col sm:flex-row items-center gap-4 w-full md:w-auto">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-[#fdcb6e] to-[#ffeaa7] flex items-center justify-center shadow-lg shadow-[#fdcb6e]/20 text-neutral-950 font-black text-xl">
              ⚡
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-white flex items-center gap-2">
                Fonbet LIVE Parser
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/30">
                  v2.0
                </span>
              </h1>
              <p className="text-xs text-neutral-400">
                Автоматический парсинг & AI прогнозы ставок
              </p>
            </div>
          </div>

          {/* Nav Tabs */}
          <nav className="flex items-center gap-1 bg-neutral-950 p-1 rounded-xl border border-neutral-800 ml-0 sm:ml-4">
            <Link
              href="/"
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                pathname === "/"
                  ? "bg-neutral-800 text-white shadow"
                  : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900"
              }`}
            >
              <Radio className="w-3.5 h-3.5 text-[#ff7675]" />
              LIVE Парсер
            </Link>
            <Link
              href="/neurobets"
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                pathname === "/neurobets"
                  ? "bg-[#fdcb6e]/20 text-[#ffeaa7] border border-[#fdcb6e]/40 shadow-sm"
                  : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900"
              }`}
            >
              <Cpu className="w-3.5 h-3.5 text-[#fdcb6e]" />
              Нейроставки
              <span className="text-[9px] font-mono px-1.5 py-0.2 rounded-full bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30">
                AI TOP
              </span>
            </Link>
            <Link
              href="/admin"
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition ${
                pathname === "/admin"
                  ? "bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/40 shadow-sm"
                  : "text-neutral-400 hover:text-neutral-200 hover:bg-neutral-900"
              }`}
            >
              <ShieldCheck className="w-3.5 h-3.5 text-[#00b894]" />
              Админка
            </Link>
          </nav>
        </div>

        {/* Right: Stats Badges & Scrape Trigger */}
        <div className="flex items-center gap-3 overflow-x-auto max-w-full pb-1 md:pb-0">
          <div className="flex items-center gap-2 bg-neutral-950 px-3 py-1.5 rounded-xl border border-neutral-800 text-xs">
            <Radio className="w-4 h-4 text-[#ff7675] animate-pulse" />
            <span className="text-neutral-400">LIVE матчи:</span>
            <span className="font-mono font-bold text-[#fdcb6e]">
              {stats ? stats.live_events_count : matchesCount}
            </span>
          </div>

          <div className="flex items-center gap-2 bg-neutral-950 px-3 py-1.5 rounded-xl border border-neutral-800 text-xs">
            <Database className="w-4 h-4 text-[#0984e3]" />
            <span className="text-neutral-400">История кэф:</span>
            <span className="font-mono font-bold text-[#fdcb6e]">
              {stats ? stats.total_odds_history_count.toLocaleString() : "0"}
            </span>
          </div>

          {onManualTrigger && (
            <button
              onClick={onManualTrigger}
              disabled={triggeringScrape}
              className="flex items-center gap-1.5 bg-[#fdcb6e] hover:bg-[#ffeaa7] text-neutral-950 font-bold px-3 py-1.5 rounded-xl transition text-xs shadow-md shadow-[#fdcb6e]/20 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${triggeringScrape ? "animate-spin" : ""}`} />
              Спарсить
            </button>
          )}
        </div>
      </div>
    </header>
  )
}
