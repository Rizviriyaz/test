// Universal in-flight promise sharing and client-side memory cache used by fleetApi.js.
// Kept free of browser/Supabase imports so it can be tested offline with `node --test`.

const inFlightRequests = new Map()
// Insertion-ordered: the least recently used entry is evicted beyond MAX_CLIENT_CACHE_ENTRIES (keys include
// user-chosen filters and date ranges, so an unbounded map would grow for the whole session)
const clientCache = new Map()
export const MAX_CLIENT_CACHE_ENTRIES = 200
// Bumped by every clearClientCache(): a request started before a clear (e.g. before sign-out) must not
// write its response back into the cache or be shared with callers that start after it.
let cacheGeneration = 0

export function getFromClientCache(key, ttlMs = 10000) {
    const entry = clientCache.get(key)
    if (entry && (Date.now() - entry.timestamp) < ttlMs) {
        clientCache.delete(key)
        clientCache.set(key, entry)
        return entry.data
    }
    return null
}

export function setClientCache(key, data) {
    clientCache.delete(key)
    clientCache.set(key, { data, timestamp: Date.now() })
    while (clientCache.size > MAX_CLIENT_CACHE_ENTRIES) {
        clientCache.delete(clientCache.keys().next().value)
    }
}

// The cached entry for key if it is younger than maxAgeMs, whether or not its fetch TTL has passed, as
// { data, fetchedAt } (epoch ms); else null. Pages use it to show the last response, marked as not yet refreshed,
// while a new request runs. Never fetches.
export function peekClientCache(key, maxAgeMs) {
    const entry = clientCache.get(key)
    if (!entry || (Date.now() - entry.timestamp) >= maxAgeMs) return null
    return { data: entry.data, fetchedAt: entry.timestamp }
}

export function clientCacheSize() {
    return clientCache.size
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
// `options.cacheKey` (default: the URL) keys the cache and in-flight sharing, e.g. a URL tagged with its UAE date.
export async function deduplicatedFetch(url, fetchJson, options = {}, ttlMs = 0) {
    const key = options.cacheKey || url
    if (ttlMs > 0 && (!options.method || options.method === "GET") && !options.skipCache) {
        const cachedData = getFromClientCache(key, ttlMs)
        if (cachedData !== null) {
            return cachedData
        }
    }

    if (inFlightRequests.has(key)) {
        return inFlightRequests.get(key)
    }

    const generation = cacheGeneration
    const promise = (async () => {
        try {
            const data = await fetchJson()
            if (ttlMs > 0 && generation === cacheGeneration) {
                setClientCache(key, data)
            }
            return data
        } finally {
            // A clear may have dropped this entry and a newer request may now own the key
            if (inFlightRequests.get(key) === promise) {
                inFlightRequests.delete(key)
            }
        }
    })()

    inFlightRequests.set(key, promise)
    return promise
}
