// Offline regression tests: a failed or superseded mileage request never leaves stale data on screen.
// Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { createLatestRequest } from "./latestRequest.js"

function deferred() {
    let resolve, reject
    const promise = new Promise((res, rej) => { resolve = res; reject = rej })
    return { promise, resolve, reject }
}

// Mirrors the Dashboard mileage state: data, error and loading
function mileageState(fetcher) {
    const state = { data: { timeframe: "today" }, error: null, loading: false, log: [] }
    const loader = createLatestRequest(fetcher, {
        onStart: () => { state.data = null; state.error = null; state.loading = true; state.log.push("start") },
        onData: (data) => { state.data = data; state.log.push("data") },
        onError: (err) => { state.data = null; state.error = err.message; state.log.push("error") },
        onSettled: () => { state.loading = false; state.log.push("settled") },
    })
    return { state, loader }
}

test("starting a timeframe clears the previous timeframe's data before the response arrives", async () => {
    const pending = deferred()
    const { state, loader } = mileageState(() => pending.promise)
    const done = loader.load("yesterday")
    assert.equal(state.data, null, "today's data must not be shown under 'yesterday' while loading")
    assert.equal(state.loading, true)
    pending.resolve({ timeframe: "yesterday" })
    await done
    assert.deepEqual(state.data, { timeframe: "yesterday" })
    assert.equal(state.loading, false)
})

test("a failed timeframe fetch leaves no data and exposes the error", async () => {
    const { state, loader } = mileageState(async () => { throw new Error("Wialon upstream request failed") })
    await loader.load("yesterday")
    assert.equal(state.data, null, "a failure must not leave the previous timeframe's mileage on screen")
    assert.equal(state.error, "Wialon upstream request failed")
    assert.equal(state.loading, false)
})

test("a new request clears the previous request's error", async () => {
    let fail = true
    const { state, loader } = mileageState(async (tf) => {
        if (fail) throw new Error("boom")
        return { timeframe: tf }
    })
    await loader.load("week")
    assert.equal(state.error, "boom")
    fail = false
    await loader.load("month")
    assert.equal(state.error, null)
    assert.deepEqual(state.data, { timeframe: "month" })
})

test("a slow response for an earlier timeframe never overwrites the newer timeframe", async () => {
    const slow = deferred()
    const fast = deferred()
    const responses = { today: slow.promise, yesterday: fast.promise }
    const { state, loader } = mileageState((tf) => responses[tf])
    const first = loader.load("today")
    const second = loader.load("yesterday")
    fast.resolve({ timeframe: "yesterday" })
    await second
    slow.resolve({ timeframe: "today" })
    await first
    assert.deepEqual(state.data, { timeframe: "yesterday" })
    assert.equal(state.loading, false)
})

test("a late failure for an earlier timeframe does not replace newer data with an error", async () => {
    const slow = deferred()
    const { state, loader } = mileageState((tf) => (tf === "today" ? slow.promise : Promise.resolve({ timeframe: tf })))
    const first = loader.load("today")
    await loader.load("week")
    slow.reject(new Error("late failure"))
    await first
    assert.deepEqual(state.data, { timeframe: "week" })
    assert.equal(state.error, null)
})

test("cancel drops an in-flight response (unmount)", async () => {
    const pending = deferred()
    const { state, loader } = mileageState(() => pending.promise)
    const done = loader.load("today")
    loader.cancel()
    pending.resolve({ timeframe: "today" })
    await done
    assert.equal(state.data, null)
    assert.deepEqual(state.log, ["start"])
})
