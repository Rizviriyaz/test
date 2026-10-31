import { UploadCloud } from "lucide-react"
import { useFuelFilters } from "../../context/useFuelFilters"
import { useAuth } from "../../context/useAuth"
import PageHeader from "../Common/PageHeader"
import MonthNavigator from "./MonthNavigator"

// Roles the backend accepts for fuel import (FUEL_IMPORT_ROLES in Backend/app/fuel/routes.py); it stays the authority
const FUEL_IMPORT_ROLES = new Set(["admin", "manager", "fleet_manager"])

// Idle fuel rate used by every idle estimate (KPI, chart, ranking, leaderboard); 2.0 L/h is the default
const IDLE_RATE_OPTIONS = [1.5, 2.0, 2.5, 3.0]

// `status`: an optional freshness badge for the page data (FuelPerformance passes the Wialon telematics freshness)
export default function FuelHeader({ idleRate = 2.0, setIdleRate, onOpenImport, status = null }) {
    const { profile } = useAuth()
    const canImport = FUEL_IMPORT_ROLES.has(profile?.role)
    const { selectedMonth, setMonth } = useFuelFilters()

    return (
        <PageHeader
            actions={onOpenImport ? (
                <button
                    type="button"
                    onClick={canImport ? onOpenImport : undefined}
                    disabled={!canImport}
                    className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-[#33758F] px-3 text-xs font-medium text-white transition-colors hover:bg-[#285d72] cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/40 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-[#33758F]"
                    title={canImport ? "Upload new Excel fuel statement" : "Your role does not permit importing fuel statements"}
                >
                    <UploadCloud className="h-3.5 w-3.5" />
                    <span>Import Fuel</span>
                </button>
            ) : null}
        >
            <div className="flex flex-wrap items-center gap-2">
                <MonthNavigator month={selectedMonth} onChange={setMonth} />
                {status}

                {setIdleRate && (
                    <label
                        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-xs focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15"
                        title="Idle fuel rate used for every idle fuel estimate"
                    >
                        <span className="text-slate-500">Idle rate</span>
                        <select
                            value={idleRate}
                            onChange={(e) => setIdleRate(parseFloat(e.target.value))}
                            aria-label="Idle fuel rate (litres per hour)"
                            className="cursor-pointer bg-transparent font-medium text-slate-800 tabular-nums outline-none"
                        >
                            {(IDLE_RATE_OPTIONS.includes(idleRate) ? IDLE_RATE_OPTIONS : [...IDLE_RATE_OPTIONS, idleRate].sort((a, b) => a - b))
                                .map((rate) => (
                                    <option key={rate} value={rate}>{rate.toFixed(1)} L/h</option>
                                ))}
                        </select>
                    </label>
                )}
            </div>
        </PageHeader>
    )
}
