import { useState, useRef } from "react"
import {
    LayoutDashboard,
    Fuel,
    Map,
    Users,
    Route,
    HeartPulse,
    FileText,
    Bell,
    Settings,
    Clock3,
    LogOut,
} from "lucide-react"

import { NavLink, Link, useNavigate } from "react-router-dom"
import AtsLogo from "../../assets/AtsLogo"
import { useAuth } from "../../context/useAuth"
import { SidebarContext } from "./sidebar-context"


// ─── Sidebar context (kept for backwards compatibility) ───────────────────────

export function SidebarProvider({ children }) {
    return (
        <SidebarContext.Provider value={{ collapsed: true }}>
            {children}
        </SidebarContext.Provider>
    )
}


// ─── Navigation config ────────────────────────────────────────────────────────

const navigation = [
    {
        section: null,
        items: [
            { name: "Fleet Overview",     icon: LayoutDashboard, path: "/dashboard" },
            { name: "Fuel & Performance", icon: Fuel,            path: "/fuel" },
        ],
    },
    {
        section: "OPERATIONS",
        items: [
            { name: "Live Tracking",   icon: Map,        path: "/tracking" },
            { name: "Idle Analytics",  icon: Clock3,     path: "/idle-analytics" },
            { name: "Vehicle Health",  icon: HeartPulse, path: "/vehicle-health" },
            { name: "Alerts",          icon: Bell,       path: "/alerts" },
        ],
    },
]


// ─── Sidebar component ────────────────────────────────────────────────────────

export default function Sidebar() {
    const [expanded, setExpanded] = useState(false)
    const leaveTimer = useRef(null)
    const { user, profile, logout } = useAuth()
    const navigate = useNavigate()

    const handleMouseEnter = () => {
        if (leaveTimer.current) {
            clearTimeout(leaveTimer.current)
            leaveTimer.current = null
        }
        setExpanded(true)
    }

    const handleMouseLeave = () => {
        leaveTimer.current = setTimeout(() => {
            setExpanded(false)
        }, 120)
    }

    const handleFocusCapture = () => {
        if (leaveTimer.current) {
            clearTimeout(leaveTimer.current)
            leaveTimer.current = null
        }
        setExpanded(true)
    }

    const handleBlurCapture = (e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) {
            setExpanded(false)
        }
    }

    const handleLogout = async () => {
        await logout()
        navigate("/login")
    }

    return (
        <>
            {/* ── 1. Document-flow Layout Spacer ─────────────────────────────────
                Fixed-width gutter in the flex tree to ensure the main page content
                (dashboard, tables, charts, maps) NEVER shifts horizontally when
                the floating sidebar expands or collapses on hover.
            */}
            <div
                className="shrink-0 select-none pointer-events-none transition-none"
                style={{ width: "74px" }}
                aria-hidden="true"
            />

            {/* ── 2. Mobile Backdrop (Touch overlay to dismiss when expanded) ─── */}
            {expanded && (
                <div
                    onClick={() => setExpanded(false)}
                    className="fixed inset-0 z-30 bg-slate-900/20 backdrop-blur-xs md:hidden"
                    aria-hidden="true"
                />
            )}

            {/* ── 3. Floating Sidebar Panel ───────────────────────────────────── */}
            <aside
                onMouseEnter={handleMouseEnter}
                onMouseLeave={handleMouseLeave}
                onFocusCapture={handleFocusCapture}
                onBlurCapture={handleBlurCapture}
                aria-expanded={expanded}
                aria-label="Main Navigation"
                className={`fixed left-3 top-3 bottom-3 z-40 flex flex-col bg-white/95 backdrop-blur-md rounded-2xl border border-slate-200/90 select-none overflow-hidden motion-reduce:transition-none transition-[width,box-shadow,border-color] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                    expanded
                        ? "shadow-[0_20px_45px_-10px_rgba(15,23,42,0.18),0_4px_16px_0_rgba(15,23,42,0.06)] border-slate-300/80"
                        : "shadow-[0_4px_20px_-2px_rgba(15,23,42,0.06),0_1px_3px_0_rgba(15,23,42,0.04)]"
                }`}
                style={{
                    width: expanded ? "244px" : "58px",
                }}
            >
                {/* ── Top Brand Header (Morphs Favicon ↔ Full Logo) ──────────── */}
                <div className="flex h-14 items-center border-b border-slate-100/90 px-2 shrink-0 overflow-hidden">
                    <Link
                        to="/"
                        className="group relative flex h-10 w-full items-center rounded-xl p-0 transition-colors hover:bg-slate-50/80 focus-visible:ring-2 focus-visible:ring-[#33758F] focus-visible:outline-none overflow-hidden"
                        title="ATS Fleet Management"
                        aria-label="ATS Fleet Management Home"
                    >
                        {/* State 1: Favicon Emblem (Visible ONLY when collapsed) */}
                        <div
                            className={`absolute left-0 top-0 flex h-10 w-10 items-center justify-center transition-all duration-200 ease-out ${
                                expanded
                                    ? "opacity-0 scale-75 pointer-events-none"
                                    : "opacity-100 scale-100"
                            }`}
                        >
                            <img
                                src="/ATS-Favicon.png"
                                alt="ATS"
                                className="h-7 w-7 object-contain transition-transform duration-200 group-hover:scale-105"
                            />
                        </div>

                        {/* State 2: Full ATS Logo (Visible ONLY when expanded on hover) */}
                        <div
                            className={`flex items-center h-full px-2.5 transition-all duration-200 ease-out ${
                                expanded
                                    ? "opacity-100 scale-100 translate-x-0"
                                    : "opacity-0 scale-95 -translate-x-2 pointer-events-none"
                            }`}
                        >
                            <AtsLogo className="h-7 max-h-7 w-auto max-w-[190px] object-contain" />
                        </div>
                    </Link>
                </div>

                {/* ── Navigation Links ────────────────────────────────────────── */}
                <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-2.5 space-y-2.5 scrollbar-thin">
                    {navigation.map((group, groupIndex) => (
                        <div key={groupIndex} className="space-y-0.5">
                            {/* Section Divider / Label */}
                            {group.section && (
                                <div className="overflow-hidden">
                                    {expanded ? (
                                        <p className="px-3 pt-2 pb-1 text-[10px] font-bold tracking-wider text-slate-400 uppercase whitespace-nowrap transition-opacity duration-200">
                                            {group.section}
                                        </p>
                                    ) : (
                                        <div className="my-2 mx-1 border-t border-slate-100" />
                                    )}
                                </div>
                            )}

                            {/* Section Items */}
                            <div className="space-y-1">
                                {group.items.map((item) => {
                                    const Icon = item.icon
                                    if (item.isAction) {
                                        return (
                                            <button
                                                key={item.name}
                                                type="button"
                                                onClick={() => window.dispatchEvent(new CustomEvent("open-ai-assistant"))}
                                                title={!expanded ? item.name : undefined}
                                                aria-label={item.name}
                                                className="group relative flex h-10 w-full items-center rounded-xl transition-all duration-150 cursor-pointer text-slate-600 hover:bg-slate-100/80 hover:text-slate-900 active:bg-slate-200/60 focus-visible:ring-2 focus-visible:ring-[#33758F] focus-visible:outline-none"
                                            >
                                                {/* Stationary Icon Container */}
                                                <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                                                    <Icon className="h-4.5 w-4.5 text-[#33758F] transition-transform duration-150 group-hover:scale-105" />
                                                </span>

                                                {/* Label */}
                                                <span
                                                    className={`overflow-hidden text-xs font-semibold whitespace-nowrap text-[#33758F] transition-all duration-200 ease-out pl-0.5 ${
                                                        expanded
                                                            ? "max-w-[170px] opacity-100 translate-x-0"
                                                            : "max-w-0 opacity-0 -translate-x-2 pointer-events-none"
                                                    }`}
                                                >
                                                    {item.name}
                                                </span>
                                            </button>
                                        )
                                    }

                                    return (
                                        <NavLink
                                            key={item.name}
                                            to={item.path}
                                            title={!expanded ? item.name : undefined}
                                            aria-label={item.name}
                                            className={({ isActive }) =>
                                                `group relative flex h-10 w-full items-center rounded-xl transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-[#33758F] focus-visible:outline-none ${
                                                    isActive
                                                        ? "bg-[#33758F] text-white shadow-xs font-semibold"
                                                        : "text-slate-600 hover:bg-slate-100/80 hover:text-slate-900 active:bg-slate-200/60"
                                                }`
                                            }
                                        >
                                            {/* Stationary Icon Container (Identical X-position) */}
                                            <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                                                <Icon className="h-4.5 w-4.5 transition-transform duration-150 group-hover:scale-105" />
                                            </span>

                                            {/* Smooth Label Reveal */}
                                            <span
                                                className={`overflow-hidden text-xs whitespace-nowrap transition-all duration-200 ease-out pl-0.5 ${
                                                    expanded
                                                        ? "max-w-[170px] opacity-100 translate-x-0"
                                                        : "max-w-0 opacity-0 -translate-x-2 pointer-events-none"
                                                }`}
                                            >
                                                {item.name}
                                            </span>
                                        </NavLink>
                                    )
                                })}
                            </div>
                        </div>
                    ))}
                </nav>

                {/* ── Bottom Controls: Settings & User Profile ─────────────────── */}
                <div className="border-t border-slate-100/90 p-2 bg-slate-50/40 shrink-0 space-y-1">
                    {/* Settings Navigation */}
                    <NavLink
                        to="/settings"
                        title={!expanded ? "Settings" : undefined}
                        aria-label="Settings"
                        className={({ isActive }) =>
                            `group relative flex h-10 w-full items-center rounded-xl transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-[#33758F] focus-visible:outline-none ${
                                isActive
                                    ? "bg-[#33758F] text-white shadow-xs font-semibold"
                                    : "text-slate-600 hover:bg-slate-100/80 hover:text-slate-900 active:bg-slate-200/60"
                            }`
                        }
                    >
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                            <Settings className="h-4.5 w-4.5 transition-transform duration-150 group-hover:scale-105" />
                        </span>
                        <span
                            className={`overflow-hidden text-xs whitespace-nowrap transition-all duration-200 ease-out pl-0.5 ${
                                expanded
                                    ? "max-w-[170px] opacity-100 translate-x-0"
                                    : "max-w-0 opacity-0 -translate-x-2 pointer-events-none"
                            }`}
                        >
                            Settings
                        </span>
                    </NavLink>

                    {/* User Profile / Logout */}
                    {user && (
                        <button
                            type="button"
                            onClick={handleLogout}
                            title={!expanded ? `Sign Out (${profile?.full_name || user.email})` : undefined}
                            aria-label={`Sign Out (${profile?.full_name || user.email})`}
                            className="group relative flex h-10 w-full items-center rounded-xl text-rose-600 hover:bg-rose-50/80 active:bg-rose-100/60 transition-colors duration-150 cursor-pointer focus-visible:ring-2 focus-visible:ring-rose-400 focus-visible:outline-none"
                        >
                            <span className="flex h-10 w-10 shrink-0 items-center justify-center text-rose-600">
                                <LogOut className="h-4.5 w-4.5 transition-transform duration-150 group-hover:scale-105" />
                            </span>
                            <div
                                className={`flex flex-col items-start min-w-0 text-left transition-all duration-200 ease-out pl-0.5 overflow-hidden ${
                                    expanded
                                        ? "max-w-[170px] opacity-100 translate-x-0"
                                        : "max-w-0 opacity-0 -translate-x-2 pointer-events-none"
                                }`}
                            >
                                <span className="font-semibold text-rose-700 text-xs leading-tight whitespace-nowrap">
                                    Sign Out
                                </span>
                                <span className="text-[10px] text-slate-400 truncate max-w-[145px] leading-tight">
                                    {profile?.full_name || user.email}
                                </span>
                            </div>
                        </button>
                    )}
                </div>
            </aside>
        </>
    )
}