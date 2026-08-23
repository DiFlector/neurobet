"use client"

import { useState, useMemo, useEffect, useCallback, useRef } from "react"
import { AnimatePresence, motion, useMotionValue, animate as animateValue } from "framer-motion"
import {
  BrainCircuit,
  TrendingUp,
  ShieldCheck,
  Zap,
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
  Loader2,
  ChevronDown,
  Search,
  Copy,
  Check,
} from "lucide-react"
import { HeaderNav } from "@/components/HeaderNav"
import { SPORT_FILTER_OPTIONS } from "@/lib/sports"

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
  stake: number | null // сумма, которую бот реально поставил на этот исход (₽), null если не ставил
  potentialPayout: number | null // stake * coefficient — сколько получит при выигрыше
  predictedWin: number | null // 1 = EV ≥ порога (класть деньги), 0 = не ставить, null = не оценено
  willWin: number | null // 1 = исход зайдёт; 0 = скорее всего не зайдёт; null = нет вызова (скип, не проигрыш)
}

function liveBetKey(eventId: any, factorId: any, parameter: any, marketPrefix: any): string {
  return `${eventId}-${factorId}-${parameter}-${marketPrefix}`
}

// b.placed_at is an ISO timestamp (UTC) — render it in the viewer's local time, HH:MM:SS.
function formatPlacedAt(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d.getTime())) return null
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
}

// Animated ring gauge for the banner's "точность модели" metric — eases the stroke
// (and the number ticking up inside it) from 0 to `pct` on mount / whenever the
// underlying stats change, instead of just snapping to the new value.
function AccuracyRing({ pct, known, size = 68 }: { pct: number; known: boolean; size?: number }) {
  const stroke = Math.max(6, Math.round(size * 0.09))
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const progress = useMotionValue(0)
  const [display, setDisplay] = useState(0)

  useEffect(() => {
    const controls = animateValue(progress, known ? pct : 0, {
      duration: 1.2,
      ease: "easeOut",
      onUpdate: (v) => setDisplay(v)
    })
    return () => controls.stop()
  }, [pct, known, progress])

  const offset = circumference - (Math.max(0, Math.min(100, display)) / 100) * circumference

  const glowId = `accuracy-ring-glow-${size}`

  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      {/* Rotation and blur both live inside the SVG's own coordinate space (an SVG
          feGaussianBlur filter + an animated <g transform="rotate(...)">) instead of a
          CSS `filter` on an HTML layer spun by `transform`. That combination is what was
          reading as a jagged/rough edge — the browser re-rasterizes a CSS filter's raster
          layer whenever the rotation lands on a sub-pixel angle. Keeping everything native
          SVG lets the renderer redraw the blurred arc at full precision every frame. */}
      <svg width={size} height={size} className="overflow-visible" shapeRendering="geometricPrecision">
        <defs>
          <filter id={glowId} x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation={stroke * 0.55} />
          </filter>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="#262626" strokeWidth={stroke} />
        <motion.g
          animate={known ? { rotate: 360 } : { rotate: 0 }}
          transition={known ? { duration: 6, repeat: Infinity, ease: "linear" } : { duration: 0 }}
          style={{ transformOrigin: `${size / 2}px ${size / 2}px` }}
        >
          <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
            {known && (
              <circle
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="none"
                stroke="#55efc4"
                strokeWidth={stroke + 3}
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={offset}
                opacity={0.8}
                filter={`url(#${glowId})`}
              />
            )}
            <circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={known ? "#55efc4" : "#525252"}
              strokeWidth={stroke}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={offset}
            />
          </g>
        </motion.g>
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="font-black font-mono text-white" style={{ fontSize: Math.max(13, Math.round(size * 0.19)) }}>
          {known ? `${display.toFixed(1)}%` : "—"}
        </span>
      </div>
    </div>
  )
}

const PAGE_SIZE = 20
const SETTLED_BOT_BETS_LIMIT = 30
const FETCH_TIMEOUT_MS = 12_000

function fetchApi(input: string, init?: RequestInit): Promise<Response> {
  const timeout = AbortSignal.timeout(FETCH_TIMEOUT_MS)
  const signal = init?.signal && typeof AbortSignal.any === "function"
    ? AbortSignal.any([init.signal, timeout])
    : (init?.signal ?? timeout)
  const { signal: _ignored, ...rest } = init || {}
  return fetch(input, { ...rest, signal })
}

// Fires onIntersect once the sentinel scrolls near the viewport, so the next
// page of results loads before the user actually hits the bottom.
function LoadMoreSentinel({ onIntersect, disabled }: { onIntersect: () => void; disabled: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (disabled) return
    const el = ref.current
    if (!el) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) onIntersect()
      },
      { rootMargin: "600px" }
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [onIntersect, disabled])

  return <div ref={ref} className="h-1" />
}

export default function NeurobetsPage() {
  const [activeTab, setActiveTab] = useState<"live" | "history">("live")
  const [sortMode, setSortMode] = useState<"best" | "safe">("best")
  const [selectedSport, setSelectedSport] = useState<string>("all")
  const [stats, setStats] = useState<any>(null)
  const [headlineAccuracy, setHeadlineAccuracy] = useState<{
    guess_rate_pct: number | null
    miss_rate_pct: number | null
  } | null>(null)
  const [bankroll, setBankroll] = useState<any>(null)
  const [openBetsCount, setOpenBetsCount] = useState(0)
  const [openBotBetsList, setOpenBotBetsList] = useState<any[]>([])
  const [openBetsJsonCopied, setOpenBetsJsonCopied] = useState(false)
  const [settledBotBetsList, setSettledBotBetsList] = useState<any[]>([])
  const [botBetsLoaded, setBotBetsLoaded] = useState(false)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const [liveBets, setLiveBets] = useState<NeuroBet[]>([])
  const [liveTotal, setLiveTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadingMoreLive, setLoadingMoreLive] = useState(false)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [verdictFilter, setVerdictFilter] = useState<"win" | "loss" | "all">("win")
  // searchInput updates on every keystroke (controls the text field); searchQuery is the
  // debounced value that actually drives fetches, so typing doesn't fire a request (and a
  // multi-word ILIKE ALL scan) per character.
  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")

  // History State
  const [historyItems, setHistoryItems] = useState<any[]>([])
  const [historySummary, setHistorySummary] = useState<any>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [loadingMoreHistory, setLoadingMoreHistory] = useState(false)
  const [historyOutcomeFilter, setHistoryOutcomeFilter] = useState<"all" | "correct" | "incorrect" | "push" | "pending">("all")

  // Empty by default so fetches go to a same-origin relative "/api/..." path, proxied
  // server-side to the backend by next.config.ts's rewrite — see that file for why.
  // Set NEXT_PUBLIC_API_URL only if you deliberately want the browser to hit the
  // backend directly on its own origin/port instead.
  const API_BASE = process.env.NEXT_PUBLIC_API_URL || ""
  const neurobetsRequestId = useRef(0)
  const historyRequestId = useRef(0)
  const historyAbortRef = useRef<AbortController | null>(null)
  // How many rows are currently loaded for each infinite-scroll list — kept in a ref
  // (not state) so it can be used as the next "offset" without retriggering fetches.
  const liveOffsetRef = useRef(0)
  const historyOffsetRef = useRef(0)
  // The bot's currently-open real stakes, keyed by liveBetKey(...) — kept in a ref so
  // fetchNeurobets can read the latest snapshot without needing it as a dependency
  // (avoids refetching predictions just because the bet list refreshed).
  const openBotBetsRef = useRef<Map<string, { stake: number; coefficient: number }>>(new Map())

  // Debounce the search box — waits 300ms after the user stops typing before it actually
  // drives a fetch, so a multi-word ILIKE ALL scan doesn't fire on every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setSearchQuery(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  // Reset the infinite-scroll window whenever the underlying result set changes shape
  // (filters/sort). Also clear the current list and show the loading state so switching
  // tabs/filters never mixes stale cards from the previous mode/filter with the new ones.
  useEffect(() => {
    liveOffsetRef.current = 0
    setLiveBets([])
    setLoading(true)
  }, [sortMode, selectedSport, verdictFilter, searchQuery])

  useEffect(() => {
    historyOffsetRef.current = 0
    setHistoryItems([])
    setHistoryLoading(true)
  }, [selectedSport, activeTab, historyOutcomeFilter])

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetchApi(`${API_BASE}/api/stats`, { cache: "no-store" })
      if (res.ok) {
        const data = await res.json()
        setStats(data.stats)
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  // The ring must not rely on /api/stats alone — ad blockers often silently block
  // URLs containing "stats" while /api/neurobets/* still works (bankroll, top, etc.).
  const fetchHeadlineAccuracy = useCallback(async () => {
    try {
      const res = await fetchApi(`${API_BASE}/api/neurobets/headline-accuracy`, { cache: "no-store" })
      if (res.ok) {
        const data = await res.json()
        setHeadlineAccuracy({
          guess_rate_pct: data.guess_rate_pct ?? null,
          miss_rate_pct: data.miss_rate_pct ?? null,
        })
      }
    } catch (err) {
      // Ignore
    }
  }, [API_BASE])

  const fetchBankroll = useCallback(async () => {
    try {
      const res = await fetchApi(`${API_BASE}/api/neurobets/bankroll?include_ledger=false`)
      if (res.ok) {
        const data = await res.json()
        setBankroll(data)
      }
    } catch (err) {
      // Ignore — panel just keeps showing the last known state
    }
  }, [API_BASE])

  // Lightweight badge-only refreshes — fetch just the count for whichever tab is NOT
  // currently active, so both tab badges keep updating themselves in the background
  // instead of only refreshing when the user actually switches to that tab.
  const fetchLiveTotal = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        sort: sortMode,
        limit: "1",
        offset: "0",
        verdict: verdictFilter
      })
      if (selectedSport !== "all") params.append("sport", selectedSport)
      if (searchQuery) params.append("search", searchQuery)
      const res = await fetchApi(`${API_BASE}/api/neurobets/top?${params.toString()}`)
      if (res.ok) {
        const data = await res.json()
        if (typeof data.total === "number") setLiveTotal(data.total)
      }
    } catch (err) {
      // Ignore — badge just keeps showing the last known count
    }
  }, [API_BASE, sortMode, selectedSport, verdictFilter, searchQuery])

  const fetchHistoryTotal = useCallback(async () => {
    try {
      const params = new URLSearchParams()
      if (selectedSport !== "all") params.append("sport", selectedSport)
      if (historyOutcomeFilter !== "all") params.append("outcome", historyOutcomeFilter)
      const res = await fetchApi(`${API_BASE}/api/neurobets/history-summary?${params.toString()}`, { cache: "no-store" })
      if (res.ok) {
        const data = await res.json()
        if (data.summary) setHistorySummary(data.summary)
      }
    } catch (err) {
      // Ignore — badge just keeps showing the last known count
    }
  }, [API_BASE, selectedSport, historyOutcomeFilter])

  // Open + settled bot stakes are two cheap queries (not one mixed dump of 200
  // rows that then joined every finished_bets market for those events).
  const fetchOpenBotBets = useCallback(async () => {
    try {
      const [openRes, settledRes] = await Promise.allSettled([
        fetchApi(`${API_BASE}/api/neurobets/live-bets?status=open&limit=50`),
        fetchApi(`${API_BASE}/api/neurobets/live-bets?status=settled&limit=${SETTLED_BOT_BETS_LIMIT}`),
      ])
      if (openRes.status === "fulfilled" && openRes.value.ok) {
        const data = await openRes.value.json()
        const map = new Map<string, { stake: number; coefficient: number }>()
        const openList: any[] = data.items || []
        for (const b of openList) {
          map.set(liveBetKey(b.event_id, b.factor_id, b.parameter, b.market_prefix), {
            stake: b.stake,
            coefficient: b.coefficient,
          })
        }
        openBotBetsRef.current = map
        setOpenBetsCount(typeof data.total === "number" ? data.total : map.size)
        setOpenBotBetsList(openList)
      }
      if (settledRes.status === "fulfilled" && settledRes.value.ok) {
        const data = await settledRes.value.json()
        setSettledBotBetsList(data.items || [])
      }
    } catch (err) {
      // Ignore
    } finally {
      setBotBetsLoaded(true)
    }
  }, [API_BASE])

  const fetchHistory = useCallback(async (offset: number, limit: number, mode: "replace" | "append") => {
    if (mode === "append") setLoadingMoreHistory(true)
    else setHistoryLoading(true)
    const requestId = ++historyRequestId.current
    if (mode === "replace") {
      historyAbortRef.current?.abort()
      historyAbortRef.current = new AbortController()
    }
    const ac = historyAbortRef.current
    try {
      const params = new URLSearchParams({
        limit: limit.toString(),
        offset: offset.toString(),
        include_summary: "false",
      })
      if (selectedSport !== "all") {
        params.append("sport", selectedSport)
      }
      if (historyOutcomeFilter !== "all") {
        params.append("outcome", historyOutcomeFilter)
      }
      const res = await fetchApi(`${API_BASE}/api/neurobets/history?${params.toString()}`, {
        cache: "no-store",
        signal: ac?.signal,
      })
      if (requestId !== historyRequestId.current) return
      if (res.ok) {
        const data = await res.json()
        if (requestId !== historyRequestId.current) return
        const items = data.history || []
        setHistoryItems((prev) => (mode === "append" ? [...prev, ...items] : items))
        if (data.summary) setHistorySummary(data.summary)
        historyOffsetRef.current = offset + items.length
      }
    } catch (err) {
      if ((err as { name?: string })?.name === "AbortError") return
      if (requestId === historyRequestId.current && mode === "replace") setHistoryItems([])
    } finally {
      if (requestId === historyRequestId.current) {
        setHistoryLoading(false)
        setLoadingMoreHistory(false)
      }
    }
  }, [API_BASE, selectedSport, historyOutcomeFilter])

  const loadMoreHistory = useCallback(() => {
    if (loadingMoreHistory || historyLoading) return
    if (historyOffsetRef.current >= (historySummary?.filtered_count ?? historySummary?.total_count ?? 0)) return
    fetchHistory(historyOffsetRef.current, PAGE_SIZE, "append")
  }, [fetchHistory, loadingMoreHistory, historyLoading, historySummary])

  const copyOpenBotBetsJson = useCallback(async () => {
    const payload = openBotBetsList.map((b) => ({
      id: b.id,
      event_id: b.event_id,
      factor_id: b.factor_id,
      parameter: b.parameter ?? "",
      market_prefix: b.market_prefix ?? "",
      label: b.label ?? "",
      match_name: b.match_name ?? "",
      sport_path: b.sport_path ?? "",
      stake: b.stake,
      coefficient: b.coefficient,
      current_coefficient: b.current_coefficient ?? null,
      win_probability: b.win_probability,
      expected_roi: b.expected_roi ?? null,
      current_predicted_win: b.current_predicted_win ?? null,
      current_expected_roi: b.current_expected_roi ?? null,
      status: b.status,
      placed_at: b.placed_at ?? null,
      current_score: b.current_score ?? null,
      current_timer: b.current_timer ?? null,
      match_is_live: Boolean(b.match_is_live),
    }))
    const text = JSON.stringify(payload, null, 2)
    try {
      await navigator.clipboard.writeText(text)
      setOpenBetsJsonCopied(true)
      window.setTimeout(() => setOpenBetsJsonCopied(false), 2000)
    } catch {
      // Fallback for older browsers / insecure contexts
      const ta = document.createElement("textarea")
      ta.value = text
      ta.setAttribute("readonly", "")
      ta.style.position = "fixed"
      ta.style.left = "-9999px"
      document.body.appendChild(ta)
      ta.select()
      try {
        document.execCommand("copy")
        setOpenBetsJsonCopied(true)
        window.setTimeout(() => setOpenBetsJsonCopied(false), 2000)
      } finally {
        document.body.removeChild(ta)
      }
    }
  }, [openBotBetsList])

  const fetchNeurobets = useCallback(async (offset: number, limit: number, mode: "replace" | "append") => {
    const requestId = ++neurobetsRequestId.current
    if (mode === "append") setLoadingMoreLive(true)
    try {
      const params = new URLSearchParams({
        sort: sortMode,
        limit: limit.toString(),
        offset: offset.toString(),
        verdict: verdictFilter
      })
      if (selectedSport !== "all") params.append("sport", selectedSport)
      if (searchQuery) params.append("search", searchQuery)

      const res = await fetchApi(`${API_BASE}/api/neurobets/top?${params.toString()}`)
      // Отбрасываем устаревший ответ, если после этого запроса уже успел уйти более новый
      if (requestId !== neurobetsRequestId.current) return
      if (res.ok) {
        const data = await res.json()
        if (requestId !== neurobetsRequestId.current) return
        if (data.bets) {
          setLiveTotal(typeof data.total === "number" ? data.total : data.bets.length)
          const mapped: NeuroBet[] = data.bets.map((b: any, idx: number) => {
            const coeff = floatVal(b.coefficient)
            const initCoeff = floatVal(b.initial_coefficient) || coeff
            const diff = (initCoeff - coeff).toFixed(2)
            const diffText = initCoeff > coeff ? `📉 Падение кэфа (-${diff})` : initCoeff < coeff ? `📈 Рост кэфа (+${Math.abs(Number(diff))})` : "⚖️ Стабильный кэф"

            const openBet = openBotBetsRef.current.get(liveBetKey(b.event_id, b.factor_id, b.parameter, b.market_prefix))

            return {
              id: `live-${b.event_id}-${b.factor_id}-${b.parameter}-${b.market_prefix}`,
              rankBest: idx + 1,
              rankSafe: idx + 1,
              // sport_path is a " / "-joined breadcrumb (see backend/parser_service.py's
              // get_sport_path), e.g. "Футбол / Англия / Премьер-лига" — take just the
              // top-level sport. Was splitting on "." before, which never matched this
              // separator and always returned the whole path.
              sport: b.sport_path ? b.sport_path.split("/")[0].trim() : "Спорт",
              matchName: b.match_name || `${b.team_1} — ${b.team_2}`,
              team1: b.team_1 || "Команда 1",
              team2: b.team_2 || "Команда 2",
              score: b.score || "0:0",
              timer: b.timer || "LIVE",
              marketName: b.market_prefix || "Основной маркет",
              outcomeLabel: b.label || `Factor ${b.factor_id}`,
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
              pytorchScore: b.pytorch_score,
              stake: openBet ? openBet.stake : null,
              potentialPayout: openBet ? openBet.stake * openBet.coefficient : null,
              predictedWin: b.predicted_win ?? null,
              willWin: b.will_win ?? null,
            }
          })
          setLiveBets((prev) => (mode === "append" ? [...prev, ...mapped] : mapped))
          liveOffsetRef.current = offset + mapped.length
        }
      }
    } catch (err) {
      if (requestId === neurobetsRequestId.current && mode === "replace") setLiveBets([])
    } finally {
      if (requestId === neurobetsRequestId.current) {
        setLoading(false)
        setLoadingMoreLive(false)
      }
    }
  }, [API_BASE, sortMode, selectedSport, verdictFilter, searchQuery])

  const loadMoreLive = useCallback(() => {
    if (loadingMoreLive || loading) return
    if (liveOffsetRef.current >= liveTotal) return
    fetchNeurobets(liveOffsetRef.current, PAGE_SIZE, "append")
  }, [fetchNeurobets, loadingMoreLive, loading, liveTotal])

  useEffect(() => {
    fetchOpenBotBets()
    fetchStats()
    fetchHeadlineAccuracy()
    fetchBankroll()
    if (activeTab === "live") {
      fetchNeurobets(0, PAGE_SIZE, "replace")
      fetchHistoryTotal()
    } else {
      fetchHistory(0, PAGE_SIZE, "replace")
      fetchHistoryTotal()
      fetchLiveTotal()
    }

    if (!autoRefresh) return
    // Refresh the active tab's full list/content, but also keep the OTHER tab's badge
    // count (the number in parentheses) ticking over in the background with a cheap
    // count-only request, so both badges stay live regardless of which tab is open —
    // not just the one the user happens to be looking at.
    //
    // History list is intentionally NOT re-fetched on the timer: replace-mode refresh
    // clears scroll position and flashes the full-page loader — bad for browsing.
    // Only summary/badge counts tick over; a fresh list loads on tab/filter change.
    const interval = setInterval(() => {
      fetchStats()
      fetchHeadlineAccuracy()
      fetchBankroll()
      fetchOpenBotBets()
      if (activeTab === "live") {
        // Re-fetch from the top on each refresh, but keep however many rows the user
        // has already scrolled to load, so auto-refresh doesn't collapse the list back to one page.
        fetchNeurobets(0, Math.max(liveOffsetRef.current, PAGE_SIZE), "replace")
        fetchHistoryTotal()
      } else {
        fetchHistoryTotal()
        fetchLiveTotal()
      }
    }, 10000)
    return () => clearInterval(interval)
  }, [activeTab, fetchNeurobets, fetchHistory, fetchStats, fetchHeadlineAccuracy, fetchBankroll, fetchOpenBotBets, fetchLiveTotal, fetchHistoryTotal, autoRefresh])

  const sportsList = SPORT_FILTER_OPTIONS

  function floatVal(val: any): number {
    const p = parseFloat(val)
    return isNaN(p) ? 1.0 : p
  }

  const modelGuessRatePct =
    headlineAccuracy?.guess_rate_pct ?? stats?.guess_rate_pct ?? null
  const modelMissRatePct =
    headlineAccuracy?.miss_rate_pct ??
    stats?.miss_rate_pct ??
    (modelGuessRatePct != null ? Math.round((100 - modelGuessRatePct) * 10) / 10 : null)
  const modelAccuracyKnown = modelGuessRatePct != null

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased flex flex-col">
      {/* Shared Header Navigation */}
      <HeaderNav stats={stats} />

      {/* Main Body */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-4 md:p-6 space-y-6">
        {/* Banner: AI Engine Overview */}
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-r from-neutral-900 via-neutral-900/90 to-neutral-950 border border-neutral-800 p-6 md:p-8 shadow-2xl">
          {/* Ambient blobs drifting slowly in place — pure texture, no message to read */}
          <motion.div
            className="absolute -right-10 -bottom-10 w-72 h-72 bg-[#fdcb6e]/10 rounded-full blur-3xl pointer-events-none"
            animate={{ x: [0, 18, 0], y: [0, -14, 0] }}
            transition={{ duration: 14, repeat: Infinity, ease: "easeInOut" }}
          />
          <motion.div
            className="absolute right-32 top-0 w-48 h-48 bg-[#00b894]/10 rounded-full blur-3xl pointer-events-none"
            animate={{ x: [0, -14, 0], y: [0, 16, 0] }}
            transition={{ duration: 11, repeat: Infinity, ease: "easeInOut" }}
          />
          {/* Faint dot-grid texture, so the left half isn't just flat gradient */}
          <div
            className="absolute inset-0 opacity-[0.07] pointer-events-none"
            style={{
              backgroundImage: "radial-gradient(circle, #fff 1px, transparent 1px)",
              backgroundSize: "22px 22px"
            }}
          />
          {/* Slow diagonal sheen sweeping across the banner — a subtle "scanning" cue */}
          <motion.div
            className="absolute inset-y-0 w-1/3 pointer-events-none mix-blend-overlay"
            style={{ background: "linear-gradient(115deg, transparent, rgba(255,255,255,0.08), transparent)" }}
            animate={{ x: ["-40%", "160%"] }}
            transition={{ duration: 6, repeat: Infinity, ease: "linear", repeatDelay: 2 }}
          />

          <div className="relative z-10 flex flex-col lg:flex-row items-start lg:items-center justify-between gap-6">
            <motion.div
              className="space-y-3 max-w-2xl"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            >
              <div className="relative inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[#fdcb6e]/10 border border-[#fdcb6e]/30 text-[#ffeaa7] text-xs font-semibold">
                <span className="relative flex items-center justify-center">
                  <motion.span
                    className="absolute inline-flex h-3 w-3 rounded-full bg-[#fdcb6e]/50"
                    animate={{ scale: [1, 2.2, 1], opacity: [0, 0.5, 0] }}
                    transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                  />
                  <BrainCircuit className="relative w-4 h-4 text-[#fdcb6e]" />
                </span>
                <span>LightGBM & PyTorch Online Model Ensemble</span>
              </div>
              <h2 className="text-2xl md:text-3xl font-black tracking-tight text-white">
                🧠 Нейроставки — Умный Рейтинг Топ Ставок
              </h2>
              <p className="text-sm text-neutral-300 leading-relaxed">
                Алгоритм непрерывно анализирует прямую трансляцию коэффициентов и движений линий Fonbet LIVE и сам определяет, выиграет исход или проиграет —
                своим собственным обученным вердиктом, а не по внешнему порогу вероятности. По умолчанию показаны только исходы с вердиктом «выиграет» —
                переключить на проигрывающие или все можно фильтром ниже.
              </p>
            </motion.div>

            {/* AI Architecture Panel — two compact "engine" chips + a live accuracy ring */}
            <motion.div
              className="flex items-stretch gap-3 w-full lg:w-auto"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1, ease: "easeOut" }}
            >
              <div className="flex flex-col gap-3 min-w-[170px] flex-1 lg:flex-initial lg:w-[230px]">
                <div className="flex items-center gap-3 bg-neutral-950/80 border border-neutral-800 rounded-xl px-4 py-[18px] backdrop-blur transition-[border-color] duration-300 ease-out hover:border-[#55efc4]/40">
                  <div className="relative w-9 h-9 rounded-lg bg-[#55efc4]/10 flex items-center justify-center shrink-0">
                    <motion.span
                      className="pointer-events-none absolute inset-0 rounded-lg bg-[#55efc4]/25"
                      animate={{ scale: [1, 1.35, 1], opacity: [0, 0.5, 0] }}
                      transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                    />
                    <Activity className="relative w-[18px] h-[18px] text-[#55efc4]" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-[10px] text-neutral-500 font-mono uppercase leading-none">PyTorch Temporal</div>
                    <div className="text-sm font-bold text-[#55efc4] font-mono mt-1.5 leading-none">Online GRU</div>
                    <div className="text-[10px] text-neutral-500 mt-1 leading-none">окно 10m</div>
                  </div>
                </div>
                <div className="flex items-center gap-3 bg-neutral-950/80 border border-neutral-800 rounded-xl px-4 py-[18px] backdrop-blur transition-[border-color] duration-300 ease-out hover:border-[#fdcb6e]/40">
                  <div className="relative w-9 h-9 rounded-lg bg-[#fdcb6e]/10 flex items-center justify-center shrink-0">
                    <motion.span
                      className="pointer-events-none absolute inset-0 rounded-lg bg-[#fdcb6e]/25"
                      animate={{ scale: [1, 1.35, 1], opacity: [0, 0.5, 0] }}
                      transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut", delay: 1.2 }}
                    />
                    <Zap className="relative w-[18px] h-[18px] text-[#fdcb6e]" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-[10px] text-neutral-500 font-mono uppercase leading-none">LightGBM GBDT</div>
                    <div className="text-sm font-bold text-[#fdcb6e] font-mono mt-1.5 leading-none">Leaf Speed</div>
                    <div className="text-[10px] text-neutral-500 mt-1 leading-none">&lt;5ms инференс</div>
                  </div>
                </div>
              </div>

              <div className="relative bg-neutral-950/80 border border-neutral-800/80 rounded-xl px-6 py-4 flex flex-col items-center justify-center gap-2 backdrop-blur bg-gradient-to-b from-neutral-950 to-[#55efc4]/5 shrink-0">
                <div className="text-[10px] text-neutral-400 font-mono uppercase">Точность модели</div>
                <AccuracyRing
                  size={96}
                  pct={modelGuessRatePct ?? 0}
                  known={modelAccuracyKnown}
                />
                <div className="text-[10px] font-semibold">
                  {!modelAccuracyKnown && headlineAccuracy == null && stats == null ? (
                    <span className="text-neutral-500">загрузка...</span>
                  ) : !modelAccuracyKnown ? (
                    <span className="text-neutral-500">нет данных</span>
                  ) : (
                    <span className="text-[#ff7675]">
                      промах {modelMissRatePct!.toFixed(1)}%
                    </span>
                  )}
                </div>
              </div>
            </motion.div>
          </div>
        </div>

        {/* Bankroll Panel: live simulated account only — the training bankroll is an
            internal training signal, not something a viewer here needs to see; it's
            still visible on /admin. Always rendered (with an empty state) rather than
            disappearing entirely, so a slow/unreachable backend doesn't look like the
            panel was removed — same treatment as "Ставки нейросети" below. */}
        {(() => {
          const acc = bankroll?.accounts?.live
          // ROI must be based on total equity (spendable + locked-in-open-bets), not just
          // spendable balance — money currently staked on an open position hasn't been
          // won or lost yet, so counting it as gone (the old balance-only calculation)
          // showed a "-71.8%" loss for money that was simply still in play. Locked money
          // only actually leaves the total once a bet settles and balance/locked update.
          const totalEquity = acc ? Number(acc.balance) + Number(acc.locked || 0) : 0
          const roiPct = acc && acc.start_balance > 0 ? ((totalEquity - acc.start_balance) / acc.start_balance) * 100 : 0
          // Hit-rate of the same window as «История ставок нейросети» (last N settled
          // rows). Void/cancelled are skipped — they are not a model miss.
          const recentJudged = settledBotBetsList.filter((b) => b.status === "won" || b.status === "lost")
          const recentWins = recentJudged.filter((b) => b.status === "won").length
          const liveBetHitKnown = recentJudged.length > 0
          const liveBetHitPct = liveBetHitKnown ? (recentWins / recentJudged.length) * 100 : 0
          return (
            <div className="relative overflow-hidden rounded-2xl bg-neutral-900/80 border border-[#fdcb6e]/40 p-5 space-y-3 shadow-lg">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-bold text-white">
                  💰 Банк
                </div>
                <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-neutral-950 text-neutral-400 border border-neutral-800">
                  реальные ставки бота
                </span>
              </div>

              {!acc ? (
                <div className="flex items-center gap-3 bg-neutral-950/60 border border-neutral-800/80 rounded-xl px-4 py-3.5">
                  <Clock className="w-4 h-4 text-neutral-500 shrink-0" />
                  <p className="text-xs text-neutral-400">
                    Нет данных о банке — бэкенд недоступен или live-аккаунт ещё не инициализирован.
                  </p>
                </div>
              ) : (
                <>
                  <div className="flex items-end gap-3 min-w-0">
                    <div className="text-3xl font-black font-mono text-white">
                      {Number(acc.balance).toFixed(1)} ₽
                    </div>
                    <div className={`text-sm font-bold font-mono mb-1 ${roiPct >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"}`}>
                      {roiPct >= 0 ? "+" : ""}{roiPct.toFixed(1)}%
                    </div>
                  </div>

                  <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 text-center">
                    <div className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800">
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">Пик</div>
                      <div className="text-xs font-bold text-white font-mono">{Number(acc.peak_balance).toFixed(0)}</div>
                    </div>
                    <div className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800">
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">W / L</div>
                      <div className="text-xs font-bold font-mono">
                        <span className="text-[#55efc4]">{acc.wins}</span>
                        <span className="text-neutral-600"> / </span>
                        <span className="text-[#ff7675]">{acc.losses}</span>
                      </div>
                    </div>
                    <div className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800">
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">Открыто ставок</div>
                      <div className="text-xs font-bold text-[#74b9ff] font-mono">{openBetsCount}</div>
                    </div>
                    <div className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800">
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">В игре</div>
                      <div className="text-xs font-bold text-[#ffeaa7] font-mono">{Number(acc.locked || 0).toFixed(0)}</div>
                    </div>
                    <div className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800">
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">Банкротств</div>
                      <div className={`text-xs font-bold font-mono ${acc.ruin_count > 0 ? "text-[#ff7675]" : "text-neutral-400"}`}>
                        {acc.ruin_count}
                      </div>
                    </div>
                    <div
                      className="bg-neutral-950/80 rounded-lg p-2 border border-neutral-800"
                      title={
                        liveBetHitKnown
                          ? `${recentWins} из ${recentJudged.length} выигранных среди последних ${settledBotBetsList.length} рассчитанных ставок`
                          : "Нет выигранных/проигранных среди последних рассчитанных ставок"
                      }
                    >
                      <div className="text-[9px] text-neutral-500 font-mono uppercase">Точность · {SETTLED_BOT_BETS_LIMIT}</div>
                      <div className={`text-xs font-bold font-mono ${liveBetHitKnown ? "text-[#55efc4]" : "text-neutral-400"}`}>
                        {liveBetHitKnown ? `${liveBetHitPct.toFixed(1)}%` : "—"}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          )
        })()}

        {/* Ставки нейросети — the bot's actual open real-money positions, kept visually
            separate from the "Активные LIVE Прогнозы" tab below (which is just every
            live outcome the AI has scored, most of which have no money on them). */}
        <div className="bg-neutral-900/80 border border-[#00b894]/40 rounded-2xl p-4 md:p-5 space-y-3 backdrop-blur-md shadow-lg shadow-[#00b894]/5">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <div className="flex items-center gap-3">
              <div className="relative w-9 h-9 rounded-xl bg-[#00b894]/15 border border-[#00b894]/30 flex items-center justify-center shrink-0">
                <motion.span
                  className="absolute inset-0 rounded-xl bg-[#00b894]/25"
                  animate={{ scale: [1, 1.3, 1], opacity: [0, 0.5, 0] }}
                  transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                />
                <Zap className="relative w-4 h-4 text-[#55efc4]" />
              </div>
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                Ставки нейросети
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30">
                  открыто: {openBotBetsList.length}
                </span>
              </h3>
            </div>
            <button
              type="button"
              onClick={copyOpenBotBetsJson}
              disabled={openBotBetsList.length === 0}
              className={`inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1.5 rounded-lg border transition shrink-0 ${
                openBotBetsList.length === 0
                  ? "bg-neutral-950/40 border-neutral-800 text-neutral-600 cursor-not-allowed"
                  : openBetsJsonCopied
                  ? "bg-[#00b894]/15 border-[#00b894]/40 text-[#55efc4]"
                  : "bg-neutral-950/80 border-neutral-700 text-neutral-300 hover:border-[#00b894]/50 hover:text-[#55efc4]"
              }`}
              title="Скопировать открытые ставки бота как JSON-массив"
            >
              {openBetsJsonCopied ? (
                <>
                  <Check className="w-3.5 h-3.5" />
                  Скопировано
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  Скопировать JSON
                </>
              )}
            </button>
          </div>

          {!botBetsLoaded ? (
            <div className="flex items-center gap-3 bg-neutral-950/60 border border-neutral-800/80 rounded-xl px-4 py-3.5">
              <Loader2 className="w-4 h-4 text-neutral-500 shrink-0 animate-spin" />
              <p className="text-xs text-neutral-400">Загрузка открытых ставок…</p>
            </div>
          ) : openBotBetsList.length === 0 ? (
            <div className="flex items-center gap-3 bg-neutral-950/60 border border-neutral-800/80 rounded-xl px-4 py-3.5">
              <Clock className="w-4 h-4 text-neutral-500 shrink-0" />
              <p className="text-xs text-neutral-400">
                Сейчас нет открытых ставок — бот ждёт исход с положительным EV и свободным банком.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {openBotBetsList.map((b) => {
                const currentCoeff = b.current_coefficient != null ? Number(b.current_coefficient) : null
                const betCoeff = Number(b.coefficient)
                const coeffRose = currentCoeff !== null && currentCoeff > betCoeff
                const coeffDropped = currentCoeff !== null && currentCoeff < betCoeff
                const skipNow = b.current_predicted_win != null && Number(b.current_predicted_win) === 0

                return (
                  <div
                    key={b.id}
                    className="flex items-center justify-between gap-3 bg-neutral-950/80 border border-neutral-800 rounded-xl p-3"
                  >
                    <div className="min-w-0 space-y-0.5">
                      {b.sport_path && (
                        <span className="inline-block text-[9px] font-mono uppercase px-1.5 py-0.5 rounded bg-neutral-900 text-neutral-400 border border-neutral-800 mb-0.5">
                          {b.sport_path}
                        </span>
                      )}
                      <div className="flex items-center gap-1.5 min-w-0">
                        <div className="text-xs font-bold text-white truncate">{b.match_name}</div>
                        {skipNow && (
                          <span
                            className="shrink-0 text-[9px] font-mono uppercase px-1.5 py-0.5 rounded bg-[#d63031]/15 text-[#ff7675] border border-[#d63031]/40"
                            title="Позиция уже открыта. Живой пересчёт: EV ниже порога, новую ставку бот сейчас бы не открыл."
                          >
                            (сейчас не ставить)
                          </span>
                        )}
                      </div>
                      <div className="text-[11px] text-neutral-400 truncate">
                        {b.market_prefix} — {b.label}
                      </div>
                      <div className="text-[10px] text-neutral-500 font-mono flex items-center gap-1.5 flex-wrap">
                        <span>Коэф. ставки {betCoeff.toFixed(2)}</span>
                        {currentCoeff !== null && (
                          <span className={coeffRose ? "text-[#ff7675]" : coeffDropped ? "text-[#55efc4]" : "text-neutral-500"}>
                            → сейчас {currentCoeff.toFixed(2)}
                          </span>
                        )}
                        <span>· Вероятность {Number(b.win_probability).toFixed(1)}%</span>
                      </div>
                      <div className="text-[10px] font-mono flex items-center gap-2">
                        <span className="text-[#fdcb6e] font-bold">{b.current_score || "0:0"}</span>
                        {b.match_is_live && b.current_timer && (
                          <span className="text-neutral-500">⏱ {b.current_timer}</span>
                        )}
                        {!b.match_is_live && (
                          <span className="text-neutral-600">матч завершён</span>
                        )}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-sm font-black font-mono text-white">{Number(b.stake).toFixed(1)} ₽</div>
                      <div className="text-[10px] font-mono text-[#55efc4]">
                        → {(Number(b.stake) * Number(b.coefficient)).toFixed(1)} ₽
                      </div>
                      {formatPlacedAt(b.placed_at) && (
                        <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                          🕒 {formatPlacedAt(b.placed_at)}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* История ставок нейросети — the bot's own settled real-money positions
            (won/lost/void/cancelled), separate from the "История Прогнозов" tab below
            (which is every scored outcome, not just the ones the bot actually staked on).
            Collapsed by default — it's not something you need open at all times, and it
            was crowding out the more time-sensitive sections above it. */}
        <div className="bg-neutral-900/80 border border-neutral-800 rounded-2xl backdrop-blur-md overflow-hidden">
          <button
            onClick={() => setHistoryExpanded((v) => !v)}
            className="w-full flex items-center justify-between gap-2 p-4 md:p-5 text-left hover:bg-neutral-800/30 transition"
          >
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-neutral-800/80 border border-neutral-700 flex items-center justify-center shrink-0">
                <Database className="w-4 h-4 text-neutral-400" />
              </div>
              <h3 className="text-sm font-bold text-white flex items-center gap-2">
                История ставок нейросети
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400 border border-neutral-700">
                  последние {settledBotBetsList.length}
                </span>
              </h3>
            </div>
            <ChevronDown className={`w-4 h-4 text-neutral-400 shrink-0 transition-transform ${historyExpanded ? "rotate-180" : ""}`} />
          </button>

          {!historyExpanded ? null : !botBetsLoaded ? (
            <div className="flex items-center gap-3 px-4 md:px-5 pb-4 md:pb-5">
              <Loader2 className="w-4 h-4 text-neutral-500 shrink-0 animate-spin" />
              <p className="text-xs text-neutral-400">Загрузка истории ставок…</p>
            </div>
          ) : settledBotBetsList.length === 0 ? (
            <p className="text-xs text-neutral-400 px-4 md:px-5 pb-4 md:pb-5">
              Пока нет рассчитанных ставок — как только открытая ставка бота разрешится, она появится здесь.
            </p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 px-4 md:px-5 pb-4 md:pb-5">
              {settledBotBetsList.map((b) => {
                const statusCfg: Record<string, { label: string; cls: string }> = {
                  won: { label: "🟢 ВЫИГРАЛА", cls: "border-[#00b894]/50 bg-[#00b894]/10" },
                  lost: { label: "🔴 ПРОИГРАЛА", cls: "border-[#d63031]/50 bg-[#d63031]/10" },
                  void: { label: "⚪ АННУЛИРОВАНА", cls: "border-neutral-700 bg-neutral-800/30" },
                  cancelled: { label: "🟠 ОТМЕНЕНА", cls: "border-[#fdcb6e]/40 bg-[#fdcb6e]/10" },
                }
                const cfg = statusCfg[b.status] || { label: b.status, cls: "border-neutral-700 bg-neutral-800/30" }
                const profit = b.payout != null ? Number(b.payout) - Number(b.stake) : null

                return (
                  <div
                    key={b.id}
                    className={`flex items-center justify-between gap-3 rounded-xl p-3 border ${cfg.cls}`}
                  >
                    <div className="min-w-0 space-y-0.5">
                      {b.sport_path && (
                        <span className="inline-block text-[9px] font-mono uppercase px-1.5 py-0.5 rounded bg-neutral-900 text-neutral-400 border border-neutral-800 mb-0.5">
                          {b.sport_path}
                        </span>
                      )}
                      <div className="text-xs font-bold text-white truncate">{b.match_name}</div>
                      <div className="text-[11px] text-neutral-400 truncate">
                        {b.market_prefix} — {b.label}
                      </div>
                      <div className="text-[10px] text-neutral-500 font-mono">
                        Коэф. {Number(b.coefficient).toFixed(2)} · Вероятность {Number(b.win_probability).toFixed(1)}%
                      </div>
                      {formatPlacedAt(b.settled_at) && (
                        <div className="text-[10px] text-neutral-600 font-mono">
                          🕒 рассчитана в {formatPlacedAt(b.settled_at)}
                        </div>
                      )}
                    </div>
                    <div className="text-right shrink-0 space-y-0.5">
                      <div className="text-[10px] font-mono font-bold">{cfg.label}</div>
                      <div className="text-sm font-black font-mono text-white">{Number(b.stake).toFixed(1)} ₽</div>
                      {profit !== null && (
                        <div className={`text-[10px] font-mono ${profit > 0 ? "text-[#55efc4]" : profit < 0 ? "text-[#ff7675]" : "text-neutral-500"}`}>
                          {profit > 0 ? "+" : ""}{profit.toFixed(1)} ₽
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Main Sub-Tab Switcher: Active LIVE Bets vs History & Results */}
        <div className="flex items-center gap-2 bg-neutral-900/90 p-1.5 rounded-2xl border border-neutral-800 backdrop-blur-md shadow-lg">
          <button
            onClick={() => setActiveTab("live")}
            className={`relative flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl text-xs md:text-sm font-bold transition-colors ${
              activeTab === "live" ? "text-neutral-950" : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {activeTab === "live" && (
              <motion.div
                layoutId="mainTabIndicator"
                layoutDependency={activeTab}
                className="absolute inset-0 rounded-xl bg-gradient-to-r from-[#fdcb6e] to-[#ffeaa7] shadow-lg shadow-[#fdcb6e]/20"
                transition={{ type: "spring", stiffness: 400, damping: 32 }}
              />
            )}
            <Zap className="relative z-10 w-4 h-4" />
            <span className="relative z-10">🔥 Активные LIVE Прогнозы ({liveTotal.toLocaleString()})</span>
          </button>

          <button
            onClick={() => setActiveTab("history")}
            className={`relative flex-1 flex items-center justify-center gap-2 py-3 px-4 rounded-xl text-xs md:text-sm font-bold transition-colors ${
              activeTab === "history" ? "text-neutral-950" : "text-neutral-400 hover:text-neutral-200"
            }`}
          >
            {activeTab === "history" && (
              <motion.div
                layoutId="mainTabIndicator"
                layoutDependency={activeTab}
                className="absolute inset-0 rounded-xl bg-gradient-to-r from-[#00b894] to-[#55efc4] shadow-lg shadow-[#00b894]/20"
                transition={{ type: "spring", stiffness: 400, damping: 32 }}
              />
            )}
            <Trophy className="relative z-10 w-4 h-4" />
            <span className="relative z-10">📜 История Прогнозов ({historySummary?.total_count || stats?.finished_odds_history_count || 0})</span>
          </button>
        </div>

        {/* Filter Controls & Sort Mode Switcher */}
        <div className="bg-neutral-900/80 border border-neutral-800 rounded-2xl p-4 md:p-5 space-y-4 backdrop-blur-md">
          <div className="flex flex-col lg:flex-row lg:items-end justify-between gap-4">
            {/* Sort Mode Buttons (Only shown for LIVE Tab) */}
            {activeTab === "live" ? (
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-neutral-400 flex items-center gap-1.5">
                  <BarChart3 className="w-3.5 h-3.5 text-[#fdcb6e]" />
                  Режим сортировки ставок:
                </label>
                <div className="inline-flex items-center gap-1 bg-neutral-950 p-1 rounded-xl border border-neutral-800">
                  <button
                    onClick={() => setSortMode("best")}
                    className={`relative flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-colors ${
                      sortMode === "best" ? "text-neutral-950" : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    {sortMode === "best" && (
                      <motion.div
                        layoutId="sortModeIndicator"
                        layoutDependency={sortMode}
                        className="absolute inset-0 rounded-lg bg-[#fdcb6e] shadow-sm shadow-[#fdcb6e]/20"
                        transition={{ type: "spring", stiffness: 400, damping: 32 }}
                      />
                    )}
                    <Trophy className="relative z-10 w-3.5 h-3.5" />
                    <span className="relative z-10">⭐ Самая лучшая (Max ROI / EV)</span>
                  </button>

                  <button
                    onClick={() => setSortMode("safe")}
                    className={`relative flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-colors ${
                      sortMode === "safe" ? "text-neutral-950" : "text-neutral-400 hover:text-white"
                    }`}
                  >
                    {sortMode === "safe" && (
                      <motion.div
                        layoutId="sortModeIndicator"
                        layoutDependency={sortMode}
                        className="absolute inset-0 rounded-lg bg-[#00b894] shadow-sm shadow-[#00b894]/20"
                        transition={{ type: "spring", stiffness: 400, damping: 32 }}
                      />
                    )}
                    <ShieldCheck className="relative z-10 w-3.5 h-3.5" />
                    <span className="relative z-10">🛡️ Самая безопасная (Max Win %)</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-1">
                <h3 className="text-sm font-bold text-white flex items-center gap-2">
                  📜 Результаты Прогнозов Нейросети
                  <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/30">
                    Архив завершенных матчей
                  </span>
                </h3>
                <p className="text-xs text-neutral-400">
                  Зеленый цвет — ставка сыграла (нейросеть угадала), Красный — ставка не прошла.
                </p>
              </div>
            )}

            {/* Verdict Filter — moved here (was where the now-removed odds-range badge sat) */}
            {activeTab === "live" && (
              <div className="self-start lg:self-auto inline-flex flex-wrap items-center gap-1 bg-neutral-950 p-1 rounded-xl border border-neutral-800 shrink-0">
                <button
                  onClick={() => setVerdictFilter("win")}
                  className={`relative flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors ${
                    verdictFilter === "win" ? "text-neutral-950" : "text-neutral-400 hover:text-white"
                  }`}
                >
                  {verdictFilter === "win" && (
                    <motion.div
                      layoutId="verdictFilterIndicator"
                      layoutDependency={verdictFilter}
                      className="absolute inset-0 rounded-lg bg-[#00b894] shadow-sm shadow-[#00b894]/20"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <span className="relative z-10">🟢 Выигрывающие</span>
                </button>
                <button
                  onClick={() => setVerdictFilter("loss")}
                  className={`relative flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors ${
                    verdictFilter === "loss" ? "text-white" : "text-neutral-400 hover:text-white"
                  }`}
                >
                  {verdictFilter === "loss" && (
                    <motion.div
                      layoutId="verdictFilterIndicator"
                      layoutDependency={verdictFilter}
                      className="absolute inset-0 rounded-lg bg-[#d63031] shadow-sm shadow-[#d63031]/20"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <span className="relative z-10">🔴 Проигрывающие</span>
                </button>
                <button
                  onClick={() => setVerdictFilter("all")}
                  className={`relative flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-bold transition-colors ${
                    verdictFilter === "all" ? "text-white" : "text-neutral-400 hover:text-white"
                  }`}
                >
                  {verdictFilter === "all" && (
                    <motion.div
                      layoutId="verdictFilterIndicator"
                      layoutDependency={verdictFilter}
                      className="absolute inset-0 rounded-lg bg-neutral-700 shadow-sm"
                      transition={{ type: "spring", stiffness: 400, damping: 32 }}
                    />
                  )}
                  <span className="relative z-10">⚪ Все</span>
                </button>
              </div>
            )}
          </div>

          {/* Search (LIVE tab only) — now spans the full row on its own */}
          {activeTab === "live" && (
            <div className="relative pt-3 border-t border-neutral-800/80">
              <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-neutral-500" />
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Команда, матч или тип ставки — например «Фора 1» или «команда — команда»"
                className="w-full bg-neutral-950 border border-neutral-800 rounded-full pl-10 pr-4 py-2.5 text-sm text-neutral-200 placeholder-neutral-500 focus:outline-none focus:border-[#fdcb6e] transition"
              />
            </div>
          )}

          {/* Sport Categories Tabs */}
          <div className="flex flex-wrap items-center gap-2 pt-2 pb-1 border-t border-neutral-800/80">
            {sportsList.map((sport) => (
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

        {/* Tab Content Section */}
        {activeTab === "history" ? (
          /* HISTORY TAB VIEW */
          <div className="space-y-6">
            {/* History Summary Cards — double as outcome filter buttons */}
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
              <button
                type="button"
                onClick={() => setHistoryOutcomeFilter("all")}
                className={`rounded-2xl p-4 text-center backdrop-blur shadow-lg border transition ${
                  historyOutcomeFilter === "all"
                    ? "bg-neutral-800 border-white/60 ring-1 ring-white/30"
                    : "bg-neutral-900/90 border-neutral-800 hover:border-neutral-600"
                }`}
              >
                <div className="text-[10px] text-neutral-400 font-mono uppercase">Всего прогнозов</div>
                <div className="text-xl font-black text-white font-mono mt-1">
                  {historySummary?.total_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-neutral-500 mt-0.5">В архивном датасете</div>
              </button>

              <button
                type="button"
                onClick={() => setHistoryOutcomeFilter("correct")}
                className={`rounded-2xl p-4 text-center backdrop-blur shadow-lg border transition ${
                  historyOutcomeFilter === "correct"
                    ? "bg-[#00b894]/20 border-[#00b894] ring-1 ring-[#00b894]/50"
                    : "bg-neutral-900/90 border-[#00b894]/40 hover:border-[#00b894]/70"
                }`}
              >
                <div className="text-[10px] text-[#55efc4] font-mono uppercase font-bold">🟢 Угадано</div>
                <div className="text-xl font-black text-[#55efc4] font-mono mt-1">
                  {historySummary?.correct_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-[#55efc4]/80 mt-0.5">Вердикт сети совпал с исходом</div>
              </button>

              <button
                type="button"
                onClick={() => setHistoryOutcomeFilter("incorrect")}
                className={`rounded-2xl p-4 text-center backdrop-blur shadow-lg border transition ${
                  historyOutcomeFilter === "incorrect"
                    ? "bg-[#d63031]/20 border-[#d63031] ring-1 ring-[#d63031]/50"
                    : "bg-neutral-900/90 border-[#d63031]/40 hover:border-[#d63031]/70"
                }`}
              >
                <div className="text-[10px] text-[#ff7675] font-mono uppercase font-bold">🔴 Не угадано</div>
                <div className="text-xl font-black text-[#ff7675] font-mono mt-1">
                  {historySummary?.incorrect_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-[#ff7675]/80 mt-0.5">Вердикт сети разошелся с исходом</div>
              </button>

              <button
                type="button"
                onClick={() => setHistoryOutcomeFilter("push")}
                className={`rounded-2xl p-4 text-center backdrop-blur shadow-lg border transition ${
                  historyOutcomeFilter === "push"
                    ? "bg-[#0984e3]/20 border-[#0984e3] ring-1 ring-[#0984e3]/50"
                    : "bg-neutral-900/90 border-[#0984e3]/40 hover:border-[#0984e3]/70"
                }`}
              >
                <div className="text-[10px] text-[#74b9ff] font-mono uppercase font-bold">🔵 Возврат</div>
                <div className="text-xl font-black text-[#74b9ff] font-mono mt-1">
                  {historySummary?.push_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-[#74b9ff]/80 mt-0.5">Линия легла точно в ноль</div>
              </button>

              <button
                type="button"
                onClick={() => setHistoryOutcomeFilter("pending")}
                className={`rounded-2xl p-4 text-center backdrop-blur shadow-lg border transition ${
                  historyOutcomeFilter === "pending"
                    ? "bg-neutral-700 border-neutral-400 ring-1 ring-neutral-400/50"
                    : "bg-neutral-900/90 border-neutral-700 hover:border-neutral-500"
                }`}
              >
                <div className="text-[10px] text-neutral-300 font-mono uppercase font-bold">⚪ Не рассчитано</div>
                <div className="text-xl font-black text-neutral-300 font-mono mt-1">
                  {historySummary?.pending_count?.toLocaleString() || 0}
                </div>
                <div className="text-[10px] text-neutral-400 mt-0.5">Исход или вердикт сети неизвестны</div>
              </button>

              <div className="bg-neutral-900/90 border border-[#fdcb6e]/40 rounded-2xl p-4 text-center backdrop-blur shadow-lg">
                <div className="text-[10px] text-[#ffeaa7] font-mono uppercase font-bold">🎯 Процент угадывания</div>
                <div className="text-xl font-black text-[#ffeaa7] font-mono mt-1">
                  {historySummary?.guess_rate_pct !== undefined ? `${historySummary.guess_rate_pct.toFixed(1)}%` : "0.0%"}
                </div>
                <div className="text-[10px] text-[#ffeaa7]/80 mt-0.5">От прогнозов с известным исходом</div>
              </div>
            </div>

            <p className="text-xs text-neutral-500 -mt-3">
              Угадано/не угадано — совпал ли прогноз исхода с фактом. Вне коридора 1.1–2.0 деньги не кладём, но это не автопроигрыш: либо скип, либо «выиграет / скорее всего не победит». Падение кэфа повышает вероятность исхода, рост — понижает. «Не ставить» из‑за нулевого EV больше не считается прогнозом «проиграет».
            </p>

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
                  Как только активные матчи завершатся, они автоматически попадут в историю прогнозов с отметкой угадано (зеленый) или не угадано (красный).
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {historyItems.map((item: any, idx: number) => {
                  const outcomeCall = item.will_win ?? item.predicted_win
                  const judged = item.is_win !== null && item.is_win !== undefined
                    && item.predicted_win !== null && item.predicted_win !== undefined
                  const status: "correct" | "incorrect" | "push" | "pending" =
                    judged
                      ? (outcomeCall === item.is_win ? "correct" : "incorrect")
                      : item.is_win === null && item.is_push
                      ? "push"
                      : "pending"
                  const coeff = item.initial_coefficient || item.final_coefficient || 1.5

                  const cardCls =
                    status === "correct"
                      ? "bg-[#00b894]/10 border-[#00b894]/50 shadow-[#00b894]/5"
                      : status === "incorrect"
                      ? "bg-[#d63031]/10 border-[#d63031]/50 shadow-[#d63031]/5"
                      : status === "push"
                      ? "bg-[#0984e3]/10 border-[#0984e3]/50 shadow-[#0984e3]/5"
                      : "bg-neutral-800/20 border-neutral-700/60 shadow-black/5"

                  return (
                    <div
                      key={item.id || idx}
                      className={`relative overflow-hidden rounded-2xl p-5 border backdrop-blur-md transition shadow-md ${cardCls}`}
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
                              Маркет: <span className="text-neutral-200 font-medium">{item.market_prefix || "Основной"} — {item.label}</span>
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
                            <div className="text-[10px] text-neutral-400 font-mono uppercase">Прогноз сети</div>
                            <div className={`text-sm font-black font-mono mt-0.5 ${
                              outcomeCall === 1 ? "text-[#55efc4]" : outcomeCall === 0 ? "text-[#ff7675]" : "text-neutral-500"
                            }`}>
                              {outcomeCall === 1 ? "🟢 выиграет" : outcomeCall === 0 ? "🔴 проиграет" : "—"}
                            </div>
                            {item.predicted_win === 0 && outcomeCall === 1 && (
                              <div className="text-[10px] text-neutral-500 font-mono mt-0.5">
                                не ставить
                              </div>
                            )}
                            {item.predicted_win_probability != null && (
                              <div className="text-[10px] text-neutral-400 font-mono mt-0.5">
                                {item.predicted_win_probability}%
                              </div>
                            )}
                          </div>

                          {/* Guess Status Badge */}
                          <div className={`px-4 py-3 rounded-xl border flex items-center justify-center gap-1.5 text-xs font-black font-mono shrink-0 shadow-md ${
                            status === "correct"
                              ? "bg-[#00b894] text-neutral-950 border-[#55efc4]"
                              : status === "incorrect"
                              ? "bg-[#d63031] text-white border-[#ff7675]"
                              : status === "push"
                              ? "bg-[#0984e3] text-white border-[#74b9ff]"
                              : "bg-neutral-700 text-neutral-200 border-neutral-600"
                          }`}>
                            {status === "correct" ? (
                              <>
                                <CheckCircle2 className="w-4 h-4 text-neutral-950" />
                                УГАДАНО
                              </>
                            ) : status === "incorrect" ? (
                              <>
                                <AlertTriangle className="w-4 h-4 text-white" />
                                НЕ УГАДАНО
                              </>
                            ) : status === "push" ? (
                              <>
                                <Info className="w-4 h-4 text-white" />
                                ВОЗВРАТ
                              </>
                            ) : (
                              <>
                                <Info className="w-4 h-4 text-neutral-300" />
                                НЕ РАССЧИТАНА
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

            {!historyLoading && historyItems.length > 0 && (
              <>
                {historyOffsetRef.current < (historySummary?.filtered_count ?? historySummary?.total_count ?? 0) && (
                  <LoadMoreSentinel onIntersect={loadMoreHistory} disabled={loadingMoreHistory} />
                )}
                {loadingMoreHistory ? (
                  <div className="flex items-center justify-center gap-2 text-neutral-400 text-xs py-4">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Загрузка ещё записей истории...
                  </div>
                ) : historyOffsetRef.current >= (historySummary?.filtered_count ?? historySummary?.total_count ?? 0) ? (
                  <div className="text-center text-neutral-600 text-xs py-4">
                    Показаны все записи ({(historySummary?.filtered_count ?? historySummary?.total_count ?? 0).toLocaleString()})
                  </div>
                ) : null}
              </>
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
                  {searchQuery ? "Ничего не найдено по запросу" : "Ожидание данных от парсера и нейросети..."}
                </p>
                <p className="text-xs text-neutral-400 max-w-md mx-auto">
                  {searchQuery
                    ? `По запросу «${searchQuery}» нет исходов в выбранном фильтре (${
                        verdictFilter === "win" ? "выигрывающие" : verdictFilter === "loss" ? "проигрывающие" : "все"
                      }). Попробуйте другой фильтр вердикта или измените запрос.`
                    : "Парсер Fonbet LIVE сканирует активные матчи, а LightGBM & PyTorch в реальном времени определяют вердикт по каждому исходу — выиграет он или проиграет. Данные обновляются каждые 10 секунд."}
                </p>
              </div>
            ) : (
              <AnimatePresence initial={false} mode="popLayout">
              {liveBets.map((bet, index) => {
                const currentRank = index + 1
                const isRose = bet.coefficient > bet.initialCoefficient
                const isDropped = bet.coefficient < bet.initialCoefficient

                return (
                  <motion.div
                    key={bet.id}
                    layout
                    initial={{ opacity: 0, y: -12, scale: 0.98 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.97 }}
                    transition={{ layout: { type: "spring", stiffness: 350, damping: 32 }, duration: 0.25 }}
                    className={`relative overflow-hidden rounded-2xl bg-neutral-900/80 border transition-colors hover:border-neutral-700 shadow-lg p-5 md:p-6 space-y-4 ${
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

                        <div className={`px-2.5 py-1 rounded-lg text-[10px] font-black font-mono uppercase flex items-center gap-1 ${
                          bet.predictedWin === 1
                            ? "bg-[#00b894]/15 border border-[#00b894]/40 text-[#55efc4]"
                            : bet.willWin === 1
                            ? "bg-[#fdcb6e]/15 border border-[#fdcb6e]/40 text-[#ffeaa7]"
                            : bet.willWin === 0
                            ? "bg-[#d63031]/15 border border-[#d63031]/40 text-[#ff7675]"
                            : "bg-neutral-800/80 border border-neutral-700 text-neutral-400"
                        }`}>
                          {bet.predictedWin === 1 ? (
                            <>
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Сеть ставит: выиграет
                            </>
                          ) : bet.willWin === 1 ? (
                            <>
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              Выиграет · не ставить
                            </>
                          ) : bet.willWin === 0 ? (
                            <>
                              <AlertTriangle className="w-3.5 h-3.5" />
                              Скорее всего не победит
                            </>
                          ) : (
                            <>
                              <AlertTriangle className="w-3.5 h-3.5" />
                              Сейчас не ставить
                            </>
                          )}
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

                      {/* Odds & Value Index (5 cols) — outer div only handles column
                          placement, the background/border box sizes to its own content
                          (not the full 5-column width) so it doesn't leave a big empty
                          strip when right-aligned. */}
                      <div className="lg:col-span-5 flex justify-center lg:justify-end">
                        <div className="flex flex-wrap sm:flex-nowrap items-center gap-4 bg-neutral-950/60 p-3 rounded-xl border border-neutral-800/80">
                          {/* Coefficient display */}
                          <div>
                            <div className="text-[10px] text-neutral-400 font-mono">Коэффициент</div>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              <motion.span
                                key={bet.coefficient}
                                initial={{ color: isRose ? "#ff7675" : isDropped ? "#55efc4" : "#ffffff" }}
                                animate={{ color: "#ffffff" }}
                                transition={{ duration: 0.8 }}
                                className="text-xl font-black font-mono"
                              >
                                {bet.coefficient.toFixed(2)}
                              </motion.span>
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
                            <motion.div
                              key={bet.expectedRoi}
                              initial={{ scale: 1.15, opacity: 0.6 }}
                              animate={{ scale: 1, opacity: 1 }}
                              transition={{ duration: 0.4 }}
                              className="text-base font-black font-mono text-[#55efc4] mt-0.5"
                            >
                              +{bet.expectedRoi.toFixed(1)}%
                            </motion.div>
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* Bot Stake Banner — only shown for outcomes the bot actually has an open bet on.
                        Includes the live score right here (not just the badge up top) since that's
                        the value the bet's actual outcome depends on — it refreshes on the same
                        10s poll as the rest of the card. */}
                    {bet.stake !== null && (
                      <div className="flex flex-wrap items-center gap-3 bg-[#00b894]/10 border border-[#00b894]/40 rounded-xl px-4 py-2.5">
                        <span className="text-xs font-bold text-[#55efc4] flex items-center gap-1.5">
                          💰 Бот поставил:
                        </span>
                        <span className="text-sm font-black font-mono text-white">
                          {bet.stake.toFixed(1)} ₽
                        </span>
                        <span className="text-neutral-600">→</span>
                        <span className="text-xs text-neutral-300">При выигрыше получит:</span>
                        <span className="text-sm font-black font-mono text-[#55efc4]">
                          {bet.potentialPayout!.toFixed(1)} ₽
                        </span>
                        {bet.predictedWin === 0 && (
                          <span
                            className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-[#d63031]/15 text-[#ff7675] border border-[#d63031]/40"
                            title="Позиция уже открыта. Живой пересчёт: EV ниже порога, новую ставку бот сейчас бы не открыл."
                          >
                            (сейчас не ставить)
                          </span>
                        )}
                        <span className="ml-auto flex items-center gap-1.5 bg-neutral-950/80 px-2.5 py-1 rounded-lg border border-neutral-800">
                          <span className="text-[10px] text-neutral-400 font-mono uppercase">Live счёт</span>
                          <motion.span
                            key={bet.score}
                            initial={{ scale: 1.25, opacity: 0.6 }}
                            animate={{ scale: 1, opacity: 1 }}
                            transition={{ duration: 0.4 }}
                            className="font-mono font-black text-sm text-[#fdcb6e]"
                          >
                            {bet.score}
                          </motion.span>
                        </span>
                      </div>
                    )}

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
                          <motion.span
                            key={bet.aiProbability}
                            initial={{ scale: 1.2, opacity: 0.6 }}
                            animate={{ scale: 1, opacity: 1 }}
                            transition={{ duration: 0.4 }}
                            className="font-mono font-black text-[#55efc4] text-sm"
                          >
                            {bet.aiProbability.toFixed(1)}%
                          </motion.span>
                        </div>
                      </div>

                      {/* Animated Gradient Bar */}
                      <div className="w-full bg-neutral-900 rounded-full h-2.5 overflow-hidden border border-neutral-800">
                        <motion.div
                          className="bg-gradient-to-r from-[#00b894] via-[#55efc4] to-[#fdcb6e] h-full rounded-full shadow-sm"
                          initial={false}
                          animate={{ width: `${bet.aiProbability}%` }}
                          transition={{ type: "spring", stiffness: 120, damping: 20 }}
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
                  </motion.div>
                )
              })}
              </AnimatePresence>
            )}

            {!loading && liveBets.length > 0 && (
              <>
                {liveOffsetRef.current < liveTotal && (
                  <LoadMoreSentinel onIntersect={loadMoreLive} disabled={loadingMoreLive} />
                )}
                {loadingMoreLive ? (
                  <div className="flex items-center justify-center gap-2 text-neutral-400 text-xs py-4">
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Загрузка ещё ставок...
                  </div>
                ) : liveOffsetRef.current >= liveTotal ? (
                  <div className="text-center text-neutral-600 text-xs py-4">
                    Показаны все ставки ({liveTotal.toLocaleString()})
                  </div>
                ) : null}
              </>
            )}
          </div>
        )}
      </main>

      <footer className="border-t border-neutral-900 bg-neutral-950 py-4 px-6 text-center text-xs text-neutral-500">
        Нейроставки &copy; 2026 — AI прогнозы ставок
      </footer>
    </div>
  )
}
