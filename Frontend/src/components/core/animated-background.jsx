import { Children, cloneElement, useId, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { cn } from "../../lib/utils"

export function AnimatedBackground({
    children,
    defaultValue,
    className,
    transition,
    enableHover = false,
    onValueChange,
}) {
    const uniqueId = useId()
    const [activeValue, setActiveValue] = useState(defaultValue ?? null)

    const handleSetActive = (id) => {
        if (activeValue !== id) {
            setActiveValue(id)
            onValueChange?.(id)
        }
    }

    return Children.map(children, (child, index) => {
        const id = child.props["data-id"] ?? String(index)
        const interactionProps = enableHover
            ? {
                onMouseEnter: () => handleSetActive(id),
                onMouseLeave: () => handleSetActive(defaultValue ?? null),
            }
            : {
                onClick: (event) => {
                    child.props.onClick?.(event)
                    handleSetActive(id)
                },
            }

        return cloneElement(
            child,
            {
                ...interactionProps,
                className: cn("relative isolate", child.props.className),
                "data-checked": activeValue === id ? "true" : "false",
            },
            <>
                <AnimatePresence initial={false}>
                    {activeValue === id && (
                        <motion.span
                            layoutId={`animated-background-${uniqueId}`}
                            className={cn("absolute inset-0 z-0", className)}
                            transition={transition}
                            initial={{ opacity: enableHover ? 0 : 1 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                        />
                    )}
                </AnimatePresence>
                <span className="relative z-10">{child.props.children}</span>
            </>
        )
    })
}
