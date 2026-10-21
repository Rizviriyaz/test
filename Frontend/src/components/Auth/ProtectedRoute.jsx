import { Navigate, useLocation } from "react-router-dom"
import { useAuth } from "../../context/useAuth"
import AtsLogo from "../../assets/AtsLogo"
import { Loader2 } from "lucide-react"

export default function ProtectedRoute({ children }) {
    const { isAuthenticated, loading } = useAuth()
    const location = useLocation()

    if (loading) {
        return (
            <div role="status" className="flex h-dvh w-full flex-col items-center justify-center bg-white">
                <div className="flex flex-col items-center gap-4">
                    <AtsLogo className="h-10 max-h-10" />
                    <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
                        <Loader2 className="h-4 w-4 animate-spin text-brand" />
                        <span>Verifying session…</span>
                    </div>
                </div>
            </div>
        )
    }

    if (!isAuthenticated) {
        return <Navigate to="/login" state={{ from: location }} replace />
    }

    return children
}
