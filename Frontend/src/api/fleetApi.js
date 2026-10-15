import { supabase } from "../lib/supabase"

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
            : response.status === 502
                ? "Wialon is temporarily unavailable. Fleet data could not be loaded."
                : response.status === 503
                    ? (detail || "A required backend service is unavailable or not configured.")
                    : detail || `Fleet API request failed (${response.status}).`
        throw new Error(message)
    }
    return response
}

// Universal in-flight promise sharing and client-side memory cache
const inFlightRequests = new Map()
const clientCache = new Map()

export function getFromClientCache(key, ttlMs = 10000) {
    const entry = clientCache.get(key)
    if (entry && (Date.now() - entry.timestamp) < ttlMs) {
        return entry.data
    }
    return null
}

export function setClientCache(key, data) {
    clientCache.set(key, { data, timestamp: Date.now() })
}

export function clearClientCache(keyPrefix = "") {
    if (!keyPrefix) {
        clientCache.clear()
        return
    }
    for (const key of clientCache.keys()) {
        if (key.includes(keyPrefix)) {
            clientCache.delete(key)
        }
    }
}

async function deduplicatedFetch(url, options = {}, ttlMs = 0) {
    if (ttlMs > 0 && (!options.method || options.method === "GET") && !options.skipCache) {
        const cachedData = getFromClientCache(url, ttlMs)
        if (cachedData !== null) {
            return cachedData
        }
    }

    if (inFlightRequests.has(url)) {
        return inFlightRequests.get(url)
    }

    const promise = (async () => {
        try {
            const response = await apiFetch(url, options)
            const data = await response.json()
            if (ttlMs > 0) {
                setClientCache(url, data)
            }
            return data
        } finally {
            inFlightRequests.delete(url)
        }
    })()

    inFlightRequests.set(url, promise)
    return promise
}

export async function getVehicles(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/vehicles`, options, 8000)
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
    return deduplicatedFetch(url, options, 30000)
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

export async function getFleetIdleAnalytics(options = {}) {
    const { hours, from_time, to_time, timeframe, from_date, to_date, group_id, vehicle_id, include_events } = options
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

    const queryString = params.toString()
    if (queryString) {
        url += `?${queryString}`
    }

    return deduplicatedFetch(url, options, 30000)
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

export async function getFleetTripsAnalytics(options = {}) {
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

    return deduplicatedFetch(url, options, 30000)
}

export async function getFleetSafetyAnalytics(hours = 168, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/safety?hours=${hours}`, options, 30000)
}

export async function getFleetAlertsAnalytics(options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fleet/alerts`, options, 30000)
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

export async function getFuelVehicleDistance(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/distance${buildFuelQueryString(params)}`, options, 30000)
}

export async function previewFuelImport(file) {
    const formData = new FormData()
    formData.append("file", file)
    const response = await apiFetch(`${API_URL}/api/fuel/import/preview`, {
        method: "POST",
        body: formData,
    })
    return response.json()
}

export async function executeFuelImport(file, mapping) {
    const formData = new FormData()
    formData.append("file", file)
    formData.append("mapping", typeof mapping === "string" ? mapping : JSON.stringify(mapping))
    const response = await apiFetch(`${API_URL}/api/fuel/import`, {
        method: "POST",
        body: formData,
    })
    return response.json()
}

export async function getFuelImports(limit = 50, offset = 0) {
    const response = await apiFetch(`${API_URL}/api/fuel/imports?limit=${limit}&offset=${offset}`)
    return response.json()
}

export async function getFuelWaste(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/waste${buildFuelQueryString(params)}`, options, 30000)
}

export async function getVehicleFuelDetail(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/analytics/vehicle-detail${buildFuelQueryString(params)}`, options, 30000)
}

export async function getFuelTransactions(params = {}, options = {}) {
    return deduplicatedFetch(`${API_URL}/api/fuel/transactions${buildFuelQueryString(params)}`, options, 30000)
}

