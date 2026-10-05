import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
} from "react-router-dom"
import { lazy, Suspense } from "react"

import { AuthProvider } from "./context/AuthContext"
import ProtectedRoute from "./components/Auth/ProtectedRoute"

const Landing = lazy(() => import("./pages/Landing"))
const Login = lazy(() => import("./pages/Login"))
const Dashboard = lazy(() => import("./pages/Dashboard"))
const Tracking = lazy(() => import("./pages/Tracking"))
const IdleAnalytics = lazy(() => import("./pages/IdleAnalytics"))
const FleetAnalytics = lazy(() => import("./pages/FleetAnalytics"))
const VehicleHealth = lazy(() => import("./pages/VehicleHealth"))
const DriverSafety = lazy(() => import("./pages/DriverSafety"))
const FleetAlerts = lazy(() => import("./pages/FleetAlerts"))

const AiAssistantWidget = lazy(() => import("./components/AI/AiAssistantWidget"))


function App() {
    return (
        <AuthProvider>
            <BrowserRouter>
                <Suspense fallback={<div className="min-h-screen grid place-items-center text-sm text-slate-500">Loading page…</div>}>
                <Routes>
                    {/* Public Routes */}
                    <Route path="/" element={<Landing />} />
                    <Route path="/login" element={<Login />} />

                    {/* Protected Fleet Operations Routes */}
                    <Route
                        path="/dashboard"
                        element={
                            <ProtectedRoute>
                                <Dashboard />
                            </ProtectedRoute>
                        }
                    />
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
                        path="/vehicles"
                        element={
                            <ProtectedRoute>
                                <Dashboard />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/drivers"
                        element={
                            <ProtectedRoute>
                                <Dashboard />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/trips"
                        element={
                            <ProtectedRoute>
                                <Tracking />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/analytics"
                        element={
                            <ProtectedRoute>
                                <FleetAnalytics />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/driver-safety"
                        element={
                            <ProtectedRoute>
                                <DriverSafety />
                            </ProtectedRoute>
                        }
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
                        path="/reports"
                        element={
                            <ProtectedRoute>
                                <IdleAnalytics />
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
                        path="/settings"
                        element={
                            <ProtectedRoute>
                                <Dashboard />
                            </ProtectedRoute>
                        }
                    />

                    {/* Fallback */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
                <AiAssistantWidget />
                </Suspense>
            </BrowserRouter>
        </AuthProvider>
    )
}

export default App
