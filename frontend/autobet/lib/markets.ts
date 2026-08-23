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
