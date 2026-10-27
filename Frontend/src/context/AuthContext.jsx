import { useEffect, useRef, useState } from "react"
import { supabase, isSupabaseConfigured } from "../lib/supabase"
import { AuthContext } from "./auth-context"
import { clearClientCache } from "../api/fleetApi"

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null)
    const [profile, setProfile] = useState(null)
    const [loading, setLoading] = useState(true)
    // Id of the user currently signed in: a profile request that answers after a sign-out or a different sign-in
    // is dropped, so one user's role is never applied to another session
    const activeUserIdRef = useRef(null)

    // ─── Initial Session Load ───────────────────────────────────────────────
    useEffect(() => {
        let mounted = true

        const initializeAuth = async () => {
            try {
                if (isSupabaseConfigured() && supabase) {
                    const { data: { session }, error } = await supabase.auth.getSession()
                    if (error) {
                        console.warn("Supabase session get error:", error.message)
                    }

                    if (mounted && session?.user) {
                        activeUserIdRef.current = session.user.id
                        setUser(session.user)
                        // The profile loads in the background: pages render as soon as the session is known. Until
                        // it arrives `profile` is null, so role-gated UI (fuel import) stays hidden; the backend
                        // enforces roles on every request regardless.
                        const stillCurrent = () => mounted && activeUserIdRef.current === session.user.id
                        void (async () => { try {
                            const { data: profileData } = await supabase
                                .from("profiles")
                                .select("*")
                                .eq("id", session.user.id)
                                .single()
                            if (profileData && stillCurrent()) {
                                setProfile(profileData)
                            } else if (stillCurrent()) {
                                setProfile({
                                    id: session.user.id,
                                    email: session.user.email,
                                    full_name: session.user.user_metadata?.full_name || session.user.email?.split("@")[0] || "Fleet Manager",
                                    role: "viewer",
                                })
                            }
                        } catch {
                            if (stillCurrent()) {
                                setProfile({
                                    id: session.user.id,
                                    email: session.user.email,
                                    full_name: session.user.email?.split("@")[0] || "Fleet Manager",
                                    role: "viewer",
                                })
                            }
                        } })()
                    }
                }
            } catch (err) {
                console.error("Auth initialization error:", err)
            } finally {
                if (mounted) setLoading(false)
            }
        }

        initializeAuth()

        // Listen to Supabase auth state changes if enabled
        let authListener = null
        if (isSupabaseConfigured() && supabase) {
            const { data } = supabase.auth.onAuthStateChange(async (event, session) => {
                if (session?.user) {
                    activeUserIdRef.current = session.user.id
                    setUser(session.user)
                    // Keep the profile already fetched for this user (token refresh etc.); fall back only for a new user
                    setProfile((prev) => (prev?.id === session.user.id ? prev : {
                        id: session.user.id,
                        email: session.user.email,
                        full_name: session.user.user_metadata?.full_name || session.user.email?.split("@")[0] || "Fleet Manager",
                        role: "viewer",
                    }))
                } else {
                    activeUserIdRef.current = null
                    clearClientCache()
                    setUser(null)
                    setProfile(null)
                }
                setLoading(false)
            })
            authListener = data?.subscription
        }

        return () => {
            mounted = false
            if (authListener) authListener.unsubscribe()
        }
    }, [])

    // ─── Login Function ─────────────────────────────────────────────────────
    const login = async (email, password, rememberMe = true) => {
        const cleanEmail = email.trim().toLowerCase()

        if (!cleanEmail || !password) {
            throw new Error("Please enter both email and password.")
        }

        if (isSupabaseConfigured() && supabase) {
            const { data, error } = await supabase.auth.signInWithPassword({
                email: cleanEmail,
                password: password,
            })

            if (error) {
                // If Supabase authentication fails with network/invalid error, check if demo fallback is permitted
                throw new Error(error.message || "Invalid email or password.")
            }

            activeUserIdRef.current = data.user.id
            setUser(data.user)
            let fetchedProfile = {
                id: data.user.id,
                email: data.user.email,
                full_name: data.user.user_metadata?.full_name || cleanEmail.split("@")[0] || "Fleet Manager",
                role: "viewer",
            }

            try {
                const { data: prof } = await supabase
                    .from("profiles")
                    .select("*")
                    .eq("id", data.user.id)
                    .single()
                if (prof) fetchedProfile = prof
            } catch {
                // profile fallback
            }

            // Not applied when another sign-out or sign-in happened while the profile loaded
            if (activeUserIdRef.current === data.user.id) setProfile(fetchedProfile)
            return { user: data.user, profile: fetchedProfile }
        }
        void rememberMe
        throw new Error("Supabase authentication is not configured. Sign-in is unavailable.")
    }

    // ─── Logout Function ────────────────────────────────────────────────────
    const logout = async () => {
        try {
            if (isSupabaseConfigured() && supabase) await supabase.auth.signOut()
        } catch (e) {
            console.warn("Supabase sign out warning:", e)
        } finally {
            // Drop cached API responses so the next user never sees the previous session's data
            activeUserIdRef.current = null
            clearClientCache()
            setUser(null)
            setProfile(null)
        }
    }

    const value = {
        user,
        profile,
        loading,
        isAuthenticated: Boolean(user),
        isSupabaseEnabled: isSupabaseConfigured(),
        login,
        logout,
    }

    return (
        <AuthContext.Provider value={value}>
            {children}
        </AuthContext.Provider>
    )
}
