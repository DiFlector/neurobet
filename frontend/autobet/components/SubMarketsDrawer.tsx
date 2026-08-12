"use client"

import { X, Layers } from "lucide-react"
import { OddsButton } from "./OddsButton"

interface OddItem {
  factor_id: number
  market_prefix: string
  label: string
  parameter: string
  coefficient: number
  initial_coefficient?: number
}

interface SubMarketsDrawerProps {
  matchName: string
  score: string
  sportPath: string
  odds: OddItem[]
  eventId: number
  isOpen: boolean
  onClose: () => void
  safeMode?: boolean
}

export function SubMarketsDrawer({
  matchName,
  score,
  sportPath,
  odds,
  eventId,
  isOpen,
  onClose,
  safeMode = false
}: SubMarketsDrawerProps) {
  if (!isOpen) return null

  // Filter out dangerous odds if safeMode is true (< 1.1 or > 2.1)
  const filteredOdds = safeMode
    ? odds.filter((o) => o.coefficient >= 1.1 && o.coefficient <= 2.1)
    : odds

  // Group odds by market_prefix
  const groupedOdds: Record<string, OddItem[]> = {}
  filteredOdds.forEach((odd) => {
    const prefix = odd.market_prefix || "Основной матч"
    if (!groupedOdds[prefix]) {
      groupedOdds[prefix] = []
    }
    groupedOdds[prefix].push(odd)
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl max-h-[85vh] bg-neutral-900 border border-neutral-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden text-white">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-neutral-800 bg-neutral-950">
          <div>
            <div className="text-xs text-[#fdcb6e] font-medium">{sportPath}</div>
            <h2 className="text-lg font-bold flex items-center gap-2 text-white">
              <span>{matchName}</span>
              <span className="px-2 py-0.5 rounded bg-[#fdcb6e]/20 text-[#ffeaa7] text-sm font-mono border border-[#fdcb6e]/30">
                {score}
              </span>
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-neutral-800 text-neutral-400 hover:text-white hover:bg-neutral-700 transition"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4 space-y-6 custom-scrollbar">
          {Object.keys(groupedOdds).length === 0 ? (
            <div className="py-12 text-center text-neutral-400 text-sm">
              Нет доступных исходов {safeMode ? "(в Безопасном режиме)" : ""}
            </div>
          ) : (
            Object.entries(groupedOdds).map(([prefix, marketOdds]) => (
              <div key={prefix} className="bg-neutral-950/60 p-4 rounded-xl border border-neutral-800/80">
                <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-3 flex items-center gap-2">
                  <Layers className="w-3.5 h-3.5 text-[#0984e3]" />
                  {prefix} ({marketOdds.length} исходов)
                </h3>
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2.5">
                  {marketOdds.map((odd, idx) => (
                    <OddsButton
                      key={`${odd.factor_id}-${odd.parameter}-${idx}`}
                      eventId={eventId}
                      factorId={odd.factor_id}
                      parameter={odd.parameter}
                      marketPrefix={odd.market_prefix}
                      coefficient={odd.coefficient}
                      initialCoefficient={odd.initial_coefficient}
                      label={odd.label}
                      shortLabel={odd.label}
                      variant="full"
                      safeMode={safeMode}
                    />
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
