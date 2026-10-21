import { useState, useEffect } from "react"
import { Link } from "react-router-dom"
import {
    MapPin,
    Clock,
    Activity,
    ShieldAlert,
    Gauge,
    ArrowRight,
    Menu,
    X,
    Radio,
    ChevronRight,
    CheckCircle2,
    Compass,
} from "lucide-react"
import AtsLogo from "../assets/AtsLogo"
import { Magnetic } from "../components/core/magnetic"
import { useAuth } from "../context/useAuth"

// Real captured screenshots from the active ATS Fleet Management dashboard
import fleetOverviewImg from "../assets/screenshots/fleet-overview.png"
import liveTrackingImg from "../assets/screenshots/live-tracking.png"
import vehicleTelemetryImg from "../assets/screenshots/vehicle-telemetry.png"
import fleetAlertsImg from "../assets/screenshots/fleet-alerts.png"

const CORE_CAPABILITIES = [
    {
        icon: MapPin,
        tag: "Location & Status",
        title: "Real-Time Fleet Tracking",
        desc: "Track every vehicle across Dubai, Sharjah, Abu Dhabi, and the Northern Emirates with live position updates, moving/idling/stopped status, and geofence verification.",
    },
    {
        icon: Clock,
        tag: "Engine & Fuel",
        title: "Idle Cost & Waste Analytics",
        desc: "Quantify engine idling in real time. Flag stationary vehicles with ignition on over 5 minutes, track historical idle waste, and curtail unbudgeted fuel loss.",
    },
    {
        icon: Activity,
        tag: "Diagnostics",
        title: "Vehicle Health & Telemetry",
        desc: "Monitor battery voltages (12V/24V/28V systems), ignition events, connection latency, and GPS satellite fix strength without manual vehicle inspections.",
    },
    {
        icon: ShieldAlert,
        tag: "Risk Mitigation",
        title: "Harsh Driving & Safety Alerts",
        desc: "Detect harsh cornering, rapid acceleration, and sudden braking with precise G-force metrics. Categorize critical immediate risks versus threshold warnings.",
    },
    {
        icon: Compass,
        tag: "Logistics",
        title: "Trip Reconstruction & Auditing",
        desc: "Segment daily vehicle operations into verifiable trips. Audit exact departure, transit time, arrival, and distance logged for every customer run.",
    },
    {
        icon: Gauge,
        tag: "Utilization",
        title: "Mileage & Utilization Trends",
        desc: "Evaluate fleet mileage across Today, Yesterday, Last 1 Week, and Last Month. Identify under-utilized assets and balance operational workload across groups.",
    },
]

const HERO_SCREENSHOTS = [
    {
        id: "tracking",
        title: "Live GPS Tracking · UAE",
        badge: "Active",
        tag: "UAE Map",
        image: liveTrackingImg,
        alt: "Live GPS tracking operational map",
        dotColor: "bg-emerald-500",
        posClass: "top-0 left-0 -rotate-2 z-10",
    },
    {
        id: "alerts",
        title: "Fleet Sensor Alerts",
        badge: "Live",
        tag: "1.48G",
        image: fleetAlertsImg,
        alt: "Real-time sensor alerts and diagnostics",
        dotColor: "bg-rose-500",
        posClass: "top-3 sm:top-4 right-0 rotate-2 z-15",
    },
    {
        id: "telemetry",
        title: "Unit Telemetry · #13158",
        badge: "Online",
        tag: "28.87 V",
        image: vehicleTelemetryImg,
        alt: "Live vehicle battery and GPS telemetry",
        dotColor: "bg-[#33758F]",
        posClass: "top-7 sm:top-8 left-2 sm:left-4 -rotate-1 z-20",
    },
    {
        id: "overview",
        title: "Fleet Operations Command",
        badge: "Connected",
        tag: "103 Units",
        image: fleetOverviewImg,
        alt: "ATS Fleet Overview console dashboard",
        dotColor: "bg-emerald-500",
        posClass: "top-11 sm:top-13 right-1 sm:right-3 rotate-1 z-25 shadow-2xl",
    },
]

export default function Landing() {
    const { isAuthenticated } = useAuth()
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
    const [activeScreenshot, setActiveScreenshot] = useState(null)

    // Cursor tracking for subtle ambient spotlight
    const [cursorPos, setCursorPos] = useState({ x: -1000, y: -1000 })
    const handleHeroMouseMove = (e) => {
        const rect = e.currentTarget.getBoundingClientRect()
        setCursorPos({
            x: e.clientX - rect.left,
            y: e.clientY - rect.top,
        })
    }

    // Close mobile menu on resize to desktop
    useEffect(() => {
        const handleResize = () => {
            if (window.innerWidth >= 768) {
                setMobileMenuOpen(false)
            }
        }
        window.addEventListener("resize", handleResize)
        return () => window.removeEventListener("resize", handleResize)
    }, [])

    return (
        <div className="bg-[#FAFBFD] min-h-screen flex flex-col justify-between text-slate-900 font-sans selection:bg-[#33758F] selection:text-white antialiased">
            {/* ─── SITE HEADER ──────────────────────────────────────────────── */}
            <header className="sticky top-0 z-50 bg-white/90 backdrop-blur-md border-b border-slate-200/80 transition-all">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-20 flex items-center justify-between">
                    {/* Brand Logo (Enlarged & Prominent) */}
                    <a href="#" className="flex items-center gap-3 shrink-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#33758F] rounded-lg">
                        <AtsLogo className="h-11 sm:h-12 md:h-13 max-h-14 w-auto" />
                    </a>

                    {/* On-Page Anchor Navigation (No internal page routes) */}
                    <nav className="hidden md:flex items-center gap-1 bg-slate-100/80 border border-slate-200/70 p-1 rounded-full text-xs font-semibold text-slate-600">
                        <a
                            href="#features"
                            className="px-4 py-1.5 rounded-full hover:text-slate-900 hover:bg-white transition-all"
                        >
                            Capabilities
                        </a>
                        <a
                            href="#telemetry"
                            className="px-4 py-1.5 rounded-full hover:text-slate-900 hover:bg-white transition-all"
                        >
                            Telemetry
                        </a>
                        <a
                            href="#safety"
                            className="px-4 py-1.5 rounded-full hover:text-slate-900 hover:bg-white transition-all"
                        >
                            Safety & Alerts
                        </a>
                    </nav>

                    {/* Single Conditional Auth Button */}
                    <div className="flex items-center gap-3">
                        {isAuthenticated ? (
                            <Link
                                to="/dashboard"
                                className="inline-flex items-center gap-2 bg-[#33758F] hover:bg-[#285E73] text-white text-xs sm:text-sm font-semibold px-4 sm:px-5 py-2.5 rounded-full transition-all shadow-sm hover:shadow active:scale-98"
                            >
                                <span>Launch Dashboard</span>
                                <ArrowRight className="w-4 h-4" />
                            </Link>
                        ) : (
                            <Link
                                to="/login"
                                className="inline-flex items-center gap-2 bg-[#33758F] hover:bg-[#285E73] text-white text-xs sm:text-sm font-semibold px-4 sm:px-5 py-2.5 rounded-full transition-all shadow-sm hover:shadow active:scale-98"
                            >
                                <span>Sign In</span>
                                <ArrowRight className="w-4 h-4" />
                            </Link>
                        )}

                        {/* Mobile Hamburger Toggle */}
                        <button
                            type="button"
                            onClick={() => setMobileMenuOpen(true)}
                            aria-label="Open navigation menu"
                            className="md:hidden p-2 text-slate-700 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
                        >
                            <Menu className="h-6 w-6" />
                        </button>
                    </div>
                </div>
            </header>

            {/* ─── MOBILE DRAWER (NO INTERNAL PAGE ROUTES) ──────────────────── */}
            {mobileMenuOpen && (
                <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-xs z-[60] md:hidden flex justify-end">
                    <div className="w-full max-w-sm bg-white h-full shadow-2xl flex flex-col p-6 animate-in slide-in-from-right duration-200">
                        <div className="flex items-center justify-between pb-6 border-b border-slate-100">
                            <AtsLogo className="h-9 sm:h-10 max-h-10 w-auto" />
                            <button
                                type="button"
                                onClick={() => setMobileMenuOpen(false)}
                                aria-label="Close navigation menu"
                                className="p-2 text-slate-500 hover:text-slate-900 rounded-lg hover:bg-slate-100"
                            >
                                <X className="h-6 w-6" />
                            </button>
                        </div>

                        <div className="flex flex-col py-6 space-y-2 text-base font-semibold text-slate-700">
                            <a
                                href="#features"
                                onClick={() => setMobileMenuOpen(false)}
                                className="py-2.5 px-3 rounded-lg hover:bg-slate-50 hover:text-[#33758F] transition-colors"
                            >
                                Capabilities
                            </a>
                            <a
                                href="#telemetry"
                                onClick={() => setMobileMenuOpen(false)}
                                className="py-2.5 px-3 rounded-lg hover:bg-slate-50 hover:text-[#33758F] transition-colors"
                            >
                                Telemetry
                            </a>
                            <a
                                href="#safety"
                                onClick={() => setMobileMenuOpen(false)}
                                className="py-2.5 px-3 rounded-lg hover:bg-slate-50 hover:text-[#33758F] transition-colors"
                            >
                                Safety & Alerts
                            </a>
                        </div>

                        <div className="mt-auto pt-6 border-t border-slate-100">
                            {isAuthenticated ? (
                                <Link
                                    to="/dashboard"
                                    onClick={() => setMobileMenuOpen(false)}
                                    className="w-full flex items-center justify-center gap-2 bg-[#33758F] hover:bg-[#285E73] text-white py-3 rounded-xl font-semibold text-sm shadow-sm"
                                >
                                    <span>Launch Dashboard</span>
                                    <ArrowRight className="w-4 h-4" />
                                </Link>
                            ) : (
                                <Link
                                    to="/login"
                                    onClick={() => setMobileMenuOpen(false)}
                                    className="w-full flex items-center justify-center gap-2 bg-[#33758F] hover:bg-[#285E73] text-white py-3 rounded-xl font-semibold text-sm shadow-sm"
                                >
                                    <span>Sign In</span>
                                    <ArrowRight className="w-4 h-4" />
                                </Link>
                            )}
                        </div>
                    </div>
                </div>
            )}

            <main className="flex-grow">
                {/* ─── HERO SECTION: TWO-COLUMN LAYOUT (SCOPED STACKING CONTEXT z-10) ── */}
                <section
                    onMouseMove={handleHeroMouseMove}
                    className="relative z-10 pt-10 sm:pt-14 pb-16 sm:pb-20 overflow-hidden"
                >
                    {/* Background Subtle Gradient Blobs */}
                    <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[850px] h-[450px] bg-gradient-to-tr from-[#33758F]/10 via-[#285E73]/5 to-transparent rounded-full blur-3xl pointer-events-none -z-10" />

                    {/* Interactive Cursor Spotlight Glow */}
                    <div
                        className="pointer-events-none absolute -inset-px transition-opacity duration-300 opacity-60 hidden md:block -z-10"
                        style={{
                            background: `radial-gradient(650px circle at ${cursorPos.x}px ${cursorPos.y}px, rgba(51, 117, 143, 0.09), transparent 70%)`,
                        }}
                    />

                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="grid grid-cols-1 lg:grid-cols-12 gap-10 lg:gap-8 xl:gap-12 items-center">
                            {/* ─── LEFT COLUMN: HEADLINE, COPY & CTA ──────────────── */}
                            <div className="lg:col-span-5 text-center lg:text-left">
                                <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold tracking-tight font-display text-slate-900 leading-[1.08]">
                                    Every Vehicle. <br />
                                    <span className="text-[#33758F]">One Clear View.</span>
                                </h1>

                                <p className="mt-5 text-base sm:text-lg text-slate-600 max-w-xl mx-auto lg:mx-0 font-normal leading-relaxed">
                                    Live fleet tracking, idle cost intelligence, battery health telemetry, and safety diagnostics — centralized in one operational command center.
                                </p>

                                {/* Action Buttons with Magnetic Cursor-Following Effect */}
                                <div className="mt-8 flex flex-wrap items-center justify-center lg:justify-start gap-3">
                                    <Magnetic intensity={0.25} range={90}>
                                        {isAuthenticated ? (
                                            <Link
                                                to="/dashboard"
                                                className="inline-flex items-center gap-2 rounded-full bg-[#33758F] hover:bg-[#285E73] px-6 sm:px-7 py-3.5 text-sm font-semibold text-white shadow-sm hover:shadow transition-all active:scale-98"
                                            >
                                                <span>Launch Dashboard</span>
                                                <ArrowRight className="w-4 h-4" />
                                            </Link>
                                        ) : (
                                            <Link
                                                to="/login"
                                                className="inline-flex items-center gap-2 rounded-full bg-[#33758F] hover:bg-[#285E73] px-6 sm:px-7 py-3.5 text-sm font-semibold text-white shadow-sm hover:shadow transition-all active:scale-98"
                                            >
                                                <span>Sign In</span>
                                                <ArrowRight className="w-4 h-4" />
                                            </Link>
                                        )}
                                    </Magnetic>

                                    <Magnetic intensity={0.2} range={80}>
                                        <a
                                            href="#features"
                                            className="inline-flex items-center gap-1.5 rounded-full border border-slate-200/90 hover:border-slate-300 bg-white hover:bg-slate-50 px-5 sm:px-6 py-3.5 text-sm font-semibold text-slate-700 transition-all shadow-2xs"
                                        >
                                            <span>Explore Capabilities</span>
                                            <ChevronRight className="w-4 h-4 text-slate-400" />
                                        </a>
                                    </Magnetic>
                                </div>
                            </div>

                            {/* ─── RIGHT COLUMN: COMPACT OVERLAPPING SCREENSHOT STACK (EXACT SAME SIZE) ── */}
                            <div className="lg:col-span-7 relative w-full max-w-lg lg:max-w-none mx-auto select-none py-4 sm:py-6 lg:py-8">
                                {/* Ambient Background Glow */}
                                <div className="absolute inset-4 bg-gradient-to-tr from-[#33758F]/15 via-[#285E73]/8 to-transparent rounded-3xl blur-2xl -z-10 pointer-events-none" />

                                <div className="relative mx-auto w-full max-w-[480px] sm:max-w-[540px] h-[340px] sm:h-[370px]">
                                    {HERO_SCREENSHOTS.map((item) => {
                                        const isSelected = activeScreenshot === item.id
                                        return (
                                            <div
                                                key={item.id}
                                                onClick={() => setActiveScreenshot(isSelected ? null : item.id)}
                                                className={`absolute w-[86%] sm:w-[84%] rounded-2xl bg-white p-2 sm:p-2.5 shadow-xl border border-slate-200/90 transition-all duration-300 hover:rotate-0 hover:scale-[1.02] hover:z-30 cursor-pointer ${
                                                    isSelected ? "z-30 !rotate-0 !scale-[1.03] shadow-2xl ring-2 ring-[#33758F]/30" : item.posClass
                                                }`}
                                                title={`Click to focus: ${item.title}`}
                                            >
                                                {/* Identical Window Chrome Header Bar */}
                                                <div className="flex items-center justify-between px-2.5 py-1.5 bg-slate-50 border border-slate-100 rounded-lg mb-1.5 text-xs text-slate-600">
                                                    <div className="flex items-center gap-2 min-w-0">
                                                        <div className="flex items-center gap-1 shrink-0">
                                                            <span className="w-2 h-2 rounded-full bg-rose-400" />
                                                            <span className="w-2 h-2 rounded-full bg-amber-400" />
                                                            <span className="w-2 h-2 rounded-full bg-emerald-400" />
                                                        </div>
                                                        <span className="text-[11px] sm:text-xs font-semibold text-slate-800 truncate">
                                                            {item.title}
                                                        </span>
                                                    </div>
                                                    <div className="flex items-center gap-1.5 shrink-0 ml-2">
                                                        <span className={`w-1.5 h-1.5 rounded-full ${item.dotColor} animate-pulse`} />
                                                        <span className="text-[10px] font-mono text-slate-500">{item.tag}</span>
                                                    </div>
                                                </div>

                                                {/* Full Dashboard Screenshot View: Uncropped & Crisp */}
                                                <div className="w-full aspect-[16/8] rounded-xl overflow-hidden bg-slate-900/5 border border-slate-100/90 flex items-center justify-center">
                                                    <img
                                                        src={item.image}
                                                        alt={item.alt}
                                                        className="w-full h-full object-contain block select-none"
                                                    />
                                                </div>
                                            </div>
                                        )
                                    })}
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                {/* ─── SHOWCASE CORE CAPABILITIES (REQUIREMENT 6) ───────────── */}
                <section id="features" className="py-20 bg-white border-y border-slate-200/80">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="text-center max-w-3xl mx-auto mb-16">
                            <span className="text-xs font-bold uppercase tracking-wider text-[#33758F] font-mono">
                                Comprehensive Telemetry Suite
                            </span>
                            <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight font-display text-slate-900 mt-2">
                                Engineered for High-Uptime Fleet Operations
                            </h2>
                            <p className="mt-4 text-slate-600 text-sm sm:text-base leading-relaxed">
                                Complete coverage of operations, asset security, and cost control without generic vanity metrics.
                            </p>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 sm:gap-8">
                            {CORE_CAPABILITIES.map((cap, idx) => {
                                const Icon = cap.icon
                                return (
                                    <div
                                        key={idx}
                                        className="bg-[#FAFBFD] border border-slate-200/80 hover:border-[#33758F]/40 p-6 sm:p-7 rounded-2xl transition-all duration-200 hover:shadow-md flex flex-col justify-between group"
                                    >
                                        <div>
                                            <div className="flex items-center justify-between mb-4">
                                                <div className="w-11 h-11 rounded-xl bg-[#F0F6F9] border border-[#D1E2EA] flex items-center justify-center text-[#33758F] group-hover:bg-[#33758F] group-hover:text-white transition-colors duration-200">
                                                    <Icon className="w-5 h-5" />
                                                </div>
                                                <span className="text-[11px] font-mono font-semibold px-2 py-0.5 rounded bg-slate-200/60 text-slate-600">
                                                    {cap.tag}
                                                </span>
                                            </div>
                                            <h3 className="text-lg font-bold text-slate-900 mb-2 group-hover:text-[#33758F] transition-colors">
                                                {cap.title}
                                            </h3>
                                            <p className="text-xs sm:text-sm text-slate-600 leading-relaxed">
                                                {cap.desc}
                                            </p>
                                        </div>

                                        <div className="pt-5 mt-5 border-t border-slate-200/60 flex items-center text-xs font-semibold text-[#33758F]">
                                            <span>Full live dashboard support</span>
                                            <CheckCircle2 className="w-4 h-4 ml-auto text-emerald-500" />
                                        </div>
                                    </div>
                                )
                            })}
                        </div>
                    </div>
                </section>

                {/* ─── DEEP DIVE: TELEMETRY & IDLE ANALYTICS ─────────────────── */}
                <section id="telemetry" className="py-20 bg-[#FAFBFD]">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
                            <div>
                                <span className="text-xs font-bold uppercase tracking-wider text-[#33758F] font-mono">
                                    Idle Waste Mitigation
                                </span>
                                <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight font-display text-slate-900 mt-2">
                                    Turn Wasted Engine Hours Into Verified Savings
                                </h2>
                                <p className="mt-4 text-slate-600 text-sm sm:text-base leading-relaxed">
                                    Commercial fleets lose up to 15% of annual fuel expenditure directly to stationary idling. ATS surfaces every idling event the minute it crosses 5 minutes, computing exact fuel liters and AED loss.
                                </p>

                                <div className="mt-6 space-y-4">
                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Direct Financial Attribution</h4>
                                            <p className="text-xs text-slate-600">Calculates fuel loss based on exact unit idle hours and fuel type rates.</p>
                                        </div>
                                    </div>

                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Longest Idle Alerts</h4>
                                            <p className="text-xs text-slate-600">Instantly spotlights excessive stationary runs (e.g. 24m+ idle runs on delivery units).</p>
                                        </div>
                                    </div>

                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-emerald-100 text-emerald-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Auditable Historical Reports</h4>
                                            <p className="text-xs text-slate-600">Review idle trends by Today, Yesterday, Last 1 Week, or custom date ranges.</p>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <div className="rounded-2xl border border-slate-200/90 bg-white p-3 shadow-lg overflow-hidden">
                                <div className="px-3 py-2 bg-slate-50 border-b border-slate-100 rounded-t-xl flex items-center justify-between text-xs font-semibold text-slate-700">
                                    <div className="flex items-center gap-2">
                                        <Clock className="w-4 h-4 text-[#33758F]" />
                                        <span>Live Idling Monitor · Real Dashboard Feed</span>
                                    </div>
                                    <span className="text-amber-600 font-mono text-[11px] font-bold">24m 23s Idle Flagged</span>
                                </div>
                                <img
                                    src={fleetOverviewImg}
                                    alt="Idle telemetry card from live dashboard"
                                    className="w-full h-auto rounded-b-xl object-cover object-bottom max-h-[380px]"
                                />
                            </div>
                        </div>
                    </div>
                </section>

                {/* ─── DEEP DIVE: SAFETY & HARSH DRIVING ────────────────────── */}
                <section id="safety" className="py-20 bg-white border-t border-slate-200/80">
                    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
                            <div className="order-2 lg:order-1 rounded-2xl border border-slate-200/90 bg-white p-3 shadow-lg overflow-hidden">
                                <div className="px-3 py-2 bg-slate-50 border-b border-slate-100 rounded-t-xl flex items-center justify-between text-xs font-semibold text-slate-700">
                                    <div className="flex items-center gap-2">
                                        <ShieldAlert className="w-4 h-4 text-rose-600" />
                                        <span>G-Force Event Log · Unit #600446393</span>
                                    </div>
                                    <span className="text-rose-600 font-mono text-[11px] font-bold">1.48G Cornering</span>
                                </div>
                                <img
                                    src={fleetAlertsImg}
                                    alt="Fleet safety alerts real screenshot"
                                    className="w-full h-auto rounded-b-xl object-cover object-top max-h-[380px]"
                                />
                            </div>

                            <div className="order-1 lg:order-2">
                                <span className="text-xs font-bold uppercase tracking-wider text-rose-600 font-mono">
                                    Safety & Sensor Telemetry
                                </span>
                                <h2 className="text-3xl sm:text-4xl font-extrabold tracking-tight font-display text-slate-900 mt-2">
                                    Identify Risk Before It Becomes An Incident
                                </h2>
                                <p className="mt-4 text-slate-600 text-sm sm:text-base leading-relaxed">
                                    On-board accelerometer telemetry monitors aggressive vehicle handling. High G-force events are timestamped, attributed to specific delivery units, and cataloged for driver coaching and insurance risk reduction.
                                </p>

                                <div className="mt-6 space-y-4">
                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-rose-100 text-rose-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Harsh Cornering & Acceleration</h4>
                                            <p className="text-xs text-slate-600">Detects abrupt lateral movements and rapid speed bursts exceeding safety limits.</p>
                                        </div>
                                    </div>

                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-rose-100 text-rose-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Real-Time Risk Grading</h4>
                                            <p className="text-xs text-slate-600">Immediate classification into Critical risks and Warning threshold violations.</p>
                                        </div>
                                    </div>

                                    <div className="flex items-start gap-3">
                                        <div className="w-6 h-6 rounded-full bg-rose-100 text-rose-700 flex items-center justify-center shrink-0 mt-0.5 font-bold text-xs">
                                            ✓
                                        </div>
                                        <div>
                                            <h4 className="text-sm font-bold text-slate-900">Preventive Maintenance Synergy</h4>
                                            <p className="text-xs text-slate-600">Protects brakes, suspensions, and tires by identifying abusive operating patterns.</p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </section>

                {/* ─── BOTTOM CALL TO ACTION ─────────────────────────────────── */}
                <section className="py-20 bg-gradient-to-b from-[#FAFBFD] to-[#F0F6F9] border-t border-slate-200/80">
                    <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                        <div className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-white border border-[#D1E2EA] text-[#33758F] text-xs font-semibold mb-6 shadow-2xs">
                            <Radio className="w-3.5 h-3.5 animate-pulse" />
                            <span>Commercial Fleet Telemetry Platform</span>
                        </div>

                        <h2 className="text-3xl sm:text-4xl md:text-5xl font-extrabold tracking-tight font-display text-slate-900">
                            Command Your Fleet With Deterministic Precision
                        </h2>
                        <p className="mt-4 text-base sm:text-lg text-slate-600 max-w-xl mx-auto">
                            Eliminate blind spots, protect fuel budgets, and monitor every vehicle on a unified operations platform.
                        </p>

                        <div className="mt-8 flex flex-wrap items-center justify-center gap-4">
                            <Magnetic intensity={0.2} range={80}>
                                {isAuthenticated ? (
                                    <Link
                                        to="/dashboard"
                                        className="inline-flex items-center gap-2 rounded-full bg-[#33758F] hover:bg-[#285E73] px-8 py-3.5 text-sm font-semibold text-white shadow-sm hover:shadow transition-all active:scale-98"
                                    >
                                        <span>Launch Dashboard</span>
                                        <ArrowRight className="w-4 h-4" />
                                    </Link>
                                ) : (
                                    <Link
                                        to="/login"
                                        className="inline-flex items-center gap-2 rounded-full bg-[#33758F] hover:bg-[#285E73] px-8 py-3.5 text-sm font-semibold text-white shadow-sm hover:shadow transition-all active:scale-98"
                                    >
                                        <span>Sign In</span>
                                        <ArrowRight className="w-4 h-4" />
                                    </Link>
                                )}
                            </Magnetic>
                        </div>
                    </div>
                </section>
            </main>

            {/* ─── FOOTER (NO INTERNAL PAGE ROUTES) ─────────────────────────── */}
            <footer className="border-t border-slate-200 bg-white py-12">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row items-center justify-between gap-6">
                    <div className="flex flex-col sm:flex-row items-center gap-3 text-center sm:text-left">
                        <AtsLogo className="h-8 max-h-8" />
                        <span className="hidden sm:inline-block text-slate-300">|</span>
                        <span className="text-xs text-slate-500 font-medium">
                            Advanced Technology Solutions · Live Fleet Telemetry System
                        </span>
                    </div>

                    {/* Section Anchors Only (No internal page routes) */}
                    <div className="flex items-center flex-wrap justify-center gap-6 text-xs font-semibold text-slate-600">
                        <a href="#features" className="hover:text-slate-900 transition-colors">
                            Capabilities
                        </a>
                        <a href="#telemetry" className="hover:text-slate-900 transition-colors">
                            Idle Analytics
                        </a>
                        <a href="#safety" className="hover:text-slate-900 transition-colors">
                            Safety Alerts
                        </a>
                    </div>

                    <div className="text-xs text-slate-400">
                        © {new Date().getFullYear()} ATS Fleet Management. All rights reserved.
                    </div>
                </div>
            </footer>
        </div>
    )
}
