"use client"

import { useEffect, useState } from "react"
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts"
import { TrendingUp, TrendingDown, Minus, Clock, Loader2 } from "lucide-react"

interface OddsPoint {
  id: number
  event_id: number
  factor_id: number
  market_prefix: string
  label: string
  parameter: string
  coefficient: number
  score_at_time: string
  timestamp: string
}

interface OddsHistoryGraphProps {
  eventId: number
  factorId: number
  parameter?: string
  marketPrefix?: string
  currentCoeff: number
  label: string
}

export function OddsHistoryGraph({
  eventId,
  factorId,
  parameter = "",
  marketPrefix = "",
  currentCoeff,
  label
}: OddsHistoryGraphProps) {
  const [history, setHistory] = useState<OddsPoint[]>([])
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"

  useEffect(() => {
    let isMounted = true
    async function fetchHistory() {
      setLoading(true)
      setError(null)
      try {
        const queryParams = new URLSearchParams({
          factor_id: factorId.toString(),
        })
        if (parameter) queryParams.append("parameter", parameter)
        if (marketPrefix) queryParams.append("market_prefix", marketPrefix)

        const res = await fetch(`${API_BASE}/api/matches/${eventId}/odds-history?${queryParams.toString()}`)
        if (!res.ok) throw new Error("Failed to load odds history")
        const data = await res.json()
        if (isMounted) {
          setHistory(data.history || [])
        }
      } catch (err: any) {
        if (isMounted) {
          setError(err.message || "Failed to load history")
        }
      } finally {
        if (isMounted) setLoading(false)
      }
    }

    fetchHistory()
    return () => {
      isMounted = false
    }
  }, [eventId, factorId, parameter, marketPrefix, API_BASE])

  const chartData = history.map((item) => {
    const timeStr = item.timestamp ? item.timestamp.split(" ")[1] || item.timestamp : ""
    return {
      time: timeStr,
      coeff: item.coefficient,
      score: item.score_at_time,
      fullTime: item.timestamp,
    }
  })

  const minCoeff = history.length > 0 ? Math.min(...history.map((h) => h.coefficient)) : currentCoeff
  const maxCoeff = history.length > 0 ? Math.max(...history.map((h) => h.coefficient)) : currentCoeff
  const firstCoeff = history.length > 0 ? history[0].coefficient : currentCoeff
  const diff = currentCoeff - firstCoeff
  const isUp = diff > 0.001
  const isDown = diff < -0.001

  return (
    <div className="w-80 p-3 bg-neutral-900 border border-neutral-800 text-white rounded-xl shadow-2xl backdrop-blur-md">
      <div className="flex items-center justify-between pb-2 border-b border-neutral-800 mb-2">
        <div className="text-xs font-semibold text-neutral-300 truncate max-w-[190px]">
          {label}
        </div>
        <div className="flex items-center gap-1">
          {isUp && (
            <span className="flex items-center text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#00b894]/20 text-[#55efc4] border border-[#00b894]/40">
              <TrendingUp className="w-3 h-3 mr-0.5" /> +{diff.toFixed(2)}
            </span>
          )}
          {isDown && (
            <span className="flex items-center text-[10px] font-bold px-1.5 py-0.5 rounded bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/40">
              <TrendingDown className="w-3 h-3 mr-0.5" /> {diff.toFixed(2)}
            </span>
          )}
          {!isUp && !isDown && (
            <span className="flex items-center text-[10px] font-medium px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400">
              <Minus className="w-3 h-3 mr-0.5" /> 0.00
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-center text-[10px] mb-2 bg-neutral-950/60 p-1.5 rounded-lg border border-neutral-800/80">
        <div>
          <span className="text-neutral-500 block">Старт</span>
          <span className="font-mono font-medium text-neutral-300">{firstCoeff.toFixed(2)}</span>
        </div>
        <div>
          <span className="text-neutral-500 block">Мин / Макс</span>
          <span className="font-mono font-medium text-neutral-300">
            {minCoeff.toFixed(2)} - {maxCoeff.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="text-neutral-500 block">Текущий</span>
          <span className="font-mono font-bold text-[#fdcb6e]">{currentCoeff.toFixed(2)}</span>
        </div>
      </div>

      {loading ? (
        <div className="h-32 flex flex-col items-center justify-center text-xs text-neutral-400 gap-2">
          <Loader2 className="w-5 h-5 animate-spin text-[#fdcb6e]" />
          <span>Загрузка истории...</span>
        </div>
      ) : error ? (
        <div className="h-32 flex items-center justify-center text-xs text-[#ff7675]">
          {error}
        </div>
      ) : history.length <= 1 ? (
        <div className="h-28 flex flex-col items-center justify-center text-xs text-neutral-400 gap-1">
          <Clock className="w-4 h-4 text-neutral-500" />
          <span>Мало данных истории</span>
          <span className="text-[10px] text-neutral-500">Парсинг обновляет график каждую минуту</span>
        </div>
      ) : (
        <div className="h-36 w-full pt-1">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 5, right: 10, left: -25, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262626" />
              <XAxis dataKey="time" stroke="#737373" fontSize={9} tickLine={false} />
              <YAxis domain={['auto', 'auto']} stroke="#737373" fontSize={9} tickLine={false} />
              <Tooltip
                content={({ active, payload }) => {
                  if (active && payload && payload.length) {
                    const data = payload[0].payload
                    return (
                      <div className="bg-neutral-950 border border-neutral-800 p-2 rounded shadow-lg text-[10px]">
                        <div className="text-neutral-400">{data.fullTime}</div>
                        <div className="font-bold text-[#fdcb6e] text-xs">Коэф: {data.coeff}</div>
                        <div className="text-neutral-300">Счет в матче: {data.score}</div>
                      </div>
                    )
                  }
                  return null
                }}
              />
              <Line
                type="monotone"
                dataKey="coeff"
                stroke="#0984e3"
                strokeWidth={2}
                dot={{ r: 2, fill: "#0984e3" }}
                activeDot={{ r: 5, fill: "#74b9ff", stroke: "#fff" }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
