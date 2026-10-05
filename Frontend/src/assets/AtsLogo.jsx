/**
 * Official ATS Brand Logo
 * Renders the exact supplied ATS logo image preserving proportions and aspect ratio.
 */
export default function AtsLogo({ className = "h-9 w-auto", alt = "ATS Advanced Technology Solutions", ...props }) {
    return (
        <img
            src="/logo.png"
            alt={alt}
            className={`w-auto object-contain shrink-0 ${className}`}
            {...props}
        />
    )
}

export function AtsIcon({ className = "h-8 w-auto", alt = "ATS Logo", ...props }) {
    return (
        <img
            src="/logo.png"
            alt={alt}
            className={`w-auto object-contain shrink-0 ${className}`}
            {...props}
        />
    )
}

