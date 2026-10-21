// Universal in-flight promise sharing and client-side memory cache used by fleetApi.js.
// Kept free of browser/Supabase imports so it can be tested offline with `node --test`.

const inFlightRequests = new Map()
const clientCache = new Map()
// Bumped by every clearClientCache(): a request started before a clear (e.g. before sign-out) must not
// write its response back into the cache or be shared with callers that start after it.
let cacheGeneration = 0

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
    cacheGeneration += 1
    if (!keyPrefix) {
        clientCache.clear()
        inFlightRequests.clear()
        return
    }
    for (const key of clientCache.keys()) {
        if (key.includes(keyPrefix)) {
            clientCache.delete(key)
        }
    }
    for (const key of inFlightRequests.keys()) {
        if (key.includes(keyPrefix)) {
            inFlightRequests.delete(key)
        }
    }
}

// `fetchJson()` performs the request and resolves to the parsed body (fleetApi passes apiFetch + json()).
export async function deduplicatedFetch(url, fetchJson, options = {}, ttlMs = 0) {
    if (ttlMs > 0 && (!options.method || options.method === "GET") && !options.skipCache) {
        const cachedData = getFromClientCache(url, ttlMs)
        if (cachedData !== null) {
            return cachedData
        }
    }

    if (inFlightRequests.has(url)) {
        return inFlightRequests.get(url)
    }

    const generation = cacheGeneration
    const promise = (async () => {
        try {
            const data = await fetchJson()
            if (ttlMs > 0 && generation === cacheGeneration) {
                setClientCache(url, data)
            }
            return data
        } finally {
            // A clear may have dropped this entry and a newer request may now own the key
            if (inFlightRequests.get(url) === promise) {
                inFlightRequests.delete(url)
            }
        }
    })()

    inFlightRequests.set(url, promise)
    return promise
}
