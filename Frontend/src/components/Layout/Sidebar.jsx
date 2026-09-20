import { createContext, useContext, useState, useRef } from "react"
import {
    LayoutDashboard,
    Map,
    Truck,
    Users,
    Route,
    BarChart3,
    ShieldAlert,
    HeartPulse,
    FileText,
    Bell,
    Settings,
    Clock3,
    PanelLeft,
    LogOut,
    User,
} from "lucide-react"

import { NavLink, Link, useNavigate } from "react-router-dom"
import { AtsIcon } from "../../assets/AtsLogo"
import { useAuth } from "../../context/AuthContext"


// ─── Sidebar context (kept for compatibility) ─────────────────────────────────

const SidebarContext = createContext({ collapsed: true })

export function SidebarProvider({ children }) {
    return (
        <SidebarContext.Provider value={{ collapsed: true }}>
            {children}
        </SidebarContext.Provider>
    )
}

export function useSidebar() {
    return useContext(SidebarContext)
}


// ─── Navigation config ────────────────────────────────────────────────────────

const navigation = [
    {
        section: null,
        items: [
            { name: "Dashboard",    icon: LayoutDashboard, path: "/dashboard" },
        ],
    },
    {
        section: "OPERATIONS",
        items: [
            { name: "Live Tracking", icon: Map,      path: "/tracking"  },
            { name: "Vehicles",      icon: Truck,    path: "/vehicles"  },
            { name: "Drivers",       icon: Users,    path: "/drivers"   },
            { name: "Trips",         icon: Route,    path: "/trips"     },
        ],
    },
    {
        section: "ANALYTICS",
        items: [
            { name: "Fleet Analytics", icon: BarChart3,  path: "/analytics"      },
            { name: "Idle Analytics",  icon: Clock3,     path: "/idle-analytics" },
            { name: "Driver Safety",   icon: ShieldAlert, path: "/driver-safety" },
            { name: "Vehicle Health",  icon: HeartPulse, path: "/vehicle-health" },
        ],
    },
    {
        section: "REPORTING",
        items: [
            { name: "Reports", icon: FileText, path: "/reports" },
            { name: "Alerts",  icon: Bell,     path: "/alerts"  },
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
                    /* Expanded: show logo + brand */
                    <Link to="/" className="flex items-center gap-2.5 no-underline min-w-0 overflow-hidden">
                        <AtsIcon className="h-8 w-8 shrink-0 drop-shadow-xs" />
                        <div className="min-w-0">
                            <p className="text-sm font-black tracking-tight text-[#1C5E7A] leading-none whitespace-nowrap">
                                ATS FLEET
                            </p>
                            <p className="text-[9px] font-bold tracking-wider text-slate-400 uppercase mt-0.5 whitespace-nowrap">
                                Fleet Intelligence
                            </p>
                        </div>
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
                                                    ? "bg-[#1C5E7A] text-white shadow-xs"
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
                                ? "bg-[#1C5E7A] text-white shadow-xs"
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
