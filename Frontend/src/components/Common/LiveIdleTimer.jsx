import { useState, useEffect } from "react"
import { formatElapsedDuration } from "../../utils/datetime"
import { idleElapsedSeconds } from "../../utils/fleetRules"

/**
 * Live-updating continuous idle timer.
 * Automatically updates every second while vehicle status === "IDLING", capped at `endTime` (the last message
 * time, epoch seconds) when given.
 * Cleans up interval timers on unmount to prevent memory leaks.
 */
export default function LiveIdleTimer({
    startTime,
    baseDuration = 0,
    status = "IDLING",
    endTime = null,
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

    const elapsed = idleElapsedSeconds(startTime, baseDuration, nowMs, endTime)

    return (
        <span className={`font-semibold tabular-nums ${className}`}>
            {formatElapsedDuration(elapsed)}
        </span>
    )
}
