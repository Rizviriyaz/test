import { useState, useEffect } from "react"
import { formatElapsedDuration } from "../../utils/datetime"

function calculateSeconds(startTime, baseDuration, nowMs) {
    if (!startTime) {
        return Number(baseDuration || 0)
    }
    const startSec = typeof startTime === "number"
        ? (startTime > 1e11 ? Math.floor(startTime / 1000) : startTime)
        : Math.floor(new Date(startTime).getTime() / 1000)

    if (isNaN(startSec) || startSec <= 0) {
        return Number(baseDuration || 0)
    }

    const nowSec = Math.floor(nowMs / 1000)
    return Math.max(0, nowSec - startSec)
}

/**
 * Live-updating continuous idle timer.
 * Automatically updates every second while vehicle status === "IDLING".
 * Cleans up interval timers on unmount to prevent memory leaks.
 */
export default function LiveIdleTimer({
    startTime,
    baseDuration = 0,
    status = "IDLING",
    className = "",
}) {
    const [nowMs, setNowMs] = useState(() => Date.now())

    useEffect(() => {
        // Only tick automatically while the vehicle is currently idling
        if (status !== "IDLING") {
            return
        }

        const interval = setInterval(() => {
            setNowMs(Date.now())
        }, 1000)

        return () => clearInterval(interval)
    }, [status])

    const elapsed = calculateSeconds(startTime, baseDuration, nowMs)

    return (
        <span className={`font-semibold tabular-nums ${className}`}>
            {formatElapsedDuration(elapsed)}
        </span>
    )
}
