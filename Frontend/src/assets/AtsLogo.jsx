import React from "react"

/**
 * Official ATS Brand Icon (Emblem only)
 */
export function AtsIcon({ className = "h-9 w-9", ...props }) {
    return (
        <img
            src="/logo.png"
            alt="ATS Logo"
            className={className}
            style={{ objectFit: "contain" }}
            {...props}
        />
    )
}

/**
 * Official ATS Horizontal Logo with Text
 */
export default function AtsLogo({ className = "h-10 w-auto", showSubtitle = true, ...props }) {
    return (
        <div className={`inline-flex items-center gap-3 ${className}`} {...props}>
            <img
                src="/logo.png"
                alt="ATS Logo"
                className="h-10 w-10 shrink-0"
                style={{ objectFit: "contain" }}
            />
            <div className="flex flex-col justify-center">
                <span className="text-xl font-black tracking-tight text-[#1C5E7A] leading-none">
                    ATS
                </span>
                {showSubtitle && (
                    <span className="text-[9px] font-extrabold tracking-[0.12em] uppercase text-[#1C5E7A] mt-0.5 leading-tight max-w-[13.5rem]">
                        Advanced Technology Solutions
                    </span>
                )}
            </div>
        </div>
    )
}
