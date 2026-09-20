import {
    BrowserRouter,
    Routes,
    Route,
    Navigate,
} from "react-router-dom"

import { AuthProvider } from "./context/AuthContext"
import ProtectedRoute from "./components/Auth/ProtectedRoute"

import Landing from "./pages/Landing"
import Login from "./pages/Login"
import Dashboard from "./pages/Dashboard"
import Tracking from "./pages/Tracking"
import IdleAnalytics from "./pages/IdleAnalytics"


function App() {
    return (
        <AuthProvider>
            <BrowserRouter>
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
                                <Dashboard />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/driver-safety"
                        element={
                            <ProtectedRoute>
                                <IdleAnalytics />
                            </ProtectedRoute>
                        }
                    />
                    <Route
                        path="/vehicle-health"
                        element={
                            <ProtectedRoute>
                                <Dashboard />
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
                                <IdleAnalytics />
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
            </BrowserRouter>
        </AuthProvider>
    )
}

export default App
