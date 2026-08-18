/** Max points drawn on any admin trend chart. These panels are ~300–400px wide;
 * more than this turns the line into a solid band of dots. */
export const MAX_CHART_POINTS = 40

/**
 * Evenly sample `items` down to `max` points, always keeping the first and last
 * so the full time range still reads as a trend. `items` must already be in
 * display (oldest → newest) order.
 */
export function limitChartPoints<T>(items: T[], max = MAX_CHART_POINTS): T[] {
  const n = items.length
  if (n <= max) return items
  const out: T[] = []
  const last = n - 1
  let prev = -1
  for (let i = 0; i < max; i++) {
    const idx = Math.round((i * last) / (max - 1))
    if (idx === prev) continue
    out.push(items[idx])
    prev = idx
  }
  return out
}
