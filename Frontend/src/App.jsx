import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
} from "react-router-dom"
import { lazy, Suspense, useEffect } from "react"

import { AuthProvider } from "./context/AuthContext"
import { FuelFilterProvider } from "./context/FuelFilterContext"
import { useAuth } from "./context/useAuth"
import ProtectedRoute from "./components/Auth/ProtectedRoute"

const Landing = lazy(() => import("./pages/Landing"))
const Login = lazy(() => import("./pages/Login"))

// Operational page chunks, also prefetched after sign-in (see PrefetchOperationalPages)
const operationalPages = {
    dashboard: () => import("./pages/Dashboard"),
    fuel: () => import("./pages/FuelPerformance"),
    tracking: () => import("./pages/Tracking"),
    idle: () => import("./pages/IdleAnalytics"),
    health: () => import("./pages/VehicleHealth"),
    alerts: () => import("./pages/FleetAlerts"),
}

// Main Fleet Dashboard Pages
const Dashboard = lazy(operationalPages.dashboard)
const FuelPerformance = lazy(operationalPages.fuel)
const FuelDailyDashboard = lazy(() => import("./pages/FuelDailyDashboard"))

// Additional Fleet Operations Pages
const Tracking = lazy(operationalPages.tracking)
const IdleAnalytics = lazy(operationalPages.idle)
const VehicleHealth = lazy(operationalPages.health)
const FleetAlerts = lazy(operationalPages.alerts)

const AiAssistantWidget = lazy(() => import("./components/AI/AiAssistantWidget"))

// Once signed in, load the other pages' code in the background so moving between pages never replaces the whole
// screen (navigation included) with the Suspense fallback. Code only: no data is fetched.
function PrefetchOperationalPages() {
    const { isAuthenticated } = useAuth()
    useEffect(() => {
        if (!isAuthenticated) return undefined
        const timer = setTimeout(() => {
            Object.values(operationalPages).forEach(load => { load().catch(() => {}) })
        }, 1500)
        return () => clearTimeout(timer)
    }, [isAuthenticated])
    return null
}

function App() {
    return (
        <AuthProvider>
            <PrefetchOperationalPages />
            <FuelFilterProvider>
                <BrowserRouter>
                    <Suspense fallback={<div role="status" className="grid min-h-dvh place-items-center bg-white text-xs font-semibold text-slate-500">Loading…</div>}>
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
                                element={
                                    <ProtectedRoute>
                                        <FuelDailyDashboard />
                                    </ProtectedRoute>
                                }
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
                            {/* Legacy URLs (/trips, /reports, /drivers, /settings) redirect to the page that serves them, so the
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
                                element={<Navigate to="/dashboard" replace />}
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
                            <Route
                                path="/alerts"
                                element={
                                    <ProtectedRoute>
                                        <FleetAlerts />
                                    </ProtectedRoute>
                                }
                            />
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
