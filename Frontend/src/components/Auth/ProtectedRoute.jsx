import { Navigate, useLocation } from "react-router-dom"
import { useAuth } from "../../context/useAuth"
import { AppShellSkeleton } from "../Common/Skeleton"

export default function ProtectedRoute({ children }) {
    const { isAuthenticated, loading } = useAuth()
    const location = useLocation()

    // While the session is verified: the layout's skeleton only (no page, no data), so the screen is never blank
    if (loading) {
        return <AppShellSkeleton label="Verifying session…" />
    }

    if (!isAuthenticated) {
        return <Navigate to="/login" state={{ from: location }} replace />
    }

    return children
}
