import { useRef } from "react"
import { motion, useMotionValue, useSpring } from "framer-motion"

export function Magnetic({
    children,
    intensity = 0.35,
    range = 100,
    springOptions = { stiffness: 150, damping: 15, mass: 0.1 },
    disabled = false,
}) {
    const ref = useRef(null)
    const x = useMotionValue(0)
    const y = useMotionValue(0)
    const springX = useSpring(x, springOptions)
    const springY = useSpring(y, springOptions)

    const reset = () => {
        x.set(0)
        y.set(0)
    }

    const handleMouseMove = (event) => {
        if (disabled || !ref.current) return
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return

        const rect = ref.current.getBoundingClientRect()
        const centerX = rect.left + rect.width / 2
        const centerY = rect.top + rect.height / 2
        const distanceX = event.clientX - centerX
        const distanceY = event.clientY - centerY
        const distance = Math.hypot(distanceX, distanceY)

        if (distance < range) {
            x.set(distanceX * intensity)
            y.set(distanceY * intensity)
        } else {
            reset()
        }
    }

    return (
        <motion.div
            ref={ref}
            style={{ x: springX, y: springY }}
            onMouseMove={handleMouseMove}
            onMouseLeave={reset}
            className="inline-flex"
        >
            {children}
        </motion.div>
    )
}
