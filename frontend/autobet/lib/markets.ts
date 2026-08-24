// Market-group chips for LIVE «Активные прогнозы».
// Keep ids in sync with neurobet_filters.normalize_market_group / market_group_sql.
export const MARKET_FILTER_OPTIONS: { id: string; label: string }[] = [
  { id: "all", label: "Все рынки" },
  { id: "result", label: "Исходы (П1, X, П2)" },
  { id: "totals", label: "Тоталы" },
  { id: "handicap", label: "Форы" },
  { id: "itotal", label: "Инд. тоталы" },
  { id: "other", label: "Прочие" },
]

/** Canonical live-universe families (ALLOWED_MARKET_FAMILIES). Admin toggles. */
export const UNIVERSE_MARKET_OPTIONS: { id: string; label: string }[] = [
  { id: "w1", label: "П1" },
  { id: "draw", label: "Ничья" },
  { id: "w2", label: "П2" },
  { id: "total_over", label: "Тотал больше" },
  { id: "total_under", label: "Тотал меньше" },
]

export const UNIVERSE_MARKET_IDS = UNIVERSE_MARKET_OPTIONS.map((m) => m.id)

/** Backtest `by_market` / vocab labels that map onto an admin family id. */
export const MARKET_BACKTEST_ALIASES: Record<string, string[]> = {
  w1: ["w1"],
  w2: ["w2"],
  draw: ["draw"],
  total_over: ["total_over", "total_over"],
  total_under: ["total_under", "total_under"],
}
