import { useState, useEffect } from "react"

/**
 * Formats a duration in seconds to a human-readable live string (e.g. "13m 24s", "1h 04m 12s", "45s").
 */
export function formatElapsedDuration(totalSeconds) {
    const s = Math.max(0, Math.floor(Number(totalSeconds || 0)))
    const hours = Math.floor(s / 3600)
    const minutes = Math.floor((s % 3600) / 60)
    const seconds = s % 60

    if (hours > 0) {
        return `${hours}h ${minutes.toString().padStart(2, "0")}m ${seconds.toString().padStart(2, "0")}s`
    }
    if (minutes > 0) {
        return `${minutes}m ${seconds.toString().padStart(2, "0")}s`
    }
    return `${seconds}s`
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
    const calculateSeconds = () => {
        if (!startTime) {
            return Number(baseDuration || 0)
        }
        const startSec = typeof startTime === "number"
            ? (startTime > 1e11 ? Math.floor(startTime / 1000) : startTime)
            : Math.floor(new Date(startTime).getTime() / 1000)

        if (isNaN(startSec) || startSec <= 0) {
            return Number(baseDuration || 0)
        }

        const nowSec = Math.floor(Date.now() / 1000)
        return Math.max(0, nowSec - startSec)
    }

    const [elapsed, setElapsed] = useState(calculateSeconds)

    useEffect(() => {
        // Initial calculation on prop change
        setElapsed(calculateSeconds())

        // Only tick automatically while the vehicle is currently idling
        if (status !== "IDLING") {
            return
        }

        const interval = setInterval(() => {
            setElapsed(calculateSeconds())
        }, 1000)

        return () => clearInterval(interval)
    }, [startTime, baseDuration, status])

    return (
        <span className={`font-mono font-semibold ${className}`}>
            {formatElapsedDuration(elapsed)}
        </span>
    )
}
