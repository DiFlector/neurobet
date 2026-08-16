"use client"

import { useMemo } from "react"
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Legend
} from "recharts"

interface BacktestRun {
  generated_at: string
  samples_evaluated: number
  overall?: {
    current?: { accuracy_pct: number | null; bets: number; roi_pct: number | null; brier: number | null }
    market_brier?: number | null
  }
}

interface QualityTrendChartProps {
  history: BacktestRun[]
}

function formatTick(iso: string): string {
  const dt = new Date(iso)
  if (isNaN(dt.getTime())) return iso
  const pad = (n: number) => n.toString().padStart(2, "0")
  return `${pad(dt.getDate())}.${pad(dt.getMonth() + 1)} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

const axisProps = { stroke: "#737373", fontSize: 9, tickLine: false }

function MiniTooltip({ active, payload, formatter }: any) {
  if (!active || !payload || !payload.length) return null
  const d = payload[0].payload
  return (
    <div className="bg-neutral-950 border border-neutral-800 p-2 rounded shadow-lg text-[10px] space-y-0.5">
      <div className="text-neutral-400">{d.fullDate}</div>
      <div className="text-neutral-500">{d.samples?.toLocaleString()} сэмплов</div>
      {formatter(d)}
    </div>
  )
}

export function QualityTrendChart({ history }: QualityTrendChartProps) {
  // backtest_history arrives newest-first (see backend/ai_service's save_and_record) —
  // reversed here so the chart reads left-to-right as oldest-to-newest, the way a trend
  // is normally read.
  const chartData = useMemo(() => {
    return [...history].reverse().map((r) => {
      const cur = r.overall?.current
      return {
        label: formatTick(r.generated_at),
        fullDate: r.generated_at,
        samples: r.samples_evaluated,
        roi: cur?.roi_pct ?? null,
        accuracy: cur?.accuracy_pct ?? null,
        brier: cur?.brier ?? null,
        marketBrier: r.overall?.market_brier ?? null,
        bets: cur?.bets ?? 0,
      }
    })
  }, [history])

  if (chartData.length < 2) {
    return (
      <div className="h-40 flex items-center justify-center text-xs text-neutral-500">
        Нужно хотя бы 2 запуска бэктеста, чтобы построить тренд
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">ROI текущей модели, %</div>
        <div className="h-32 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 8, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...axisProps} domain={["auto", "auto"]} />
              <ReferenceLine y={0} stroke="#525252" strokeDasharray="4 4" />
              <Tooltip
                content={(p) => (
                  <MiniTooltip
                    {...p}
                    formatter={(d: any) => (
                      <div className={`font-bold text-xs ${d.roi == null ? "text-neutral-500" : d.roi >= 0 ? "text-[#55efc4]" : "text-[#ff7675]"}`}>
                        ROI: {d.roi != null ? `${d.roi}%` : "—"} ({d.bets} ставок)
                      </div>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="roi" stroke="#55efc4" strokeWidth={2} dot={{ r: 2, fill: "#55efc4" }} activeDot={{ r: 5, stroke: "#fff" }} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">Точность вердикта, %</div>
        <div className="h-32 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 8, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...axisProps} domain={[0, 100]} />
              <Tooltip
                content={(p) => (
                  <MiniTooltip
                    {...p}
                    formatter={(d: any) => (
                      <div className="font-bold text-xs text-[#74b9ff]">
                        Точность: {d.accuracy != null ? `${d.accuracy}%` : "—"}
                      </div>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="accuracy" stroke="#74b9ff" strokeWidth={2} dot={{ r: 2, fill: "#74b9ff" }} activeDot={{ r: 5, stroke: "#fff" }} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">Brier: модель vs рынок (меньше — лучше)</div>
        <div className="h-32 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 8, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...axisProps} domain={["auto", "auto"]} />
              <Tooltip
                content={(p) => (
                  <MiniTooltip
                    {...p}
                    formatter={(d: any) => (
                      <>
                        <div className={`font-bold text-xs ${d.brier != null && d.marketBrier != null && d.brier < d.marketBrier ? "text-[#a29bfe]" : "text-[#ff7675]"}`}>
                          Модель: {d.brier ?? "—"}
                        </div>
                        <div className="text-xs text-neutral-400">Рынок: {d.marketBrier ?? "—"}</div>
                      </>
                    )}
                  />
                )}
              />
              <Legend wrapperStyle={{ fontSize: 9 }} formatter={(v) => (v === "brier" ? "Модель" : "Рынок")} />
              <Line type="monotone" dataKey="brier" stroke="#a29bfe" strokeWidth={2} dot={{ r: 2, fill: "#a29bfe" }} activeDot={{ r: 5, stroke: "#fff" }} connectNulls />
              <Line type="monotone" dataKey="marketBrier" stroke="#737373" strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">
          Ставок за прогон — контекст для доверия к ROI
        </div>
        <div className="h-32 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 5, right: 8, left: -22, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="label" {...axisProps} />
              <YAxis {...axisProps} allowDecimals={false} />
              <Tooltip
                content={(p) => (
                  <MiniTooltip
                    {...p}
                    formatter={(d: any) => (
                      <div className="font-bold text-xs text-[#fdcb6e]">Ставок: {d.bets}</div>
                    )}
                  />
                )}
              />
              <Bar dataKey="bets" fill="#fdcb6e" radius={[2, 2, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
