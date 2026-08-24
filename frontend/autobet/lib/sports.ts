// Canonical display order for sports across the whole app (LIVE parser filters,
// Нейроставки filters, and Статистика tabs) — keep in sync everywhere a sport list
// is rendered so the order matches regardless of which page you're on.
// `icon` is a lucide-react icon name (line icons, not emoji). See SportIcon and
// .cursor/rules/neurobet-no-emoji.mdc.
export const SPORT_ORDER: { id: string; icon: SportIconName }[] = [
  { id: "Футбол", icon: "Goal" },
  { id: "Теннис", icon: "CircleDot" },
  { id: "Киберспорт", icon: "Gamepad2" },
  { id: "Хоккей", icon: "Disc" },
  { id: "Баскетбол", icon: "Orbit" },
  { id: "Настольный теннис", icon: "Target" },
  { id: "Волейбол", icon: "Volleyball" },
  { id: "Единоборства", icon: "Swords" },
  { id: "Футзал", icon: "LandPlot" },
  { id: "Бейсбол", icon: "Diamond" },
  { id: "Регби", icon: "Egg" },
  { id: "Пляжный волейбол", icon: "Sun" },
  { id: "Баскетбол 3x3", icon: "Grid3x3" },
  { id: "Хоккей на траве", icon: "Flag" },
  { id: "Крикет", icon: "Crosshair" },
  { id: "Американский футбол", icon: "Shield" },
]

export type SportIconName =
  | "Goal"
  | "CircleDot"
  | "Gamepad2"
  | "Disc"
  | "Orbit"
  | "Target"
  | "Volleyball"
  | "Swords"
  | "LandPlot"
  | "Diamond"
  | "Egg"
  | "Sun"
  | "Grid3x3"
  | "Flag"
  | "Crosshair"
  | "Shield"

// Plain sport names in canonical order, no "all" entry, no icon — for pages that
// sort a backend-provided sport list (e.g. Статистика's per-sport breakdown).
export const SPORT_NAME_ORDER: string[] = SPORT_ORDER.map((s) => s.id)

/** Canonical model universe (ALLOWED_SPORTS). Parser chips stay on SPORT_FILTER_OPTIONS. */
export const UNIVERSE_SPORT_IDS = [
  "футбол",
  "баскетбол",
  "настольный теннис",
  "волейбол",
  "теннис",
] as const

export function universeSportOptions(enabledLower?: string[] | null) {
  // null/undefined → not loaded yet, show the full universe.
  // [] → admin turned every sport off; do not fall back to all five.
  const ids = enabledLower == null ? [...UNIVERSE_SPORT_IDS] : enabledLower
  const allow = new Set(ids.map((s) => s.toLowerCase()))
  return SPORT_ORDER.filter((s) => allow.has(s.id.toLowerCase()))
}

export function sportMeta(name: string): { id: string; icon: SportIconName } | undefined {
  const key = String(name || "").toLowerCase()
  return SPORT_ORDER.find((s) => s.id.toLowerCase() === key)
}

export function sportLabel(name: string): string {
  return sportMeta(name)?.id || name || "—"
}

// { id, label } options for sport-filter tab bars, with an "all" entry prepended.
// Labels are plain names; render <SportIcon /> next to them in the UI.
export const SPORT_FILTER_OPTIONS: { id: string; label: string }[] = [
  { id: "all", label: "Все виды спорта" },
  ...SPORT_ORDER.map((s) => ({ id: s.id, label: s.id })),
]

// Sorts any list of items with a sport-name field into canonical order; items whose
// name isn't in SPORT_NAME_ORDER fall to the end, alphabetically among themselves.
export function sortBySportOrder<T>(items: T[], getName: (item: T) => string): T[] {
  return [...items].sort((a, b) => {
    const ai = SPORT_NAME_ORDER.indexOf(getName(a))
    const bi = SPORT_NAME_ORDER.indexOf(getName(b))
    if (ai === -1 && bi === -1) return getName(a).localeCompare(getName(b), "ru")
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}
