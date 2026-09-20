import { Navigate, useLocation } from "react-router-dom"
import { useAuth } from "../../context/AuthContext"
import { AtsIcon } from "../../assets/AtsLogo"
import { Loader2 } from "lucide-react"

export default function ProtectedRoute({ children }) {
    const { isAuthenticated, loading } = useAuth()
    const location = useLocation()

    if (loading) {
        return (
            <div className="flex h-screen w-screen flex-col items-center justify-center bg-slate-900 text-white">
                <div className="flex flex-col items-center gap-4">
                    <div className="relative flex items-center justify-center">
                        <AtsIcon className="h-12 w-12 text-[#38bdf8] animate-pulse" />
                    </div>
                    <div className="flex items-center gap-2 text-sm font-semibold text-slate-300">
                        <Loader2 className="h-4 w-4 animate-spin text-[#38bdf8]" />
                        <span>Verifying fleet session...</span>
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
