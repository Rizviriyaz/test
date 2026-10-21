import { useState, useEffect } from "react"
import { Link, useNavigate, useLocation } from "react-router-dom"
import { useAuth } from "../context/useAuth"
import AtsLogo from "../assets/AtsLogo"
import {
    Mail,
    Lock,
    Eye,
    EyeOff,
    Loader2,
    ArrowRight,
    ShieldCheck,
    AlertCircle,
    CheckCircle2,
} from "lucide-react"

export default function Login() {
    const { login, isAuthenticated } = useAuth()
    const navigate = useNavigate()
    const location = useLocation()

    const from = location.state?.from?.pathname || "/dashboard"

    const [email, setEmail] = useState("")
    const [password, setPassword] = useState("")
    const [showPassword, setShowPassword] = useState(false)
    const [rememberMe, setRememberMe] = useState(true)

    const [submitting, setSubmitting] = useState(false)
    const [error, setError] = useState("")
    const [successMessage, setSuccessMessage] = useState("")

    // If already authenticated, redirect to destination
    useEffect(() => {
        if (isAuthenticated) {
            navigate(from, { replace: true })
        }
    }, [isAuthenticated, navigate, from])

    const handleSubmit = async (e) => {
        e?.preventDefault()
        setError("")
        setSuccessMessage("")

        if (!email.trim()) {
            setError("Please enter your email or username.")
            return
        }

        if (!password) {
            setError("Please enter your password.")
            return
        }

        setSubmitting(true)
        try {
            await login(email, password, rememberMe)
            setSuccessMessage("Authentication successful. Redirecting...")
            setTimeout(() => {
                navigate(from, { replace: true })
            }, 300)
        } catch (err) {
            setError(err.message || "Failed to sign in. Please verify your credentials.")
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <div className="bg-white min-h-screen flex flex-col justify-between selection:bg-black selection:text-white text-zinc-900 font-sans overflow-x-hidden">
            {/* Subtle background grid pattern */}
            <div
                aria-hidden
                className="pointer-events-none fixed inset-0 opacity-[0.025]"
                style={{
                    backgroundImage: `linear-gradient(rgba(0,0,0,0.6) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.6) 1px, transparent 1px)`,
                    backgroundSize: "56px 56px",
                }}
            />

            {/* Top Navigation — matches Landing */}
            <header className="w-full max-w-7xl mx-auto px-6 py-6 flex items-center justify-between z-10 relative">
                <Link to="/" className="inline-flex items-center transition-opacity hover:opacity-80">
                    <AtsLogo className="h-9 sm:h-10 max-h-10" />
                </Link>
                <Link
                    to="/"
                    className="text-xs font-semibold text-zinc-700 hover:text-black px-3.5 py-2 rounded-full border border-zinc-200 hover:border-zinc-300 transition-all"
                >
                    Back to Portal
                </Link>
            </header>

            {/* Main Login Card Section */}
            <main className="flex-1 flex items-center justify-center px-4 py-10 z-10 relative">
                <div className="w-full max-w-md">
                    {/* Card container */}
                    <div className="rounded-3xl border border-zinc-200 bg-white shadow-xl shadow-zinc-900/5 p-7 sm:p-9 relative overflow-hidden">
                        {/* Subtle top accent */}
                        <div className="absolute top-0 inset-x-0 h-0.5 bg-gradient-to-r from-transparent via-zinc-300 to-transparent" />

                        {/* Title & Subtitle */}
                        <div className="mb-7 text-center sm:text-left">
                            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-zinc-200 bg-zinc-100 text-zinc-600 text-xs font-semibold mb-3">
                                <ShieldCheck className="h-3.5 w-3.5" />
                                <span>Fleet Operations Access</span>
                            </div>
                            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900">
                                Sign in to ATS Fleet
                            </h1>
                            <p className="mt-1.5 text-xs sm:text-sm text-zinc-500">
                                Enter your credentials to access the command center.
                            </p>
                        </div>

                        {/* Error Banner */}
                        {error && (
                            <div className="mb-5 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3.5 text-xs text-red-700 animate-in fade-in">
                                <AlertCircle className="h-4 w-4 text-red-500 shrink-0 mt-0.5" />
                                <div className="flex-1">
                                    <p className="font-semibold text-red-800">Authentication Failed</p>
                                    <p className="mt-0.5 leading-relaxed">{error}</p>
                                </div>
                            </div>
                        )}

                        {/* Success Banner */}
                        {successMessage && (
                            <div className="mb-5 flex items-center gap-2.5 rounded-lg border border-emerald-200 bg-emerald-50 p-3.5 text-xs text-emerald-700">
                                <CheckCircle2 className="h-4 w-4 text-emerald-500 shrink-0" />
                                <span>{successMessage}</span>
                            </div>
                        )}

                        {/* Login Form */}
                        <form onSubmit={handleSubmit} className="space-y-4">
                            {/* Email / Username */}
                            <div>
                                <label
                                    htmlFor="email"
                                    className="block text-xs font-semibold text-zinc-500 mb-1.5"
                                >
                                    Email or Username
                                </label>
                                <div className="relative">
                                    <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-zinc-400">
                                        <Mail className="h-4 w-4" />
                                    </div>
                                    <input
                                        id="email"
                                        type="text"
                                        autoComplete="email"
                                        autoFocus
                                        required
                                        value={email}
                                        onChange={(e) => setEmail(e.target.value)}
                                        placeholder="Enter your email"
                                        disabled={submitting}
                                        className="w-full rounded-lg border border-zinc-200 bg-zinc-50 pl-10 pr-4 py-2.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 transition disabled:opacity-50"
                                    />
                                </div>
                            </div>

                            {/* Password */}
                            <div>
                                <div className="flex items-center justify-between mb-1.5">
                                    <label
                                        htmlFor="password"
                                        className="block text-xs font-semibold text-zinc-500"
                                    >
                                        Password
                                    </label>
                                    <span className="text-[11px] text-zinc-400 hover:text-zinc-700 cursor-pointer transition">
                                        Forgot?
                                    </span>
                                </div>
                                <div className="relative">
                                    <div className="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-zinc-400">
                                        <Lock className="h-4 w-4" />
                                    </div>
                                    <input
                                        id="password"
                                        type={showPassword ? "text" : "password"}
                                        autoComplete="current-password"
                                        required
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        placeholder="••••••••"
                                        disabled={submitting}
                                        className="w-full rounded-lg border border-zinc-200 bg-zinc-50 pl-10 pr-10 py-2.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-900 focus:outline-none focus:ring-2 focus:ring-zinc-900/10 transition disabled:opacity-50"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPassword(!showPassword)}
                                        aria-label={showPassword ? "Hide password" : "Show password"}
                                        className="absolute inset-y-0 right-0 pr-3.5 flex items-center text-zinc-400 hover:text-zinc-700 transition"
                                    >
                                        {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                    </button>
                                </div>
                            </div>

                            {/* Remember me */}
                            <div className="flex items-center justify-between pt-1">
                                <label className="flex items-center gap-2 cursor-pointer text-xs text-zinc-600">
                                    <input
                                        type="checkbox"
                                        checked={rememberMe}
                                        onChange={(e) => setRememberMe(e.target.checked)}
                                        className="rounded border-zinc-300 bg-white text-zinc-900 focus:ring-zinc-900 focus:ring-offset-white"
                                    />
                                    <span>Keep me signed in</span>
                                </label>
                            </div>

                            {/* Submit Button — matches Landing "Launch Dashboard" style */}
                            <button
                                type="submit"
                                disabled={submitting}
                                className="w-full mt-2 flex items-center justify-center gap-2 rounded-full bg-black hover:bg-zinc-800 text-white py-3 px-4 text-sm font-medium shadow-sm hover:shadow-md transition-all disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer"
                            >
                                {submitting ? (
                                    <>
                                        <Loader2 className="h-4 w-4 animate-spin" />
                                        <span>Authenticating...</span>
                                    </>
                                ) : (
                                    <>
                                        <span>Sign In to Dashboard</span>
                                        <ArrowRight className="h-4 w-4" />
                                    </>
                                )}
                            </button>
                        </form>
                    </div>

                    {/* Footer Security Badge */}
                    <div className="mt-6 text-center text-xs text-zinc-400 flex items-center justify-center gap-2">
                        <ShieldCheck className="h-3.5 w-3.5 text-zinc-400" />
                        <span>ATS Fleet Security · Encrypted Connection</span>
                    </div>
                </div>
            </main>

            {/* Bottom copyright */}
            <footer className="w-full max-w-7xl mx-auto px-6 py-4 text-center text-xs text-zinc-400 z-10 relative">
                © {new Date().getFullYear()} Advanced Technology Solutions. All rights reserved.
            </footer>
        </div>
    )
}
