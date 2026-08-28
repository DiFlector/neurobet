export const ADMIN_TOKEN_KEY = "admin_token"
export const ADMIN_TOKEN_VALUE = "diflector-admin-secret-token"

/** Migrate one-time from sessionStorage (old behavior) to localStorage. */
export function migrateAdminSessionStorage(): void {
  if (typeof window === "undefined") return
  if (localStorage.getItem(ADMIN_TOKEN_KEY) === ADMIN_TOKEN_VALUE) return
  const legacy = sessionStorage.getItem(ADMIN_TOKEN_KEY)
  if (legacy === ADMIN_TOKEN_VALUE) {
    localStorage.setItem(ADMIN_TOKEN_KEY, ADMIN_TOKEN_VALUE)
    sessionStorage.removeItem(ADMIN_TOKEN_KEY)
  }
}

export function getAdminToken(): string | null {
  if (typeof window === "undefined") return null
  migrateAdminSessionStorage()
  return localStorage.getItem(ADMIN_TOKEN_KEY)
}

export function isAdminLoggedIn(): boolean {
  return getAdminToken() === ADMIN_TOKEN_VALUE
}

export function setAdminSession(): void {
  if (typeof window === "undefined") return
  localStorage.setItem(ADMIN_TOKEN_KEY, ADMIN_TOKEN_VALUE)
  sessionStorage.removeItem(ADMIN_TOKEN_KEY)
  window.dispatchEvent(new Event("admin-auth-changed"))
}

export function clearAdminSession(): void {
  if (typeof window === "undefined") return
  localStorage.removeItem(ADMIN_TOKEN_KEY)
  sessionStorage.removeItem(ADMIN_TOKEN_KEY)
  window.dispatchEvent(new Event("admin-auth-changed"))
}

export function subscribeAdminAuth(onChange: () => void): () => void {
  if (typeof window === "undefined") return () => {}
  const handler = () => onChange()
  window.addEventListener("storage", handler)
  window.addEventListener("admin-auth-changed", handler)
  return () => {
    window.removeEventListener("storage", handler)
    window.removeEventListener("admin-auth-changed", handler)
  }
}

export function adminAuthHeaders(): HeadersInit {
  const token = getAdminToken()
  return token ? { "X-Admin-Token": token } : {}
}
