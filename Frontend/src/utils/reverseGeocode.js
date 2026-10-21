import { useState, useEffect } from "react"

// Multi-tier caching and reverse geocoding utility for coordinates to human-readable location names

const STORAGE_KEY = "ats_geocode_cache_v1"
const memoryCache = new Map()
const pendingRequests = new Map()

// Nominatim's usage policy allows at most one request per second, so uncached lookups share one paced queue.
const MIN_REQUEST_INTERVAL_MS = 1100
let nextRequestAt = 0

function waitForRequestSlot() {
    const now = Date.now()
    const waitMs = Math.max(0, nextRequestAt - now)
    nextRequestAt = Math.max(now, nextRequestAt) + MIN_REQUEST_INTERVAL_MS
    return waitMs > 0 ? new Promise(resolve => setTimeout(resolve, waitMs)) : Promise.resolve()
}

// Initialize memory cache from localStorage
try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
        const parsed = JSON.parse(saved)
        Object.entries(parsed).forEach(([k, v]) => {
            if (typeof v === "string" && v) {
                memoryCache.set(k, v)
            }
        })
    }
} catch {
    // Ignore localStorage access errors
}

function persistToStorage(key, value) {
    try {
        const current = {}
        // Keep up to 200 recent entries in localStorage
        const entries = Array.from(memoryCache.entries()).slice(-200)
        entries.forEach(([k, v]) => {
            current[k] = v
        })
        current[key] = value
        localStorage.setItem(STORAGE_KEY, JSON.stringify(current))
    } catch {
        // Ignore storage quota errors
    }
}

export function getCoordKey(lat, lng) {
    if (lat == null || lng == null) return null
    const numLat = Number(lat)
    const numLng = Number(lng)
    if (isNaN(numLat) || isNaN(numLng)) return null
    return `${numLat.toFixed(4)},${numLng.toFixed(4)}`
}

export function getCachedLocationName(lat, lng) {
    const key = getCoordKey(lat, lng)
    if (!key) return null
    return memoryCache.get(key) || null
}

function formatNominatim(data) {
    if (!data) return null
    const addr = data.address || {}
    const local = addr.name || addr.road || addr.neighbourhood || addr.suburb || addr.amenity || addr.industrial || addr.commercial
    const state = (addr.state || "").replace(/\s*(Emirate|Governorate|Province|State)\b/gi, "").trim()
    const city = addr.city || addr.town || addr.municipality || addr.village || state || addr.county
    let country = addr.country || ""
    if (addr.country_code?.toUpperCase() === "AE" || country.toLowerCase() === "united arab emirates") {
        country = "UAE"
    }
    const parts = []
    for (const p of [local, city, country]) {
        if (p && !parts.includes(p)) {
            parts.push(p)
        }
    }
    return parts.length > 0 ? parts.join(", ") : data.display_name || null
}

function formatBigDataCloud(data) {
    if (!data) return null
    const local = data.localityInfo?.informative?.find(i => i.order >= 7)?.name || data.locality
    const city = data.city || data.principalSubdivision
    let country = data.countryName || ""
    if (data.countryCode?.toUpperCase() === "AE" || country.toLowerCase() === "united arab emirates") {
        country = "UAE"
    }
    const parts = []
    for (const p of [local, city, country]) {
        if (p && !parts.includes(p)) {
            parts.push(p)
        }
    }
    return parts.length > 0 ? parts.join(", ") : null
}

export async function reverseGeocode(lat, lng) {
    const key = getCoordKey(lat, lng)
    if (!key) return ""

    // 1. Check in-memory cache
    if (memoryCache.has(key)) {
        return memoryCache.get(key)
    }

    // 2. Check pending in-flight request
    if (pendingRequests.has(key)) {
        return pendingRequests.get(key)
    }

    const fallback = `${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)}`

    const fetchPromise = (async () => {
        await waitForRequestSlot()

        // Attempt 1: OpenStreetMap Nominatim with 4s timeout
        try {
            const controller = new AbortController()
            const timeoutId = setTimeout(() => controller.abort(), 4000)
            const res = await fetch(
                `https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=${lat}&lon=${lng}&accept-language=en`,
                {
                    signal: controller.signal,
                    headers: { "Accept": "application/json" }
                }
            )
            clearTimeout(timeoutId)
            if (res.ok) {
                const data = await res.json()
                const formatted = formatNominatim(data)
                if (formatted) {
                    memoryCache.set(key, formatted)
                    persistToStorage(key, formatted)
                    return formatted
                }
            }
        } catch {
            // Fall through to fallback provider
        }

        // Attempt 2: BigDataCloud Reverse Geocode Client API
        try {
            const controller = new AbortController()
            const timeoutId = setTimeout(() => controller.abort(), 4000)
            const res = await fetch(
                `https://api.bigdatacloud.net/data/reverse-geocode-client?latitude=${lat}&longitude=${lng}&localityLanguage=en`,
                { signal: controller.signal }
            )
            clearTimeout(timeoutId)
            if (res.ok) {
                const data = await res.json()
                const formatted = formatBigDataCloud(data)
                if (formatted) {
                    memoryCache.set(key, formatted)
                    persistToStorage(key, formatted)
                    return formatted
                }
            }
        } catch {
            // Fall through to fallback
        }

        // Fallback: return formatted coordinates
        return fallback
    })()

    pendingRequests.set(key, fetchPromise)

    try {
        const result = await fetchPromise
        return result
    } finally {
        pendingRequests.delete(key)
    }
}

export function LocationName({ lat, lng }) {
    const fallback = `${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)}`
    const [name, setName] = useState(() => getCachedLocationName(lat, lng) || fallback)

    useEffect(() => {
        let isMounted = true
        reverseGeocode(lat, lng).then((resolved) => {
            if (isMounted && resolved) {
                setName(resolved)
            }
        })
        return () => {
            isMounted = false
        }
    }, [lat, lng])

    return name
}
