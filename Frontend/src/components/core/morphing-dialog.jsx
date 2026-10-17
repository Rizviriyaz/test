import React, {
    useCallback,
    useContext,
    useEffect,
    useId,
    useMemo,
    useRef,
    useState,
} from "react"
import { motion, AnimatePresence, MotionConfig } from "framer-motion"
import { createPortal } from "react-dom"
import { cn } from "../../lib/utils"
import { X as XIcon } from "lucide-react"

const MorphingDialogContext = React.createContext(null)

export function useMorphingDialog() {
    const context = useContext(MorphingDialogContext)
    if (!context) {
        throw new Error(
            "useMorphingDialog must be used within a MorphingDialogProvider"
        )
    }
    return context
}

export function useClickOutside(ref, handler) {
    useEffect(() => {
        const handleClickOutside = (event) => {
            if (!ref || !ref.current || ref.current.contains(event.target)) {
                return
            }
            handler(event)
        }

        document.addEventListener("mousedown", handleClickOutside)
        document.addEventListener("touchstart", handleClickOutside)

        return () => {
            document.removeEventListener("mousedown", handleClickOutside)
            document.removeEventListener("touchstart", handleClickOutside)
        }
    }, [ref, handler])
}

const defaultTransition = {
    type: "spring",
    stiffness: 200,
    damping: 24,
}

export function MorphingDialog({
    children,
    transition = defaultTransition,
    layoutId: propLayoutId,
    isOpen: controlledIsOpen,
    open: controlledOpen,
    onOpenChange,
    onClose,
}) {
    const isControlled = controlledIsOpen !== undefined || controlledOpen !== undefined
    const effectiveOpenProp = controlledIsOpen !== undefined ? controlledIsOpen : controlledOpen

    const [uncontrolledIsOpen, setUncontrolledIsOpen] = useState(false)
    const isOpen = isControlled ? Boolean(effectiveOpenProp) : uncontrolledIsOpen

    const reactId = useId()
    const uniqueId = propLayoutId || reactId
    const triggerRef = useRef(null)

    const shouldReduceMotion = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const effectiveTransition = shouldReduceMotion ? { duration: 0.05 } : transition

    const setIsOpen = useCallback(
        (value) => {
            const nextVal = typeof value === "function" ? value(isOpen) : value
            if (!isControlled) {
                setUncontrolledIsOpen(nextVal)
            }
            onOpenChange?.(nextVal)
            if (!nextVal) {
                onClose?.()
            }
        },
        [isControlled, isOpen, onOpenChange, onClose]
    )

    const contextValue = useMemo(
        () => ({
            isOpen,
            setIsOpen,
            uniqueId,
            triggerRef,
            transition: effectiveTransition,
            onClose,
        }),
        [isOpen, setIsOpen, uniqueId, effectiveTransition, onClose]
    )

    return (
        <MorphingDialogContext.Provider value={contextValue}>
            <MotionConfig transition={effectiveTransition}>{children}</MotionConfig>
        </MorphingDialogContext.Provider>
    )
}

export function MorphingDialogTrigger({
    children,
    className,
    style,
    layoutId: customLayoutId,
    triggerRef: externalRef,
    ...props
}) {
    const context = useContext(MorphingDialogContext)
    const uniqueId = customLayoutId || context?.uniqueId || ""
    const setIsOpen = context?.setIsOpen
    const isOpen = context?.isOpen

    const handleClick = useCallback(
        (e) => {
            props.onClick?.(e)
            if (setIsOpen) {
                setIsOpen(!isOpen)
            }
        },
        [isOpen, setIsOpen, props]
    )

    const handleKeyDown = useCallback(
        (event) => {
            props.onKeyDown?.(event)
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                if (setIsOpen) {
                    setIsOpen(!isOpen)
                }
            }
        },
        [isOpen, setIsOpen, props]
    )

    return (
        <motion.div
            ref={(node) => {
                if (context?.triggerRef) context.triggerRef.current = node
                if (typeof externalRef === "function") externalRef(node)
                else if (externalRef) externalRef.current = node
            }}
            layoutId={uniqueId ? `dialog-${uniqueId}` : undefined}
            className={cn("relative cursor-pointer select-none", className)}
            onClick={handleClick}
            onKeyDown={handleKeyDown}
            style={style}
            role="button"
            tabIndex={0}
            aria-haspopup="dialog"
            aria-expanded={isOpen}
            aria-controls={uniqueId ? `motion-ui-morphing-dialog-content-${uniqueId}` : undefined}
            {...props}
        >
            {children}
        </motion.div>
    )
}

export function MorphingDialogContainer({ children, className, style, onExitComplete }) {
    const { isOpen, uniqueId, setIsOpen, onClose } = useMorphingDialog()
    const [mounted, setMounted] = useState(false)

    useEffect(() => {
        setMounted(true)
        return () => setMounted(false)
    }, [])

    const handleExitComplete = useCallback(() => {
        onExitComplete?.()
        onClose?.()
    }, [onExitComplete, onClose])

    if (!mounted || typeof document === "undefined") return null

    return createPortal(
        <AnimatePresence initial={false} mode="sync" onExitComplete={handleExitComplete}>
            {isOpen && (
                <>
                    {/* Backdrop sits above Leaflet controls (1000) and captures all background clicks */}
                    <motion.div
                        key={`backdrop-${uniqueId}`}
                        className="fixed inset-0 z-[9998] h-full w-full bg-slate-900/40 backdrop-blur-xs pointer-events-auto"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        transition={{ duration: 0.2 }}
                        onClick={() => setIsOpen(false)}
                    />
                    {/* Dialog container centered at z-[9999] */}
                    <div className="fixed inset-0 z-[9999] flex items-center justify-center p-3 sm:p-5 pointer-events-none">
                        {children}
                    </div>
                </>
            )}
        </AnimatePresence>,
        document.body
    )
}

export function MorphingDialogContent({
    children,
    className,
    style,
    layoutId: customLayoutId,
    ...props
}) {
    const { setIsOpen, isOpen, uniqueId, triggerRef, transition } = useMorphingDialog()
    const containerRef = useRef(null)
    const activeLayoutId = customLayoutId || uniqueId

    useEffect(() => {
        if (!isOpen) return
        const handleKeyDown = (event) => {
            if (event.key === "Escape") {
                event.stopPropagation()
                setIsOpen(false)
            }
        }
        document.addEventListener("keydown", handleKeyDown)
        return () => document.removeEventListener("keydown", handleKeyDown)
    }, [isOpen, setIsOpen])

    useEffect(() => {
        if (isOpen) {
            const originalOverflow = document.body.style.overflow
            document.body.style.overflow = "hidden"
            return () => {
                document.body.style.overflow = originalOverflow
            }
        } else {
            triggerRef.current?.focus?.()
        }
    }, [isOpen, triggerRef])

    useClickOutside(containerRef, () => {
        if (isOpen) {
            setIsOpen(false)
        }
    })

    return (
        <motion.div
            ref={containerRef}
            layoutId={activeLayoutId ? `dialog-${activeLayoutId}` : undefined}
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96 }}
            transition={transition}
            className={cn(
                "pointer-events-auto relative flex flex-col w-full max-w-[480px] max-h-[85vh] overflow-hidden rounded-2xl bg-white border border-slate-200 shadow-2xl",
                className
            )}
            style={style}
            role="dialog"
            aria-modal="true"
            aria-labelledby={`motion-ui-morphing-dialog-title-${activeLayoutId}`}
            aria-describedby={`motion-ui-morphing-dialog-description-${activeLayoutId}`}
            {...props}
        >
            {children}
        </motion.div>
    )
}

export function MorphingDialogTitle({ children, className, style, ...props }) {
    const { uniqueId } = useMorphingDialog()

    return (
        <motion.div
            id={`motion-ui-morphing-dialog-title-${uniqueId}`}
            layoutId={`dialog-title-container-${uniqueId}`}
            className={className}
            style={style}
            layout
            {...props}
        >
            {children}
        </motion.div>
    )
}

export function MorphingDialogSubtitle({
    children,
    className,
    style,
    ...props
}) {
    const { uniqueId } = useMorphingDialog()

    return (
        <motion.div
            layoutId={`dialog-subtitle-container-${uniqueId}`}
            className={className}
            style={style}
            {...props}
        >
            {children}
        </motion.div>
    )
}

export function MorphingDialogImage({
    src,
    alt = "",
    className,
    style,
    ...props
}) {
    const { uniqueId } = useMorphingDialog()

    return (
        <motion.img
            src={src}
            alt={alt}
            className={cn(className)}
            layoutId={`dialog-img-${uniqueId}`}
            style={style}
            {...props}
        />
    )
}

export function MorphingDialogDescription({
    children,
    className,
    variants,
    disableLayoutAnimation,
    ...props
}) {
    const { uniqueId } = useMorphingDialog()

    return (
        <motion.div
            id={`motion-ui-morphing-dialog-description-${uniqueId}`}
            key={`dialog-description-${uniqueId}`}
            layoutId={
                disableLayoutAnimation
                    ? undefined
                    : `dialog-description-content-${uniqueId}`
            }
            variants={variants}
            className={className}
            initial="initial"
            animate="animate"
            exit="exit"
            {...props}
        >
            {children}
        </motion.div>
    )
}

export function MorphingDialogClose({
    children,
    className,
    variants,
    onClick,
    ...props
}) {
    const { setIsOpen, uniqueId } = useMorphingDialog()

    const handleClose = useCallback(
        (e) => {
            onClick?.(e)
            setIsOpen(false)
        },
        [setIsOpen, onClick]
    )

    return (
        <motion.button
            onClick={handleClose}
            type="button"
            aria-label="Close dialog"
            key={`dialog-close-${uniqueId}`}
            className={cn(
                "rounded-full p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors cursor-pointer shrink-0",
                className
            )}
            initial="initial"
            animate="animate"
            exit="exit"
            variants={variants}
            {...props}
        >
            {children || <XIcon className="h-4 w-4" />}
        </motion.button>
    )
}

export default {
    MorphingDialog,
    MorphingDialogTrigger,
    MorphingDialogContainer,
    MorphingDialogContent,
    MorphingDialogClose,
    MorphingDialogTitle,
    MorphingDialogSubtitle,
    MorphingDialogDescription,
    MorphingDialogImage,
}
