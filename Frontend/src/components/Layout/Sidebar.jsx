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
    PanelLeft,
    LogOut,
} from "lucide-react"

import { NavLink, Link, useNavigate } from "react-router-dom"
import AtsLogo from "../../assets/AtsLogo"
import { useAuth } from "../../context/useAuth"
import { SidebarContext } from "./sidebar-context"


// ─── Sidebar context (kept for compatibility) ─────────────────────────────────

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

    const handleLogout = async () => {
        await logout()
        navigate("/login")
    }

    const collapsed = !expanded

    return (
        <aside
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            className="flex h-screen shrink-0 flex-col bg-white overflow-hidden z-50"
            style={{
                width: collapsed ? "56px" : "240px",
                minWidth: collapsed ? "56px" : "240px",
                transition: "width 0.22s cubic-bezier(0.4,0,0.2,1), min-width 0.22s cubic-bezier(0.4,0,0.2,1)",
                boxShadow: expanded ? "2px 0 16px 0 rgba(0,0,0,0.08)" : "1px 0 0 0 #e2e8f0",
            }}
        >

            {/* Logo / Icon strip */}
            <div
                className="flex h-14 items-center border-b border-slate-100 bg-white shrink-0 overflow-hidden"
                style={{ padding: collapsed ? "0 14px" : "0 14px" }}
            >
                {collapsed ? (
                    /* Collapsed: show just the icon acting as the hover trigger */
                    <PanelLeft className="h-5 w-5 text-slate-400" />
                ) : (
                    /* Expanded: show logo */
                    <Link to="/" className="flex items-center no-underline min-w-0 overflow-hidden">
                        <AtsLogo className="h-8 max-h-8 w-auto max-w-[200px]" />
                    </Link>
                )}
            </div>


            {/* Navigation */}
            <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-3">
                {navigation.map((group, groupIndex) => (
                    <div key={groupIndex} className={groupIndex !== 0 ? "mt-4" : ""}>
                        {group.section && !collapsed && (
                            <p className="mb-1 px-2 text-[10px] font-bold tracking-wider text-slate-400 whitespace-nowrap">
                                {group.section}
                            </p>
                        )}

                        <div className="space-y-0.5">
                            {group.items.map((item) => {
                                const Icon = item.icon
                                if (item.isAction) {
                                    return (
                                        <button
                                            key={item.name}
                                            type="button"
                                            onClick={() => window.dispatchEvent(new CustomEvent("open-ai-assistant"))}
                                            title={collapsed ? item.name : undefined}
                                            className={`flex w-full items-center rounded-xl py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50 hover:text-slate-900 transition-colors cursor-pointer ${
                                                collapsed ? "justify-center px-0" : "gap-2.5 px-2.5"
                                            }`}
                                        >
                                            <Icon className="h-4 w-4 shrink-0 text-[#33758F]" />
                                            {!collapsed && (
                                                <span className="whitespace-nowrap font-bold text-[#33758F]">
                                                    {item.name}
                                                </span>
                                            )}
                                        </button>
                                    )
                                }

                                return (
                                    <NavLink
                                        key={item.name}
                                        to={item.path}
                                        title={collapsed ? item.name : undefined}
                                        className={({ isActive }) =>
                                            `flex w-full items-center rounded-xl py-2 text-xs font-semibold transition-colors ${
                                                collapsed ? "justify-center px-0" : "gap-2.5 px-2.5"
                                            } ${
                                                isActive
                                                    ? "bg-[#33758F] text-white shadow-xs"
                                                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                                            }`
                                        }
                                    >
                                        <Icon className="h-4 w-4 shrink-0" />
                                        {!collapsed && (
                                            <span className="whitespace-nowrap">{item.name}</span>
                                        )}
                                    </NavLink>
                                )
                            })}
                        </div>
                    </div>
                ))}
            </nav>


            {/* Bottom — User profile & Settings */}
            <div className="border-t border-slate-100 p-2 bg-white shrink-0 space-y-1">
                <NavLink
                    to="/settings"
                    title={collapsed ? "Settings" : undefined}
                    className={({ isActive }) =>
                        `flex w-full items-center rounded-xl py-2 text-xs font-semibold transition-colors ${
                            collapsed ? "justify-center px-0" : "gap-2.5 px-2.5"
                        } ${
                            isActive
                                ? "bg-[#33758F] text-white shadow-xs"
                                : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                        }`
                    }
                >
                    <Settings className="h-4 w-4 shrink-0" />
                    {!collapsed && <span>Settings</span>}
                </NavLink>

                {user && (
                    <button
                        type="button"
                        onClick={handleLogout}
                        title={collapsed ? `Sign Out (${profile?.full_name || user.email})` : undefined}
                        className={`flex w-full items-center rounded-xl py-2 text-xs font-semibold text-rose-600 hover:bg-rose-50 transition-colors cursor-pointer ${
                            collapsed ? "justify-center px-0" : "gap-2.5 px-2.5"
                        }`}
                    >
                        <LogOut className="h-4 w-4 shrink-0" />
                        {!collapsed && (
                            <div className="flex flex-col items-start min-w-0 text-left">
                                <span className="font-semibold text-rose-700">Sign Out</span>
                                <span className="text-[10px] text-slate-400 truncate max-w-[150px]">
                                    {profile?.full_name || user.email}
                                </span>
                            </div>
                        )}
                    </button>
                )}
            </div>

        </aside>
    )
}
