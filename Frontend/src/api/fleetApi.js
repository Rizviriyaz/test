export const API_URL = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000"

export async function getVehicles() {
    const response = await fetch(`${API_URL}/api/vehicles`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle data")
    }
    return response.json()
}

export async function getVehicleDetails(vehicleId) {
    const response = await fetch(`${API_URL}/api/vehicles/${vehicleId}`)
    if (!response.ok) {
        throw new Error(`Failed to fetch vehicle details for ID ${vehicleId}`)
    }
    return response.json()
}

export async function getDashboardSummary(hours = 24) {
    const response = await fetch(`${API_URL}/api/dashboard/summary?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard summary")
    }
    return response.json()
}

export async function getFleetHealth() {
    const response = await fetch(`${API_URL}/api/dashboard/fleet-health`)
    if (!response.ok) {
        throw new Error("Failed to fetch fleet health")
    }
    return response.json()
}

export async function getDashboardMileage(timeframe = "today") {
    const response = await fetch(`${API_URL}/api/dashboard/mileage?timeframe=${timeframe}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard mileage")
    }
    return response.json()
}

export async function getDashboardTrends(hours = 24) {
    const response = await fetch(`${API_URL}/api/dashboard/trends?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch dashboard trends")
    }
    return response.json()
}

export async function getVehicleGroups() {
    const response = await fetch(`${API_URL}/api/groups`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle groups")
    }
    return response.json()
}

export async function getDrivers() {
    const response = await fetch(`${API_URL}/api/drivers`)
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

    const response = await fetch(url)
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

    const response = await fetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle history")
    }
    return response.json()
}

export async function getVehicleIdle(vehicleId, options = {}) {
    const { hours = 24, from_time, to_time } = options
    let url = `${API_URL}/api/vehicles/${vehicleId}/idle`
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

    const response = await fetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle idle details")
    }
    return response.json()
}

export async function getVehicleEfficiency(vehicleId, hours = 24) {
    const response = await fetch(`${API_URL}/api/vehicles/${vehicleId}/efficiency?hours=${hours}`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle efficiency")
    }
    return response.json()
}

export async function getVehicleSafety(vehicleId) {
    const response = await fetch(`${API_URL}/api/vehicles/${vehicleId}/safety`)
    if (!response.ok) {
        throw new Error("Failed to fetch vehicle safety")
    }
    return response.json()
}

export async function getFleetIdleAnalytics(options = {}) {
    const { hours = 24, from_time, to_time, group_id, vehicle_id } = options
    let url = `${API_URL}/api/idle/fleet-analytics`
    const params = new URLSearchParams()

    if (from_time !== undefined && from_time !== null && to_time !== undefined && to_time !== null) {
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

    const response = await fetch(url)
    if (!response.ok) {
        throw new Error("Failed to fetch fleet idle analytics")
    }
    return response.json()
}

export async function sendAiChatMessage(messages, sessionToken) {
    const response = await fetch(`${API_URL}/api/ai/chat`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
        },
        body: JSON.stringify({
            messages,
            session_token: sessionToken || "ats_session_active",
        }),
    })

    if (!response.ok) {
        const errData = await response.json().catch(() => ({}))
        throw new Error(errData.detail || "Failed to communicate with Fleet AI")
    }

    return response.json()
}