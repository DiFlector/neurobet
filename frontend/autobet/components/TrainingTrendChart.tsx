"use client"

import { useMemo } from "react"
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts"

interface TrainingRun {
  generated_at: string
  samples_used: number
  best_epoch: number | null
  epochs_run: number | null
  train_loss: number | null
  train_guess_rate: number | null
  val_loss: number | null
  val_guess_rate: number | null
}

interface TrainingTrendChartProps {
  history: TrainingRun[]
}

function formatTick(iso: string): string {
  const dt = new Date(iso.replace(" ", "T"))
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
      <div className="text-neutral-500">
        {d.samplesUsed?.toLocaleString()} сэмплов · best epoch {d.bestEpoch}/{d.epochsRun}
      </div>
      {formatter(d)}
    </div>
  )
}

export function TrainingTrendChart({ history }: TrainingTrendChartProps) {
  // training_runs.json arrives newest-first — reversed so the chart reads
  // left-to-right as oldest-to-newest. Passes without a val split yet (val_loss null —
  // not enough resolved bets held out) are kept in the series with a null value so the
  // line just has a gap there instead of the x-axis compressing around them.
  const chartData = useMemo(() => {
    return [...history].reverse().map((r) => ({
      label: formatTick(r.generated_at),
      fullDate: r.generated_at,
      samplesUsed: r.samples_used,
      bestEpoch: r.best_epoch,
      epochsRun: r.epochs_run,
      trainLoss: r.train_loss,
      valLoss: r.val_loss,
      valGuessRate: r.val_guess_rate,
    }))
  }, [history])

  if (chartData.length < 2) {
    return (
      <div className="h-40 flex items-center justify-center text-xs text-neutral-500">
        Нужно хотя бы 2 прохода обучения, чтобы построить тренд
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">
          val_loss по проходам обучения (меньше — лучше)
        </div>
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
                        <div className="font-bold text-xs text-[#fd79a8]">
                          val_loss: {d.valLoss != null ? d.valLoss.toFixed(4) : "—"}
                        </div>
                        <div className="text-xs text-neutral-400">
                          train_loss: {d.trainLoss != null ? d.trainLoss.toFixed(4) : "—"}
                        </div>
                      </>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="valLoss" stroke="#fd79a8" strokeWidth={2} dot={{ r: 2, fill: "#fd79a8" }} activeDot={{ r: 5, stroke: "#fff" }} connectNulls />
              <Line type="monotone" dataKey="trainLoss" stroke="#525252" strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="bg-neutral-950 border border-neutral-800 rounded-xl p-3">
        <div className="text-[10px] text-neutral-400 font-mono uppercase mb-1.5">
          val_guess_rate по проходам обучения, %
        </div>
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
                      <div className="font-bold text-xs text-[#0984e3]">
                        val_guess_rate: {d.valGuessRate != null ? `${d.valGuessRate}%` : "—"}
                      </div>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="valGuessRate" stroke="#0984e3" strokeWidth={2} dot={{ r: 2, fill: "#0984e3" }} activeDot={{ r: 5, stroke: "#fff" }} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
