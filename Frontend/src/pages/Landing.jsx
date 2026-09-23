import { useEffect, useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import {
    Menu,
    X,
} from "lucide-react"
import AtsLogo from "../assets/AtsLogo"
import { AnimatedBackground } from "../components/core/animated-background"
import { Magnetic } from "../components/core/magnetic"
import { useAuth } from "../context/AuthContext"

function usePrefersReducedMotion() {
    const [reduced, setReduced] = useState(false)

    useEffect(() => {
        const mq = window.matchMedia("(prefers-reduced-motion: reduce)")
        setReduced(mq.matches)
        const handler = (e) => setReduced(e.matches)
        mq.addEventListener("change", handler)
        return () => mq.removeEventListener("change", handler)
    }, [])

    return reduced
}



const NAV_TABS = [
    { id: "Capabilities", href: "#features", type: "anchor" },
    { id: "Tracking", href: "/tracking", type: "route" },
    { id: "Vehicles", href: "/vehicles", type: "route" },
    { id: "Health", href: "/vehicle-health", type: "route" },
    { id: "About", href: "#about", type: "anchor" },
]

export default function Landing() {
    const { isAuthenticated } = useAuth()
    const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
    const navigate = useNavigate()
    const reducedMotion = usePrefersReducedMotion()

    return (
        <div className="bg-white min-h-screen flex flex-col justify-between selection:bg-black selection:text-white text-zinc-900 font-sans overflow-x-hidden">
            <header className="w-full max-w-7xl mx-auto px-6 py-6 grid grid-cols-[1fr_auto] md:grid-cols-3 items-center gap-4">
                <Link to="/" className="justify-self-start min-w-0">
                    <AtsLogo className="h-auto" showSubtitle={true} />
                </Link>

                <nav className="hidden md:flex items-center justify-center">
                    <AnimatedBackground
                        defaultValue={NAV_TABS[0].id}
                        className="rounded-lg bg-zinc-100"
                        transition={{ type: "spring", bounce: 0.2, duration: 0.3 }}
                        enableHover
                    >
                        {NAV_TABS.map((tab) =>
                            tab.type === "route" ? (
                                <Link
                                    key={tab.id}
                                    data-id={tab.id}
                                    to={tab.href}
                                    className="px-3 py-1.5 text-sm font-medium text-zinc-600 transition-colors duration-300 hover:text-zinc-950"
                                >
                                    {tab.id}
                                </Link>
                            ) : (
                                <a
                                    key={tab.id}
                                    data-id={tab.id}
                                    href={tab.href}
                                    className="px-3 py-1.5 text-sm font-medium text-zinc-600 transition-colors duration-300 hover:text-zinc-950"
                                >
                                    {tab.id}
                                </a>
                            )
                        )}
                    </AnimatedBackground>
                </nav>

                <div className="justify-self-end flex items-center gap-2 sm:gap-3">
                    {!isAuthenticated && (
                        <Link
                            to="/login"
                            className="hidden sm:inline-flex items-center text-xs font-semibold text-zinc-700 hover:text-black px-3.5 py-2 rounded-full border border-zinc-200 hover:border-zinc-300 transition-all"
                        >
                            Sign In
                        </Link>
                    )}
                    <button
                        type="button"
                        onClick={() => setMobileMenuOpen(true)}
                        aria-label="Open navigation menu"
                        className="md:hidden p-2 text-zinc-700"
                    >
                        <Menu className="h-6 w-6" />
                    </button>
                    <Magnetic intensity={reducedMotion ? 0 : 0.28} disabled={reducedMotion}>
                        <Link
                            to="/dashboard"
                            className="inline-flex items-center bg-black text-white px-4 sm:px-5 py-2.5 rounded-full text-sm font-medium hover:bg-zinc-800 transition-all shadow-sm"
                        >
                            Launch Dashboard
                        </Link>
                    </Magnetic>
                </div>
            </header>

            {mobileMenuOpen && (
                <div className="fixed inset-0 bg-white z-50 md:hidden flex flex-col p-6">
                    <div className="flex justify-between items-center mb-8">
                        <AtsLogo className="h-auto" showSubtitle={true} />
                        <button type="button" onClick={() => setMobileMenuOpen(false)} aria-label="Close navigation menu" className="p-2 text-zinc-700">
                            <X className="h-6 w-6" />
                        </button>
                    </div>
                    <div className="flex flex-col space-y-2 text-lg font-medium text-zinc-800">
                        {NAV_TABS.map((tab) =>
                            tab.type === "route" ? (
                                <Link key={tab.id} to={tab.href} onClick={() => setMobileMenuOpen(false)} className="border-b border-zinc-100 py-3">
                                    {tab.id}
                                </Link>
                            ) : (
                                <a key={tab.id} href={tab.href} onClick={() => setMobileMenuOpen(false)} className="border-b border-zinc-100 py-3">
                                    {tab.id}
                                </a>
                            )
                        )}
                    </div>
                    <div className="mt-auto pt-6 border-t border-zinc-200">
                        <Link
                            to="/dashboard"
                            onClick={() => setMobileMenuOpen(false)}
                            className="w-full flex items-center justify-center bg-black text-white py-3 rounded-full font-medium"
                        >
                            Launch Dashboard
                        </Link>
                    </div>
                </div>
            )}

            <main className="flex-grow pb-12">
                <section className="max-w-4xl mx-auto px-6 pt-10 sm:pt-14 pb-12 sm:pb-16 text-center">
                    <h1 className="text-4xl sm:text-6xl md:text-7xl font-extrabold tracking-[-0.03em] font-display text-zinc-900 leading-[1.08]">
                        Every Vehicle. One Clear View.
                    </h1>
                    <p className="mt-6 text-lg sm:text-xl text-zinc-600 max-w-2xl mx-auto font-normal leading-relaxed">
                        Live fleet visibility, vehicle activity, and operational awareness — all in one command center.
                    </p>

                    <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
                        <Magnetic intensity={reducedMotion ? 0 : 0.3} disabled={reducedMotion}>
                            <Link
                                to="/dashboard"
                                className="inline-flex items-center rounded-full bg-black px-6 py-3 text-sm font-medium text-white hover:bg-zinc-800 transition-all"
                            >
                                View Live Fleet
                            </Link>
                        </Magnetic>
                        <AnimatedBackground
                            defaultValue={null}
                            className="rounded-full bg-zinc-100"
                            transition={{ type: "spring", bounce: 0.2, duration: 0.3 }}
                            enableHover
                        >
                            <a
                                href="#features"
                                data-id="explore"
                                className="inline-flex items-center rounded-full border border-zinc-200 px-6 py-3 text-sm font-medium text-zinc-800"
                            >
                                Explore Platform
                            </a>
                        </AnimatedBackground>
                    </div>
                </section>

                <section id="features" className="max-w-7xl mx-auto px-6 py-16 border-t border-zinc-100">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
                        {[
                            {
                                n: "01",
                                title: "Real-Time Tracking",
                                desc: "See exact vehicle locations and live status across your operations map, with precise route and geofence awareness.",
                            },
                            {
                                n: "02",
                                title: "Advanced Analytics",
                                desc: "Reduce idle time, understand mileage efficiency, and forecast maintenance from a single operational view.",
                            },
                            {
                                n: "03",
                                title: "Seamless Management",
                                desc: "A unified dashboard for fleet managers, dispatchers, and operators — available on any device.",
                            },
                        ].map((item) => (
                            <div key={item.n} className="space-y-3">
                                <div className="w-10 h-10 rounded-lg bg-zinc-100 flex items-center justify-center font-bold text-zinc-900">
                                    {item.n}
                                </div>
                                <h3 className="text-xl font-bold text-zinc-900">{item.title}</h3>
                                <p className="text-zinc-600 text-sm leading-relaxed">{item.desc}</p>
                            </div>
                        ))}
                    </div>
                </section>

                <section id="about" className="max-w-3xl mx-auto px-6 pb-20 text-center">
                    <h2 className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 mb-4">
                        Centralized control for every vehicle.
                    </h2>
                    <p className="text-zinc-600 leading-relaxed mb-8">
                        ATS brings live operations, vehicle insights, and fleet health into one platform — so your team always knows what is moving, idle, or needs attention.
                    </p>
                    <div className="flex flex-wrap items-center justify-center gap-3">
                        <Magnetic intensity={reducedMotion ? 0 : 0.28} disabled={reducedMotion}>
                            <Link
                                to="/dashboard"
                                className="inline-flex items-center rounded-full bg-black px-6 py-3 text-sm font-medium text-white hover:bg-zinc-800"
                            >
                                Open Fleet Dashboard
                            </Link>
                        </Magnetic>
                        <Magnetic intensity={reducedMotion ? 0 : 0.28} disabled={reducedMotion}>
                            <Link
                                to="/tracking"
                                className="inline-flex items-center rounded-full border border-zinc-200 px-6 py-3 text-sm font-medium text-zinc-900 hover:bg-zinc-100"
                            >
                                Go to Live Tracking
                            </Link>
                        </Magnetic>
                    </div>
                </section>
            </main>

            <footer className="border-t border-zinc-200 py-10 bg-zinc-50">
                <div className="max-w-7xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-zinc-500">
                    <div className="flex items-center gap-2">
                        <AtsLogo className="h-7" showSubtitle={false} />
                        <span className="font-semibold text-zinc-800">ATS Advanced Technology Solutions</span>
                    </div>
                    <div className="flex items-center space-x-6">
                        <Link to="/dashboard" className="hover:text-black transition-colors">Dashboard</Link>
                        <Link to="/tracking" className="hover:text-black transition-colors">Tracking</Link>
                        <Link to="/vehicles" className="hover:text-black transition-colors">Vehicles</Link>
                    </div>
                    <div>© {new Date().getFullYear()} ATS. All rights reserved.</div>
                </div>
            </footer>
        </div>
    )
}
