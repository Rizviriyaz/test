import { supabase } from "../lib/supabase"
import { clearClientCache, deduplicatedFetch as dedupeRequest, getFromClientCache, peekClientCache } from "./requestCache"
import { getUAETodayStr } from "../utils/datetime"

export const API_URL = import.meta.env.VITE_API_URL || (import.meta.env.DEV ? "http://127.0.0.1:8000" : "")

async function getValidAccessToken() {
    if (!supabase) return null
    try {
        const { data: { session } = {}, error } = await supabase.auth.getSession()
        if (error || !session) return null

        // If the token expires in less than 60 seconds, proactively refresh it
        const now = Math.floor(Date.now() / 1000)
        if (session.expires_at && session.expires_at - now < 60) {
            const { data: { session: refreshedSession } = {}, error: refreshError } = await supabase.auth.refreshSession()
            if (!refreshError && refreshedSession?.access_token) {
                return refreshedSession.access_token
            }
        }
        return session.access_token || null
    } catch {
        return null
    }
}

async function apiFetch(url, options = {}) {
    const isPublic = url.endsWith("/health") || url.endsWith("/api/health")
    const token = await getValidAccessToken()

    if (!token && !isPublic) {
        throw new Error("Your sign-in is invalid or expired. Please sign in again.")
    }

    const headers = new Headers(options.headers || {})
    if (token) {
        headers.set("Authorization", `Bearer ${token}`)
    }

    const response = await fetch(url, { ...options, headers })
    if (!response.ok) {
        let detail = ""
        try {
            const body = await response.clone().json()
            detail = body?.detail || body?.message || ""
        } catch {
            // The server may return a non-JSON gateway error.
        }
        const message = response.status === 401
            ? (detail || "Your sign-in is invalid or expired. Please sign in again.")
            : response.status === 403
                ? (detail || "Your user profile does not permit this request. Contact your administrator.")
            : response.status === 429
                ? (detail || "Too many requests for this resource. Please wait a moment and try again.")
            : response.status === 502
                ? "Wialon is temporarily unavailable. Fleet data could not be loaded."
                : response.status === 503
                    ? (detail || "A required backend service is unavailable or not configured.")
                    : detail || `Fleet API request failed (${response.status}).`
        throw new Error(message)
    }
    return response
}

// In-flight request sharing and the client cache live in requestCache.js (testable offline)
export { getFromClientCache, setClientCache, clearClientCache } from "./requestCache"

// How old a cached response may be and still be shown (marked as not yet refreshed) while a new request runs:
// live positions only briefly, report-based analytics longer. Never used in place of a fetch.
export const STALE_LIVE_MS = 2 * 60 * 1000
export const STALE_REPORT_MS = 10 * 60 * 1000

// UAE calendar timeframes ("today" ...) keep the same URL across midnight: their client cache key carries the UAE
// date, so a response for one day is never served (or shown stale) for the next
const CALENDAR_TIMEFRAMES = new Set(["today", "yesterday", "week", "month", "year"])
function calendarCacheKey(url, timeframe) {
    return CALENDAR_TIMEFRAMES.has(String(timeframe || "").toLowerCase()) ? `${url}#uae=${getUAETodayStr()}` : url
}

function deduplicatedFetch(url, options = {}, ttlMs = 0) {
    return dedupeRequest(url, async () => (await apiFetch(url, options)).json(), options, ttlMs)
}

export async function getVehicles(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/vehicles`, options, 8000)
}

export function peekVehicles(maxAgeMs = STALE_LIVE_MS) {
    return peekClientCache(`${API_URL}/api/vehicles`, maxAgeMs)
}

export async function getVehicleDetails(vehicleId, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/vehicles/${vehicleId}`, options, 10000)
}

export async function getDashboardSummary(hours = 24, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/dashboard/summary?hours=${hours}`, options, 30000)
}

export async function getFleetHealth(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/health`, options, 30000)
}

export async function getDashboardMileage(timeframe = "today", options = {}) {
    const params = new URLSearchParams()
    if (typeof timeframe === "string") {
        params.append("timeframe", timeframe)
    } else if (typeof timeframe === "object" && timeframe !== null) {
        options = timeframe
    }

    if (options.timeframe) params.set("timeframe", options.timeframe)
    if (options.from_time !== undefined && options.from_time !== null) params.append("from_time", options.from_time)
    if (options.to_time !== undefined && options.to_time !== null) params.append("to_time", options.to_time)
    if (options.from_date) params.append("from_date", options.from_date)
    if (options.to_date) params.append("to_date", options.to_date)
    if (options.group_id) params.append("group_id", options.group_id)

    const url = `${API_URL}/api/dashboard/mileage?${params.toString()}`
    // No client cache: the URL ("timeframe=today") does not change at UAE midnight, so a cached response could
    // answer for the previous day. Concurrent callers still share one request; the backend caches per window.
    return deduplicatedFetch(url, options, 0)
}

export async function getDashboardTrends(hours = 24, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/dashboard/trends?hours=${hours}`, options, 30000)
}

export async function getVehicleGroups(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/groups`, options, 30000)
}

export async function getDrivers() {
    const response = await apiFetch(`${API_URL}/api/drivers`)
    if (!response.ok) {
        throw new Error("Failed to fetch drivers")
    }
    return response.json()
}

export async function getVehicleTrips(vehicleId, options = {}) {
    const { hours = 24, from_time, to_time } = options
    const params = new URLSearchParams()

    if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    const queryString = params.toString()
    const url = `${API_URL}/api/vehicles/${vehicleId}/trips${queryString ? `?${queryString}` : ""}`
    return deduplicatedFetch(url, options, 30000)
}

export async function getVehicleHistory(vehicleId, options = {}) {
    const { hours = 24, from_time, to_time } = options
    const params = new URLSearchParams()

    if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    const queryString = params.toString()
    const url = `${API_URL}/api/vehicles/${vehicleId}/history${queryString ? `?${queryString}` : ""}`
    return deduplicatedFetch(url, options, 30000)
}

export async function getVehicleIdle(vehicleId, options = {}) {
    const { hours, from_time, to_time, timeframe, from_date, to_date } = options
    const params = new URLSearchParams()

    if (timeframe) {
        params.append("timeframe", timeframe)
    } else if (from_date && to_date) {
        params.append("from_date", from_date)
        params.append("to_date", to_date)
    } else if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    const queryString = params.toString()
    const url = `${API_URL}/api/vehicles/${vehicleId}/idle${queryString ? `?${queryString}` : ""}`
    return deduplicatedFetch(url, options, 30000)
}

export async function getVehicleEfficiency(vehicleId, hours = 24, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/vehicles/${vehicleId}/efficiency?hours=${hours}`, options, 30000)
}

export async function getVehicleSafety(vehicleId, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/vehicles/${vehicleId}/safety`, options, 30000)
}

const FLEET_IDLE_TTL_MS = 30000

function fleetIdleAnalyticsUrl(options = {}) {
    const { hours, from_time, to_time, timeframe, from_date, to_date, group_id, vehicle_id, include_events, skip_cache, refresh, skipCache } = options
    let url = `${API_URL}/api/idle/fleet-analytics`
    const params = new URLSearchParams()

    if (timeframe) {
        params.append("timeframe", timeframe)
    } else if (from_date && to_date) {
        params.append("from_date", from_date)
        params.append("to_date", to_date)
    } else if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    if (group_id) {
        params.append("group_id", group_id)
    }

    if (vehicle_id) {
        params.append("vehicle_id", vehicle_id)
    }

    if (include_events !== undefined && include_events !== null) {
        params.append("include_events", include_events)
    }

    const shouldBypass = Boolean(skip_cache || refresh || skipCache)
    if (shouldBypass) {
        params.append("skip_cache", "true")
        params.append("refresh", "true")
    }

    const queryString = params.toString()
    if (queryString) {
        url += `?${queryString}`
    }

    return { url, shouldBypass, cacheKey: calendarCacheKey(url, timeframe) }
}

export async function getFleetIdleAnalytics(options = {}) {
    const { url, shouldBypass, cacheKey } = fleetIdleAnalyticsUrl(options)
    return deduplicatedFetch(url, { ...options, skipCache: shouldBypass, cacheKey }, FLEET_IDLE_TTL_MS)
}

// The still-fresh client-cached response for these options, or null (never fetches)
export function getCachedFleetIdleAnalytics(options = {}) {
    const { shouldBypass, cacheKey } = fleetIdleAnalyticsUrl(options)
    return shouldBypass ? null : getFromClientCache(cacheKey, FLEET_IDLE_TTL_MS)
}

// The last response for these options (up to maxAgeMs old, possibly past its TTL), or null (never fetches)
export function peekFleetIdleAnalytics(options = {}, maxAgeMs = STALE_REPORT_MS) {
    const { shouldBypass, cacheKey } = fleetIdleAnalyticsUrl(options)
    return shouldBypass ? null : peekClientCache(cacheKey, maxAgeMs)
}

export async function sendAiChatMessage(messages) {
    const response = await apiFetch(`${API_URL}/api/ai/chat`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            messages,
        }),
    })

    if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || "Failed to communicate with Fleet AI")
    }

    return response.json()
}

// ── New Extended Fleet Analytics ─────────────────────────────────────────────

export async function getFleetHealthAnalytics(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/health`, options, 30000)
}

export async function getFleetUtilizationAnalytics(hours = 24, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/utilization?hours=${hours}`, options, 30000)
}

function fleetTripsUrl(options = {}) {
    let url = `${API_URL}/api/fleet/trips`
    const params = new URLSearchParams()

    if (typeof options === "number") {
        params.append("hours", options)
    } else {
        const { hours = 24, from_time, to_time, timeframe, from_date, to_date } = options
        if (timeframe) params.append("timeframe", timeframe)
        if (from_time !== undefined && from_time !== null) params.append("from_time", from_time)
        if (to_time !== undefined && to_time !== null) params.append("to_time", to_time)
        if (from_date) params.append("from_date", from_date)
        if (to_date) params.append("to_date", to_date)
        if (!timeframe && !from_time && hours) params.append("hours", hours)
    }

    const queryString = params.toString()
    if (queryString) url += `?${queryString}`
    return { url, cacheKey: calendarCacheKey(url, options?.timeframe) }
}

export async function getFleetTripsAnalytics(options = {}) {
    const { url, cacheKey } = fleetTripsUrl(options)
    return deduplicatedFetch(url, { ...(typeof options === "object" ? options : {}), cacheKey }, 30000)
}

export function peekFleetTripsAnalytics(options = {}, maxAgeMs = STALE_REPORT_MS) {
    return peekClientCache(fleetTripsUrl(options).cacheKey, maxAgeMs)
}

export async function getFleetSafetyAnalytics(hours = 168, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/safety?hours=${hours}`, options, 30000)
}

export async function getFleetAlertsAnalytics(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/alerts`, options, 30000)
}

export function peekFleetAlertsAnalytics(maxAgeMs = STALE_REPORT_MS) {
    return peekClientCache(`${API_URL}/api/fleet/alerts`, maxAgeMs)
}

export function peekFleetHealthAnalytics(maxAgeMs = STALE_REPORT_MS) {
    return peekClientCache(`${API_URL}/api/fleet/health`, maxAgeMs)
}

export async function getFleetGroupsAnalytics(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/groups`, options, 30000)
}

export async function getVehicleDeepAnalytics(vehicleId, hours = 24) {
    const response = await apiFetch(`${API_URL}/api/vehicles/${vehicleId}/analytics?hours=${hours}`)
    if (!response.ok) throw new Error(`Failed to fetch deep analytics for vehicle ${vehicleId}`)
    return response.json()
}

export async function getVehicleRoute(vehicleId, hours = 24) {
    const response = await apiFetch(`${API_URL}/api/vehicles/${vehicleId}/route?hours=${hours}`)
    if (!response.ok) throw new Error(`Failed to fetch route for vehicle ${vehicleId}`)
    return response.json()
}

// ─── Fuel & Fleet Performance Analytics APIs ──────────────────────────────────

function buildFuelQueryString(params = {}) {
    const q = new URLSearchParams()
    Object.entries(params).forEach(([key, val]) => {
        if (val !== undefined && val !== null && val !== "") {
            q.append(key, val)
        }
    })
    const qs = q.toString()
    return qs ? `?${qs}` : ""
}

export async function getFuelSummary(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/summary${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelTrend(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/trend${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelVehicles(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/vehicles${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelTypes(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/fuel-types${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFleetEfficiency(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/efficiency${buildFuelQueryString(params)}`, options, 30000)
}

export async function getVehicleEfficiencyList(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/efficiency/vehicles${buildFuelQueryString(params)}`, options, 30000)
}

export async function getEfficiencyTrend(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/efficiency/trend${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelExcluded(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/excluded${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelVehicleDistance(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/distance${buildFuelQueryString(params)}`, options, 30000)
}

// Optional import choices as form fields: column mapping, reporting month (statements without a date column),
// explicit confirmation of ambiguous columns, whole-month statement period, what a date shared by every row means
// ("dated" or "monthly_summary"), and the replace count the preview showed.
function appendImportOptions(formData, { mapping, reportingMonth, mappingConfirmed, extendToFullMonths, singleDateMode, expectedReplaceCount } = {}) {
    if (mapping) formData.append("mapping", typeof mapping === "string" ? mapping : JSON.stringify(mapping))
    if (reportingMonth) formData.append("reporting_month", reportingMonth)
    if (mappingConfirmed) formData.append("mapping_confirmed", "true")
    if (extendToFullMonths) formData.append("extend_to_full_months", "true")
    if (singleDateMode) formData.append("single_date_mode", singleDateMode)
    if (Number.isInteger(expectedReplaceCount)) formData.append("expected_replace_count", String(expectedReplaceCount))
}

export async function previewFuelImport(file, options = {}) {
    const formData = new FormData()
    formData.append("file", file)
    appendImportOptions(formData, options)
    const response = await apiFetch(`${API_URL}/api/fuel/import/preview`, {
        method: "POST",
        body: formData,
    })
    return response.json()
}

export async function executeFuelImport(file, mapping, options = {}) {
    const formData = new FormData()
    formData.append("file", file)
    appendImportOptions(formData, { ...options, mapping })
    const response = await apiFetch(`${API_URL}/api/fuel/import`, {
        method: "POST",
        body: formData,
    })
    const result = await response.json()
    // New rows change every fuel KPI: cached fuel responses must not be served after an import
    clearClientCache("/api/fuel/")
    return result
}

export async function getFuelImports(limit = 50, offset = 0) {
    const response = await apiFetch(`${API_URL}/api/fuel/imports?limit=${limit}&offset=${offset}`)
    return response.json()
}

// Recounts one import from its stored transactions and fixes its history record (inserts/deletes nothing)
export async function reconcileFuelImport(importId) {
    const response = await apiFetch(`${API_URL}/api/fuel/imports/${encodeURIComponent(importId)}/reconcile`, {
        method: "POST",
    })
    const result = await response.json()
    clearClientCache("/api/fuel/")
    return result
}

export async function getFuelWaste(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/waste${buildFuelQueryString(params)}`, options, 30000)
}

export async function getVehicleFuelDetail(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/vehicle-detail${buildFuelQueryString(params)}`, options, 30000)
}

// Monthly-summary totals in the KPI scope, by reporting month (never by a refuelling date)
export async function getFuelMonthlySummaries(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/monthly-summaries${buildFuelQueryString(params)}`, options, 30000)
}

