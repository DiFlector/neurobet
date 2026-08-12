"use client"

import { useState } from "react"
import { Trophy, Clock, ChevronRight, Activity } from "lucide-react"
import { OddsButton } from "./OddsButton"
import { SubMarketsDrawer } from "./SubMarketsDrawer"

interface OddItem {
  factor_id: number
  market_prefix: string
  label: string
  parameter: string
  coefficient: number
  initial_coefficient?: number
}

interface MatchCardProps {
  eventId: number
  sportPath: string
  matchName: string
  team1: string
  team2: string
  score1: number
  score2: number
  score: string
  timer: string
  isLive: boolean
  totalOddsCount: number
  odds: OddItem[]
  subMarkets: Array<{ sub_event_id: number; market_name: string; odds_count: number }>
  safeMode?: boolean
}

export function MatchCard({
  eventId,
  sportPath,
  matchName,
  team1,
  team2,
  score1,
  score2,
  score,
  timer,
  isLive,
  totalOddsCount,
  odds,
  subMarkets,
  safeMode = false
}: MatchCardProps) {
  const [drawerOpen, setDrawerOpen] = useState(false)

  // Filter main event odds
  const mainOdds = odds.filter(
    (o) => !o.market_prefix || o.market_prefix === "Основной матч"
  )

  // Helper to find specific factor
  const findOdd = (fId: number, param?: string) => {
    return mainOdds.find((o) => o.factor_id === fId && (!param || o.parameter === param))
  }

  const p1 = findOdd(921)
  const px = findOdd(922)
  const p2 = findOdd(923)

  const double1x = findOdd(924)
  const double12 = findOdd(925)
  const doublex2 = findOdd(926)

  const totalOver = mainOdds.find((o) => [930, 1696, 1727, 1730, 1733, 1736, 1739, 1791, 1794, 1797, 1804].includes(o.factor_id))
  const totalUnder = mainOdds.find((o) => [931, 1697, 1728, 1731, 1734, 1737, 1740, 1793, 1796, 1802, 1805].includes(o.factor_id))

  const fora1 = mainOdds.find((o) => [927, 910, 989, 1569, 1678, 1681].includes(o.factor_id))
  const fora2 = mainOdds.find((o) => [928, 912, 991, 1572, 1677, 1680].includes(o.factor_id))

  return (
    <>
      <div className="bg-neutral-900/90 border border-neutral-800 rounded-2xl p-4 shadow-xl hover:border-neutral-700/80 transition-all duration-200 flex flex-col justify-between backdrop-blur-sm">
        {/* Top Header: Sport & Timer */}
        <div>
          <div className="flex items-center justify-between gap-2 mb-3">
            <span className="text-[11px] font-semibold text-neutral-400 truncate max-w-[240px] flex items-center gap-1.5">
              <Trophy className="w-3.5 h-3.5 text-[#0984e3] shrink-0" />
              {sportPath}
            </span>
            <div className="flex items-center gap-2">
              {isLive && (
                <span className="flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full bg-[#d63031]/20 text-[#ff7675] border border-[#d63031]/40">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#d63031] animate-ping" />
                  LIVE
                </span>
              )}
              {timer && (
                <span className="text-[11px] font-mono text-neutral-400 flex items-center gap-1 bg-neutral-950 px-2 py-0.5 rounded border border-neutral-800">
                  <Clock className="w-3 h-3 text-neutral-500" />
                  {timer}
                </span>
              )}
            </div>
          </div>

          {/* Teams and Live Score Display */}
          <div className="bg-neutral-950/80 p-3 rounded-xl border border-neutral-800/80 mb-4">
            <div className="flex items-center justify-between gap-4">
              <div className="flex-1 space-y-1.5">
                <div className="font-semibold text-sm text-neutral-100 truncate">
                  {team1 || matchName}
                </div>
                <div className="font-semibold text-sm text-neutral-100 truncate">
                  {team2 || "Соперник"}
                </div>
              </div>
              <div className="flex flex-col items-center justify-center bg-[#fdcb6e]/15 px-3.5 py-1.5 rounded-lg border border-[#fdcb6e]/30">
                <span className="text-lg font-mono font-extrabold text-[#fdcb6e] tracking-wider">
                  {score}
                </span>
              </div>
            </div>
          </div>

          {/* Main Odds Grid */}
          <div className="space-y-2 mb-3">
            {/* 1X2 */}
            {(p1 || px || p2) && (
              <div className="grid grid-cols-3 gap-2">
                {p1 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={p1.factor_id}
                    parameter={p1.parameter}
                    marketPrefix={p1.market_prefix}
                    coefficient={p1.coefficient}
                    initialCoefficient={p1.initial_coefficient}
                    label={p1.label}
                    shortLabel="П1"
                    safeMode={safeMode}
                  />
                )}
                {px && (
                  <OddsButton
                    eventId={eventId}
                    factorId={px.factor_id}
                    parameter={px.parameter}
                    marketPrefix={px.market_prefix}
                    coefficient={px.coefficient}
                    initialCoefficient={px.initial_coefficient}
                    label={px.label}
                    shortLabel="Х"
                    safeMode={safeMode}
                  />
                )}
                {p2 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={p2.factor_id}
                    parameter={p2.parameter}
                    marketPrefix={p2.market_prefix}
                    coefficient={p2.coefficient}
                    initialCoefficient={p2.initial_coefficient}
                    label={p2.label}
                    shortLabel="П2"
                    safeMode={safeMode}
                  />
                )}
              </div>
            )}

            {/* Double Chance */}
            {(double1x || double12 || doublex2) && (
              <div className="grid grid-cols-3 gap-2">
                {double1x && (
                  <OddsButton
                    eventId={eventId}
                    factorId={double1x.factor_id}
                    parameter={double1x.parameter}
                    marketPrefix={double1x.market_prefix}
                    coefficient={double1x.coefficient}
                    initialCoefficient={double1x.initial_coefficient}
                    label={double1x.label}
                    shortLabel="1Х"
                    safeMode={safeMode}
                  />
                )}
                {double12 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={double12.factor_id}
                    parameter={double12.parameter}
                    marketPrefix={double12.market_prefix}
                    coefficient={double12.coefficient}
                    initialCoefficient={double12.initial_coefficient}
                    label={double12.label}
                    shortLabel="12"
                    safeMode={safeMode}
                  />
                )}
                {doublex2 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={doublex2.factor_id}
                    parameter={doublex2.parameter}
                    marketPrefix={doublex2.market_prefix}
                    coefficient={doublex2.coefficient}
                    initialCoefficient={doublex2.initial_coefficient}
                    label={doublex2.label}
                    shortLabel="Х2"
                    safeMode={safeMode}
                  />
                )}
              </div>
            )}

            {/* Totals & Handicaps */}
            {(totalOver || totalUnder || fora1 || fora2) && (
              <div className="grid grid-cols-2 gap-2 pt-1 border-t border-neutral-800/60">
                {totalOver && (
                  <OddsButton
                    eventId={eventId}
                    factorId={totalOver.factor_id}
                    parameter={totalOver.parameter}
                    marketPrefix={totalOver.market_prefix}
                    coefficient={totalOver.coefficient}
                    initialCoefficient={totalOver.initial_coefficient}
                    label={totalOver.label}
                    shortLabel={`ТБ (${totalOver.parameter || ""})`}
                    safeMode={safeMode}
                  />
                )}
                {totalUnder && (
                  <OddsButton
                    eventId={eventId}
                    factorId={totalUnder.factor_id}
                    parameter={totalUnder.parameter}
                    marketPrefix={totalUnder.market_prefix}
                    coefficient={totalUnder.coefficient}
                    initialCoefficient={totalUnder.initial_coefficient}
                    label={totalUnder.label}
                    shortLabel={`ТМ (${totalUnder.parameter || ""})`}
                    safeMode={safeMode}
                  />
                )}
                {fora1 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={fora1.factor_id}
                    parameter={fora1.parameter}
                    marketPrefix={fora1.market_prefix}
                    coefficient={fora1.coefficient}
                    initialCoefficient={fora1.initial_coefficient}
                    label={fora1.label}
                    shortLabel={`Ф1 (${fora1.parameter || ""})`}
                    safeMode={safeMode}
                  />
                )}
                {fora2 && (
                  <OddsButton
                    eventId={eventId}
                    factorId={fora2.factor_id}
                    parameter={fora2.parameter}
                    marketPrefix={fora2.market_prefix}
                    coefficient={fora2.coefficient}
                    initialCoefficient={fora2.initial_coefficient}
                    label={fora2.label}
                    shortLabel={`Ф2 (${fora2.parameter || ""})`}
                    safeMode={safeMode}
                  />
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer: All markets button */}
        <button
          onClick={() => setDrawerOpen(true)}
          className="w-full flex items-center justify-between px-3 py-2 rounded-xl bg-neutral-950/80 border border-neutral-800 text-xs font-medium text-neutral-300 hover:text-[#fdcb6e] hover:border-[#fdcb6e]/40 hover:bg-neutral-800/60 transition group mt-2"
        >
          <span className="flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-[#00b894]" />
            Все исходы ({totalOddsCount})
          </span>
          <ChevronRight className="w-4 h-4 text-neutral-500 group-hover:translate-x-0.5 transition" />
        </button>
      </div>

      <SubMarketsDrawer
        matchName={matchName}
        score={score}
        sportPath={sportPath}
        odds={odds}
        eventId={eventId}
        isOpen={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        safeMode={safeMode}
      />
    </>
  )
}
