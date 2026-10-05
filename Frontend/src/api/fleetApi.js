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

export async function getVehicles() {
    const response = await apiFetch(`${API_URL}/api/vehicles`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle data")
    }
    return response.json()
}

export async function getVehicleDetails(vehicleId) {
    const response = await apiFetch(`${API_URL}/api/vehicles/${vehicleId}`)
    if (!response.ok) {
        throw new Error(`Failed to fetch vehicle details for ID ${vehicleId}`)
    }
    return response.json()
}

export async function getDashboardSummary(hours = 24) {
    const response = await apiFetch(`${API_URL}/api/dashboard/summary?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard summary")
    }
    return response.json()
}

export async function getFleetHealth() {
    const response = await apiFetch(`${API_URL}/api/fleet/health`)
    if (!response.ok) {
        throw new Error("Failed to fetch fleet health")
    }
    return response.json()
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

    const response = await apiFetch(`${API_URL}/api/dashboard/mileage?${params.toString()}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard mileage")
    }
    return response.json()
}

export async function getDashboardTrends(hours = 24) {
    const response = await apiFetch(`${API_URL}/api/dashboard/trends?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard trends")
    }
    return response.json()
}

export async function getVehicleGroups() {
    const response = await apiFetch(`${API_URL}/api/groups`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle groups")
    }
    return response.json()
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
    let url = `${API_URL}/api/vehicles/${vehicleId}/trips`
    const params = new URLSearchParams()

    if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    const queryString = params.toString()
    if (queryString) {
        url += `?${queryString}`
    }

    const response = await apiFetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle trips")
    }
    return response.json()
}

export async function getVehicleHistory(vehicleId, options = {}) {
    const { hours = 24, from_time, to_time } = options
    let url = `${API_URL}/api/vehicles/${vehicleId}/history`
    const params = new URLSearchParams()

    if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
        params.append("from_time", from_time)
        params.append("to_time", to_time)
    } else if (hours) {
        params.append("hours", hours)
    }

    const queryString = params.toString()
    if (queryString) {
        url += `?${queryString}`
    }

    const response = await apiFetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle history")
    }
    return response.json()
}

export async function getVehicleIdle(vehicleId, options = {}) {
    const { hours, from_time, to_time, timeframe, from_date, to_date } = options
    let url = `${API_URL}/api/vehicles/${vehicleId}/idle`
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
    if (queryString) {
        url += `?${queryString}`
    }

    const response = await apiFetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle idle details")
    }
    return response.json()
}

export async function getVehicleEfficiency(vehicleId, hours = 24) {
    const response = await apiFetch(`${API_URL}/api/vehicles/${vehicleId}/efficiency?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle efficiency")
    }
    return response.json()
}

export async function getVehicleSafety(vehicleId) {
    const response = await apiFetch(`${API_URL}/api/vehicles/${vehicleId}/safety`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle safety")
    }
    return response.json()
}

export async function getFleetIdleAnalytics(options = {}) {
    const { hours, from_time, to_time, timeframe, from_date, to_date, group_id, vehicle_id } = options
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

    const queryString = params.toString()
    if (queryString) {
        url += `?${queryString}`
    }

    const response = await apiFetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch fleet idle analytics")
    }
    return response.json()
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

export async function getFleetHealthAnalytics() {
    const response = await apiFetch(`${API_URL}/api/fleet/health`)
    if (!response.ok) throw new Error("Failed to fetch fleet health analytics")
    return response.json()
}

export async function getFleetUtilizationAnalytics(hours = 24) {
    const response = await apiFetch(`${API_URL}/api/fleet/utilization?hours=${hours}`)
    if (!response.ok) throw new Error("Failed to fetch fleet utilization analytics")
    return response.json()
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

    const response = await apiFetch(url)
    if (!response.ok) throw new Error("Failed to fetch fleet trips analytics")
    return response.json()
}

export async function getFleetSafetyAnalytics(hours = 168) {
    const response = await apiFetch(`${API_URL}/api/fleet/safety?hours=${hours}`)
    if (!response.ok) throw new Error("Failed to fetch fleet safety analytics")
    return response.json()
}

export async function getFleetAlertsAnalytics() {
    const response = await apiFetch(`${API_URL}/api/fleet/alerts`)
    if (!response.ok) throw new Error("Failed to fetch fleet alerts")
    return response.json()
}

export async function getFleetGroupsAnalytics() {
    const response = await apiFetch(`${API_URL}/api/fleet/groups`)
    if (!response.ok) throw new Error("Failed to fetch fleet groups analytics")
    return response.json()
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
