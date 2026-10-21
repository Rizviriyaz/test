import { cn } from "../../lib/utils"

export function ScrollArea({ children, className, style, ...props }) {
    return (
        <div
            className={cn(
                "overflow-y-auto overscroll-contain pr-1.5 [scrollbar-width:thin] [scrollbar-color:rgba(148,163,184,0.35)_transparent]",
                className
            )}
            style={style}
            {...props}
        >
            {children}
        </div>
    )
}

export default ScrollArea
