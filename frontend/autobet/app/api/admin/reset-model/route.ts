export const maxDuration = 180

const BACKEND_INTERNAL_URL =
  process.env.BACKEND_INTERNAL_URL || "http://localhost:8000"

export async function POST() {
  const res = await fetch(`${BACKEND_INTERNAL_URL}/api/admin/reset-model`, {
    method: "POST",
    cache: "no-store",
  })
  const body = await res.text()
  return new Response(body, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") || "application/json",
    },
  })
}
