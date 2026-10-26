import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
} from "react-router-dom"
import { lazy, Suspense } from "react"

import { AuthProvider } from "./context/AuthContext"
import { FuelFilterProvider } from "./context/FuelFilterContext"
import ProtectedRoute from "./components/Auth/ProtectedRoute"

const Landing = lazy(() => import("./pages/Landing"))
const Login = lazy(() => import("./pages/Login"))

// Main Fleet Dashboard Pages
const Dashboard = lazy(() => import("./pages/Dashboard"))
const FuelPerformance = lazy(() => import("./pages/FuelPerformance"))

// Additional Fleet Operations Pages
const Tracking = lazy(() => import("./pages/Tracking"))
const IdleAnalytics = lazy(() => import("./pages/IdleAnalytics"))
const VehicleHealth = lazy(() => import("./pages/VehicleHealth"))
const FleetAlerts = lazy(() => import("./pages/FleetAlerts"))

const AiAssistantWidget = lazy(() => import("./components/AI/AiAssistantWidget"))

function App() {
    return (
        <AuthProvider>
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
