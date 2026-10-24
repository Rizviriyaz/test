// Runs requests whose results replace one another (e.g. mileage for the selected timeframe).
// Only the most recent request may report back: an older response that arrives late (an earlier
// timeframe) is dropped, so it can never overwrite the data or error of the request that replaced it.
//
// handlers:
//   onStart()      - a new request began: clear the previous result so it is never shown as this one
//   onData(data)   - the latest request succeeded
//   onError(error) - the latest request failed
//   onSettled()    - the latest request finished either way
export function createLatestRequest(fetcher, handlers) {
    let latest = 0
    return {
        async load(...args) {
            const id = ++latest
            handlers.onStart()
            try {
                const data = await fetcher(...args)
                if (id === latest) handlers.onData(data)
            } catch (err) {
                if (id === latest) handlers.onError(err)
            } finally {
                if (id === latest) handlers.onSettled()
            }
        },
        // Drop whatever is in flight (unmount, or a newer request about to start)
        cancel() {
            latest++
        },
    }
}
