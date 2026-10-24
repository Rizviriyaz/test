// Offline regression tests for the client cache / in-flight sharing used by fleetApi.js, in particular the
// sign-out race. Run: npm test (node --test).
import { test, beforeEach } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { clearClientCache, deduplicatedFetch, getFromClientCache, setClientCache } from "./requestCache.js"

function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

beforeEach(() => clearClientCache())

test("a response cached within its TTL is served without a new request", async () => {
    let calls = 0
    const fetchJson = async () => { calls += 1; return { user: "A" } }
    assert.deepEqual(await deduplicatedFetch("/api/vehicles", fetchJson, {}, 10000), { user: "A" })
    assert.deepEqual(await deduplicatedFetch("/api/vehicles", fetchJson, {}, 10000), { user: "A" })
    assert.equal(calls, 1)
})

test("concurrent callers share one in-flight request", async () => {
    const pending = deferred()
    let calls = 0
    const fetchJson = () => { calls += 1; return pending.promise }
    const first = deduplicatedFetch("/api/x", fetchJson, {}, 10000)
    const second = deduplicatedFetch("/api/x", fetchJson, {}, 10000)
    pending.resolve({ ok: 1 })
    assert.deepEqual(await Promise.all([first, second]), [{ ok: 1 }, { ok: 1 }])
    assert.equal(calls, 1)
})

test("sign-out race: a request in flight at clearClientCache() does not repopulate the cache", async () => {
    const userA = deferred()
    const requestA = deduplicatedFetch("/api/vehicles", () => userA.promise, {}, 30000)

    clearClientCache() // sign-out
    userA.resolve({ owner: "user A" })
    assert.deepEqual(await requestA, { owner: "user A" }, "the original caller still gets its own response")

    assert.equal(getFromClientCache("/api/vehicles", 30000), null, "user A's data must not be cached after sign-out")

    let userBCalls = 0
    const data = await deduplicatedFetch("/api/vehicles", async () => { userBCalls += 1; return { owner: "user B" } }, {}, 30000)
    assert.deepEqual(data, { owner: "user B" })
    assert.equal(userBCalls, 1)
})

test("sign-out race: a caller starting after clearClientCache() does not join the old in-flight request", async () => {
    const userA = deferred()
    const requestA = deduplicatedFetch("/api/vehicles", () => userA.promise, {}, 30000)
    clearClientCache()

    const userB = deferred()
    const requestB = deduplicatedFetch("/api/vehicles", () => userB.promise, {}, 30000)
    assert.notEqual(requestB, requestA)

    // The old request finishing first must not drop the new request's in-flight entry
    userA.resolve({ owner: "user A" })
    await requestA
    let joinedCalls = 0
    const joined = deduplicatedFetch("/api/vehicles", async () => { joinedCalls += 1; return { owner: "late" } }, {}, 30000)

    userB.resolve({ owner: "user B" })
    assert.deepEqual(await requestB, { owner: "user B" })
    assert.deepEqual(await joined, { owner: "user B" }, "a caller after user A finished still shares user B's request")
    assert.equal(joinedCalls, 0)
    assert.deepEqual(getFromClientCache("/api/vehicles", 30000), { owner: "user B" })
})

test("a prefix clear drops matching cache and in-flight entries only", async () => {
    setClientCache("/api/fuel/analytics/summary", { stale: true })
    setClientCache("/api/vehicles", { keep: true })
    const fuel = deferred()
    const fuelRequest = deduplicatedFetch("/api/fuel/analytics/trend", () => fuel.promise, {}, 30000)

    clearClientCache("/api/fuel/")
    assert.equal(getFromClientCache("/api/fuel/analytics/summary", 30000), null)
    assert.deepEqual(getFromClientCache("/api/vehicles", 30000), { keep: true })

    fuel.resolve({ preImport: true })
    await fuelRequest
    assert.equal(getFromClientCache("/api/fuel/analytics/trend", 30000), null, "pre-import fuel data is not cached")
})

test("a failed request is not cached and frees its in-flight slot", async () => {
    await assert.rejects(deduplicatedFetch("/api/y", async () => { throw new Error("502") }, {}, 10000), /502/)
    assert.equal(getFromClientCache("/api/y", 10000), null)
    assert.deepEqual(await deduplicatedFetch("/api/y", async () => ({ ok: true }), {}, 10000), { ok: true })
})

test("skipCache bypasses a cached response", async () => {
    setClientCache("/api/z", { old: true })
    assert.deepEqual(await deduplicatedFetch("/api/z", async () => ({ fresh: true }), { skipCache: true }, 10000), { fresh: true })
})

// Dashboard mileage: the URL ("timeframe=today" / "timeframe=yesterday") is the same on both sides of UAE midnight
// (20:00 UTC), so a client-cached response would answer for the previous calendar day.
const MILEAGE_URL = { today: "/api/dashboard/mileage?timeframe=today", yesterday: "/api/dashboard/mileage?timeframe=yesterday" }
// The client TTL getDashboardMileage passes (fleetApi.js cannot be imported offline: it loads Supabase and import.meta.env)
function dashboardMileageClientTtl() {
    const source = readFileSync(new URL("./fleetApi.js", import.meta.url), "utf8")
    const body = source.slice(source.indexOf("export async function getDashboardMileage"))
    const match = body.match(/return deduplicatedFetch\(url, options, (\d+)\)/)
    assert.ok(match, "getDashboardMileage must call deduplicatedFetch(url, options, <ttl>)")
    return Number(match[1])
}

for (const timeframe of ["today", "yesterday"]) {
    test(`dashboard mileage (${timeframe}): a response cached before UAE midnight is not served after it`, async (t) => {
        const ttl = dashboardMileageClientTtl()
        const beforeMidnight = Date.UTC(2026, 9, 24, 19, 59, 55) // 23:59:55 UAE, Oct 24
        const afterMidnight = Date.UTC(2026, 9, 24, 20, 0, 5)    // 00:00:05 UAE, Oct 25 (10 s later, inside the old 30 s TTL)
        let now = beforeMidnight
        t.mock.method(Date, "now", () => now)

        let calls = 0
        const fetchJson = async () => { calls += 1; return { day: now < afterMidnight ? "2026-10-24" : "2026-10-25" } }
        assert.deepEqual(await deduplicatedFetch(MILEAGE_URL[timeframe], fetchJson, {}, ttl), { day: "2026-10-24" })
        now = afterMidnight
        assert.deepEqual(await deduplicatedFetch(MILEAGE_URL[timeframe], fetchJson, {}, ttl), { day: "2026-10-25" },
            "the previous UAE day's response must not be served")
        assert.equal(calls, 2)
    })
}

test("dashboard mileage: concurrent callers still share one in-flight request without a client cache", async () => {
    const ttl = dashboardMileageClientTtl()
    const pending = deferred()
    let calls = 0
    const fetchJson = () => { calls += 1; return pending.promise }
    const first = deduplicatedFetch(MILEAGE_URL.today, fetchJson, {}, ttl)
    const second = deduplicatedFetch(MILEAGE_URL.today, fetchJson, {}, ttl)
    pending.resolve({ total_km: 10 })
    assert.deepEqual(await Promise.all([first, second]), [{ total_km: 10 }, { total_km: 10 }])
    assert.equal(calls, 1)
    assert.equal(getFromClientCache(MILEAGE_URL.today, 30000), null, "nothing is kept in the client cache")
})
