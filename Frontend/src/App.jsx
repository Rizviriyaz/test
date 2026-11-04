import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
    useLocation,
} from "react-router-dom"
import { AppShellSkeleton } from "./components/Common/Skeleton"
import { lazy, Suspense, useEffect } from "react"

import { AuthProvider } from "./context/AuthContext"
import { FuelFilterProvider } from "./context/FuelFilterContext"
import { useAuth } from "./context/useAuth"
import ProtectedRoute from "./components/Auth/ProtectedRoute"
import { loadVehicleDetailDrawer } from "./components/Vehicles/lazyVehicleDetailDrawer"

const Landing = lazy(() => import("./pages/Landing"))
const Login = lazy(() => import("./pages/Login"))

// Operational page chunks, also prefetched after sign-in (see PrefetchOperationalPages)
const operationalPages = {
    dashboard: () => import("./pages/Dashboard"),
    fuel: () => import("./pages/FuelPerformance"),
    tracking: () => import("./pages/Tracking"),
    idle: () => import("./pages/IdleAnalytics"),
    health: () => import("./pages/VehicleHealth"),
    drivers: () => import("./pages/Drivers"),
}

// Main Fleet Dashboard Pages
const Dashboard = lazy(operationalPages.dashboard)
const FuelPerformance = lazy(operationalPages.fuel)

// Additional Fleet Operations Pages
const Tracking = lazy(operationalPages.tracking)
const IdleAnalytics = lazy(operationalPages.idle)
const VehicleHealth = lazy(operationalPages.health)
const Drivers = lazy(operationalPages.drivers)

const AiAssistantWidget = lazy(() => import("./components/AI/AiAssistantWidget"))

// Once signed in, load the other pages' code in the background so moving between pages never replaces the whole
// screen (navigation included) with the Suspense fallback, and the vehicle drawer opens without waiting for its code.
// Code only: no data is fetched.
function PrefetchOperationalPages() {
    const { isAuthenticated } = useAuth()
    useEffect(() => {
        if (!isAuthenticated) return undefined
        const timer = setTimeout(() => {
            [...Object.values(operationalPages), loadVehicleDetailDrawer].forEach(load => { load().catch(() => {}) })
        }, 1500)
        return () => clearTimeout(timer)
    }, [isAuthenticated])
    return null
}

// While a page's code loads: the operational layout's skeleton (public pages: their plain background)
function PageCodeFallback() {
    const { pathname } = useLocation()
    if (pathname === "/" || pathname === "/login") {
        return <div role="status" aria-label="Loading" className="min-h-dvh bg-white" />
    }
    return <AppShellSkeleton label="Loading page" />
}

function App() {
    return (
        <AuthProvider>
            <PrefetchOperationalPages />
            <FuelFilterProvider>
                <BrowserRouter>
                    <Suspense fallback={<PageCodeFallback />}>
                        <Routes>
                            {/* Public Routes */}
                            <Route path="/" element={<Landing />} />
                            <Route path="/login" element={<Login />} />

                            {/* ─── 1. Fleet Overview (Main Product Page) ─────────── */}
                            <Route
                                path="/dashboard"
                                element={
                                    <ProtectedRoute>
                                        <Dashboard />
                                    </ProtectedRoute>
                                }
                            />
                            <Route
                                path="/overview"
                                element={<Navigate to="/dashboard" replace />}
                            />

                            {/* ─── 2. Fuel & Performance (Consolidated Fuel Page) ──── */}
                            <Route
                                path="/fuel"
                                element={
                                    <ProtectedRoute>
                                        <FuelPerformance />
                                    </ProtectedRoute>
                                }
                            />
                            <Route
                                path="/fuel/daily"
                                element={<Navigate to="/fuel" replace />}
                            />
                            <Route
                                path="/fuel/ranking"
                                element={<Navigate to="/fuel" replace />}
                            />
                            <Route
                                path="/ranking"
                                element={<Navigate to="/fuel" replace />}
                            />
                            <Route
                                path="/fuel/vehicles"
                                element={<Navigate to="/fuel" replace />}
                            />
                            <Route
                                path="/vehicles"
                                element={<Navigate to="/dashboard" replace />}
                            />
                            <Route
                                path="/fuel/import"
                                element={<Navigate to="/fuel" replace />}
                            />
                            <Route
                                path="/import"
                                element={<Navigate to="/fuel" replace />}
                            />

                            {/* ─── Preserved Operations Routes ────────────────────── */}
                            {/* Legacy URLs (/trips, /reports, /settings) redirect to the page that serves them, so the
                                address bar never names a page that is not shown; the target route keeps its own protection */}
                            <Route
                                path="/tracking"
                                element={
                                    <ProtectedRoute>
                                        <Tracking />
                                    </ProtectedRoute>
                                }
                            />
                            <Route
                                path="/idle-analytics"
                                element={
                                    <ProtectedRoute>
                                        <IdleAnalytics />
                                    </ProtectedRoute>
                                }
                            />
                            <Route
                                path="/drivers"
                                element={
                                    <ProtectedRoute>
                                        <Drivers />
                                    </ProtectedRoute>
                                }
                            />
                            <Route
                                path="/trips"
                                element={<Navigate to="/tracking" replace />}
                            />
                            <Route
                                path="/vehicle-health"
                                element={
                                    <ProtectedRoute>
                                        <VehicleHealth />
                                    </ProtectedRoute>
                                }
                            />
                            <Route path="/alerts" element={<Navigate to="/dashboard" replace />} />
                            <Route
                                path="/reports"
                                element={<Navigate to="/idle-analytics" replace />}
                            />
                            <Route
                                path="/settings"
                                element={<Navigate to="/dashboard" replace />}
                            />

                            {/* Fallback */}
                            <Route path="*" element={<Navigate to="/" replace />} />
                        </Routes>
                        <AiAssistantWidget />
                    </Suspense>
                </BrowserRouter>
            </FuelFilterProvider>
        </AuthProvider>
    )
}

export default App
