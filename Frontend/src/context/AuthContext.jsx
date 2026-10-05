import { useEffect, useState } from "react"
import { supabase, isSupabaseConfigured } from "../lib/supabase"
import { AuthContext } from "./auth-context"

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null)
    const [profile, setProfile] = useState(null)
    const [loading, setLoading] = useState(true)

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
                        setUser(session.user)
                        // Fetch profile from supabase profiles table if available
                        try {
                            const { data: profileData } = await supabase
                                .from("profiles")
                                .select("*")
                                .eq("id", session.user.id)
                                .single()
                            if (profileData && mounted) {
                                setProfile(profileData)
                            } else if (mounted) {
                                setProfile({
                                    id: session.user.id,
                                    email: session.user.email,
                                    full_name: session.user.user_metadata?.full_name || session.user.email?.split("@")[0] || "Fleet Manager",
                                    role: "viewer",
                                })
                            }
                        } catch {
                            if (mounted) {
                                setProfile({
                                    id: session.user.id,
                                    email: session.user.email,
                                    full_name: session.user.email?.split("@")[0] || "Fleet Manager",
                                    role: "viewer",
                                })
                            }
                        }
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
                    setUser(session.user)
                    setProfile({
                        id: session.user.id,
                        email: session.user.email,
                        full_name: session.user.user_metadata?.full_name || session.user.email?.split("@")[0] || "Fleet Manager",
                        role: "viewer",
                    })
                } else {
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

            setProfile(fetchedProfile)
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
