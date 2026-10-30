import { lazy } from "react"

// The vehicle detail drawer (and the framer-motion code its dialog uses) is loaded on first use instead of with
// Dashboard, Live Tracking and Idle Analytics: those pages mount it only once a vehicle is selected. App.jsx prefetches
// it with the page chunks after sign-in, so opening a vehicle does not wait for it.
export const loadVehicleDetailDrawer = () => import("./VehicleDetailDrawer")

export const LazyVehicleDetailDrawer = lazy(loadVehicleDetailDrawer)
