// Canonical display order for sports across the whole app (LIVE parser filters,
// Нейроставки filters, and Статистика tabs) — keep in sync everywhere a sport list
// is rendered so the order matches regardless of which page you're on.
export const SPORT_ORDER: { id: string; emoji: string }[] = [
  { id: "Футбол", emoji: "⚽" },
  { id: "Теннис", emoji: "🎾" },
  { id: "Киберспорт", emoji: "🎮" },
  { id: "Хоккей", emoji: "🏒" },
  { id: "Баскетбол", emoji: "🏀" },
  { id: "Настольный теннис", emoji: "🏓" },
  { id: "Волейбол", emoji: "🏐" },
  { id: "Единоборства", emoji: "🥊" },
  { id: "Футзал", emoji: "🥅" },
  { id: "Бейсбол", emoji: "⚾" },
  { id: "Регби", emoji: "🏉" },
  { id: "Пляжный волейбол", emoji: "🏖️" },
  { id: "Баскетбол 3x3", emoji: "🏀" },
  { id: "Хоккей на траве", emoji: "🏑" },
  { id: "Крикет", emoji: "🏏" },
  { id: "Американский футбол", emoji: "🏈" },
]

// Plain sport names in canonical order, no "all" entry, no emoji — for pages that
// sort a backend-provided sport list (e.g. Статистика's per-sport breakdown).
export const SPORT_NAME_ORDER: string[] = SPORT_ORDER.map((s) => s.id)

// { id, label } options for sport-filter tab bars, with an "all" entry prepended.
export const SPORT_FILTER_OPTIONS: { id: string; label: string }[] = [
  { id: "all", label: "Все виды спорта" },
  ...SPORT_ORDER.map((s) => ({ id: s.id, label: `${s.emoji} ${s.id}` })),
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
