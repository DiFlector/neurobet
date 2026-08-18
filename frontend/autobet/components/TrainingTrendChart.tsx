"use client"

import { useMemo } from "react"
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid
} from "recharts"
import { limitChartPoints } from "@/lib/chartPoints"

interface TrainingRun {
  generated_at: string
  samples_used: number
  best_epoch: number | null
  epochs_run: number | null
  train_loss: number | null
  train_guess_rate: number | null
  val_loss: number | null
  val_guess_rate: number | null
  checkpoint_accepted?: boolean | null
  val_loss_incoming?: number | null
  val_loss_attempted?: number | null
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
  // left-to-right as oldest-to-newest. A rejected pass still occupies its
  // timestamp, but val/train metrics are the previous checkpoint's (0.18 stayed
  // 0.18). Carry-forward here too so already-written rejected rows plot that way
  // before the next training pass rewrites them.
  const chartData = useMemo(() => {
    const chronological = [...history].reverse()
    let lastVal: number | null = null
    let lastTrain: number | null = null
    let lastGuess: number | null = null
    const carried = chronological.map((r) => {
      const saved = r.checkpoint_accepted !== false
      if (saved) {
        lastVal = r.val_loss
        lastTrain = r.train_loss
        lastGuess = r.val_guess_rate
      }
      return {
        label: formatTick(r.generated_at),
        fullDate: r.generated_at,
        samplesUsed: r.samples_used,
        bestEpoch: r.best_epoch,
        epochsRun: r.epochs_run,
        saved,
        trainLoss: saved ? r.train_loss : lastTrain,
        valLoss: saved ? r.val_loss : lastVal,
        valGuessRate: saved ? r.val_guess_rate : lastGuess,
        valIncoming: r.val_loss_incoming ?? null,
        valAttempted: r.val_loss_attempted ?? null,
      }
    })
    return limitChartPoints(carried)
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
                        {!d.saved && (
                          <div className="text-[10px] font-semibold text-[#fdcb6e]">
                            веса те же, хуже не записали
                          </div>
                        )}
                        <div className="font-bold text-xs text-[#fd79a8]">
                          val_loss: {d.valLoss != null ? d.valLoss.toFixed(4) : "—"}
                        </div>
                        {d.valAttempted != null && d.valIncoming != null && !d.saved && (
                          <div className="text-xs text-neutral-500">
                            попытка {d.valAttempted.toFixed(4)} (вход {d.valIncoming.toFixed(4)})
                          </div>
                        )}
                        <div className="text-xs text-neutral-400">
                          train_loss: {d.trainLoss != null ? d.trainLoss.toFixed(4) : "—"}
                        </div>
                      </>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="valLoss" stroke="#fd79a8" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "#fff" }} connectNulls />
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
                      <>
                        {!d.saved && (
                          <div className="text-[10px] font-semibold text-[#fdcb6e]">
                            веса те же, хуже не записали
                          </div>
                        )}
                        <div className="font-bold text-xs text-[#0984e3]">
                          val_guess_rate: {d.valGuessRate != null ? `${d.valGuessRate}%` : "—"}
                        </div>
                      </>
                    )}
                  />
                )}
              />
              <Line type="monotone" dataKey="valGuessRate" stroke="#0984e3" strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: "#fff" }} connectNulls />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}
