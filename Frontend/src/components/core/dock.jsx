import {
    createContext,
    useContext,
    useRef,
    useState,
} from "react"
import {
    AnimatePresence,
    motion,
    useMotionValue,
    useSpring,
    useTransform,
} from "framer-motion"
import { cn } from "../../lib/utils"

const DockContext = createContext(null)
const DockItemContext = createContext(null)

function useDock() {
    const context = useContext(DockContext)
    if (!context) throw new Error("Dock components must be used within Dock")
    return context
}

function useDockItem() {
    const context = useContext(DockItemContext)
    if (!context) throw new Error("DockIcon and DockLabel must be used within DockItem")
    return context
}

export function Dock({
    children,
    className,
    magnification = 70,
    distance = 140,
    panelHeight = 64,
}) {
    const mouseX = useMotionValue(Infinity)

    return (
        <motion.div
            onMouseMove={(event) => mouseX.set(event.pageX)}
            onMouseLeave={() => mouseX.set(Infinity)}
            className={cn(
                "mx-auto flex w-max max-w-full items-end gap-2 rounded-2xl border border-zinc-200/80 bg-white/80 px-3 shadow-[0_8px_30px_rgb(0,0,0,0.08)] backdrop-blur-xl",
                className
            )}
            style={{ height: panelHeight }}
            role="toolbar"
            aria-label="Application navigation"
        >
            <DockContext.Provider value={{ mouseX, magnification, distance }}>
                {children}
            </DockContext.Provider>
        </motion.div>
    )
}

export function DockItem({ children, className, onClick, ...props }) {
    const ref = useRef(null)
    const { mouseX, magnification, distance } = useDock()

    const mouseDistance = useTransform(mouseX, (val) => {
        const rect = ref.current?.getBoundingClientRect() ?? { x: 0, width: 0 }
        return val - rect.x - rect.width / 2
    })

    const widthTransform = useTransform(
        mouseDistance,
        [-distance, 0, distance],
        [40, magnification, 40]
    )
    const heightTransform = useTransform(
        mouseDistance,
        [-distance, 0, distance],
        [40, magnification, 40]
    )

    const width = useSpring(widthTransform, { mass: 0.1, stiffness: 150, damping: 12 })
    const height = useSpring(heightTransform, { mass: 0.1, stiffness: 150, damping: 12 })

    const [hovered, setHovered] = useState(false)

    return (
        <motion.button
            type="button"
            ref={ref}
            style={{ width, height }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onFocus={() => setHovered(true)}
            onBlur={() => setHovered(false)}
            onClick={onClick}
            className={cn(
                "relative flex aspect-square cursor-pointer items-center justify-center rounded-full",
                className
            )}
            {...props}
        >
            <DockItemContext.Provider value={{ hovered, width, height }}>
                {children}
            </DockItemContext.Provider>
        </motion.button>
    )
}

export function DockLabel({ children, className }) {
    const { hovered } = useDockItem()

    return (
        <AnimatePresence>
            {hovered && (
                <motion.div
                    initial={{ opacity: 0, y: 8, x: "-50%" }}
                    animate={{ opacity: 1, y: -8, x: "-50%" }}
                    exit={{ opacity: 0, y: 4, x: "-50%" }}
                    transition={{ duration: 0.15 }}
                    className={cn(
                        "absolute -top-7 left-1/2 w-fit whitespace-pre rounded-md border border-zinc-200 bg-white px-2 py-0.5 text-xs font-medium text-zinc-700 shadow-sm",
                        className
                    )}
                    role="tooltip"
                >
                    {children}
                </motion.div>
            )}
        </AnimatePresence>
    )
}

export function DockIcon({ children, className }) {
    const { width } = useDockItem()
    const size = useTransform(width, (val) => val / 2)

    return (
        <motion.div
            style={{ width: size, height: size }}
            className={cn("flex items-center justify-center", className)}
        >
            {children}
        </motion.div>
    )
}
