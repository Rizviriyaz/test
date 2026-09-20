import React from "react"

/**
 * Official ATS Brand Icon (Emblem)
 */
export function AtsIcon({ className = "h-9 w-9", ...props }) {
    return (
        <svg
            viewBox="0 0 220 220"
            className={className}
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
            {...props}
        >
            <defs>
                <linearGradient id="atsOrangeGradIcon" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#FF8A00" />
                    <stop offset="60%" stopColor="#F57016" />
                    <stop offset="100%" stopColor="#E45305" />
                </linearGradient>

                <linearGradient id="atsTealGradIcon" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#287898" />
                    <stop offset="50%" stopColor="#1C5E7A" />
                    <stop offset="100%" stopColor="#144A62" />
                </linearGradient>
            </defs>

            <g transform="translate(0, 0)">
                {/* Bottom sharp pointer / pin tip */}
                <polygon points="110,130 92,204 128,145" fill="#E45305" />
                <polygon points="105,145 92,204 116,155" fill="#1C5E7A" />

                {/* Top Left Wing / Needle */}
                <polygon points="40,36 12,50 65,70" fill="#1C5E7A" />

                {/* Top Right Wing / Needle */}
                <polygon points="180,36 214,50 155,70" fill="#1C5E7A" />

                {/* Main Orange Circular Body */}
                <circle cx="110" cy="100" r="75" fill="url(#atsOrangeGradIcon)" />

                {/* Left Teal Inner Swoosh */}
                <path d="M 52,90 C 50,55 85,38 110,38 C 90,52 82,75 88,102 C 92,120 105,135 120,145 C 90,145 60,122 52,90 Z" fill="url(#atsTealGradIcon)" />

                {/* Right/Upper Teal Swoosh */}
                <path d="M 168,90 C 170,55 135,38 110,38 C 130,52 142,75 136,102 C 130,125 110,142 95,148 C 132,150 162,125 168,90 Z" fill="url(#atsTealGradIcon)" />

                {/* Central White Globe */}
                <circle cx="110" cy="100" r="26" fill="#FFFFFF" stroke="#F57016" strokeWidth="1.8" />

                {/* Globe Grid Lines (Orange) */}
                <line x1="84" y1="100" x2="136" y2="100" stroke="#F57016" strokeWidth="1.2" />
                <ellipse cx="110" cy="92" rx="23" ry="7" fill="none" stroke="#F57016" strokeWidth="1" />
                <ellipse cx="110" cy="108" rx="23" ry="7" fill="none" stroke="#F57016" strokeWidth="1" />

                <line x1="110" y1="74" x2="110" y2="126" stroke="#F57016" strokeWidth="1.2" />
                <ellipse cx="110" cy="100" rx="14" ry="26" fill="none" stroke="#F57016" strokeWidth="1.1" />
                <ellipse cx="110" cy="100" rx="7" ry="26" fill="none" stroke="#F57016" strokeWidth="1" />
            </g>
        </svg>
    )
}

/**
 * Official ATS Horizontal Logo with Text
 */
export default function AtsLogo({ className = "h-10 w-auto", showSubtitle = true, ...props }) {
    return (
        <div className={`inline-flex items-center gap-3 ${className}`} {...props}>
            <AtsIcon className="h-10 w-10 shrink-0" />
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
