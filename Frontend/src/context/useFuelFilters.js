import { useContext } from "react"
import { FuelFilterContext } from "./fuel-filter-context"

export function useFuelFilters() {
    const ctx = useContext(FuelFilterContext)
    if (!ctx) {
        throw new Error("useFuelFilters must be used within a FuelFilterProvider")
    }
    return ctx
}
