import { useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router-dom"

import {
    Search,
    X,
    MapPin,
    Gauge,
    Power,
    Navigation,
} from "lucide-react"

import { getVehicles } from "../api/fleetApi"
import FleetMap from "../components/FleetMap"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"



function Tracking() {
    const [searchParams] = useSearchParams()

    const [vehicles, setVehicles] = useState([])

    const [selectedVehicleId, setSelectedVehicleId] =
        useState(null)

    const [searchTerm, setSearchTerm] =
        useState("")

    const [statusFilter, setStatusFilter] =
        useState("ALL")

    const [loading, setLoading] =
        useState(true)

    const [error, setError] =
        useState(null)

    useEffect(() => {
        const vParam = searchParams.get("vehicleId") || searchParams.get("v")
        if (vParam) {
            const num = Number(vParam)
            setSelectedVehicleId(!isNaN(num) ? num : vParam)
        }
    }, [searchParams])


    /*
        Load fleet vehicles
    */

    useEffect(() => {

        async function loadVehicles() {

            try {

                const data = await getVehicles()

                setVehicles(data.vehicles)

                setError(null)

            } catch (err) {

                setError(err.message)

            } finally {

                setLoading(false)

            }

        }


        loadVehicles()

        const interval =
            setInterval(
                loadVehicles,
                10000
            )


        return () =>
            clearInterval(interval)

    }, [])


    /*
        Keep selected vehicle
        synchronized with refresh
    */

    const selectedVehicle = useMemo(() => {
        if (!selectedVehicleId) return null
        return (
            vehicles.find(
                vehicle =>
                    vehicle.vehicle_id ===
                    selectedVehicleId
            ) || null
        )
    }, [vehicles, selectedVehicleId])


    /*
        Filter vehicles
    */

    const filteredVehicles =
        useMemo(() => {

            return vehicles.filter(
                vehicle => {

                    const matchesSearch =
                        vehicle.vehicle_name
                            .toLowerCase()
                            .includes(
                                searchTerm.toLowerCase()
                            )


                    const matchesStatus =
                        statusFilter === "ALL" ||
                        vehicle.status ===
                        statusFilter


                    return (
                        matchesSearch &&
                        matchesStatus
                    )

                }
            )

        }, [
            vehicles,
            searchTerm,
            statusFilter,
        ])


    /*
        Only vehicles with coordinates
    */

    const mapVehicles =
        filteredVehicles.filter(
            vehicle =>
                vehicle.latitude !== null &&
                vehicle.longitude !== null
        )


    /*
        Status counts
    */

    const counts = {

        ALL: vehicles.length,

        MOVING:
            vehicles.filter(
                vehicle =>
                    vehicle.status ===
                    "MOVING"
            ).length,

        IDLING:
            vehicles.filter(
                vehicle =>
                    vehicle.status ===
                    "IDLING"
            ).length,

        STOPPED:
            vehicles.filter(
                vehicle =>
                    vehicle.status ===
                    "STOPPED"
            ).length,

    }


    /*
        Select vehicle
    */

    const selectVehicle = (
        vehicle
    ) => {

        setSelectedVehicleId(
            vehicle ? vehicle.vehicle_id : null
        )

    }


    return (

        <SidebarProvider>
        <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">

            <Sidebar />


            <div className="flex min-w-0 flex-1 flex-col">



                <main className="flex flex-1 flex-col overflow-hidden">


                    {/* Header */}

                    <div className="shrink-0 border-b border-slate-200 bg-white px-6 py-4">

                        <div className="flex items-center justify-between">

                            <div>

                                <h1 className="text-lg font-semibold text-slate-900">
                                    Live Tracking
                                </h1>

                                <p className="mt-1 text-xs text-slate-500">
                                    Real-time vehicle locations
                                </p>

                            </div>


                            <div className="flex items-center gap-2 rounded-full bg-green-50 px-3 py-1.5">

                                <span className="h-2 w-2 rounded-full bg-green-500" />

                                <span className="text-xs font-medium text-green-700">
                                    Live
                                </span>

                            </div>

                        </div>

                    </div>


                    {/* Workspace */}

                    <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50">

                        {/* Search & Filters */}
                        <div className="px-5 pt-5">

                            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">

                                <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">

                                    {/* Search */}
                                    <div className="relative w-full lg:w-[320px]">

                                        <Search
                                            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                                        />

                                        <input
                                            type="text"
                                            placeholder="Search vehicles..."
                                            value={searchTerm}
                                            onChange={(e) =>
                                                setSearchTerm(e.target.value)
                                            }
                                            className="w-full rounded-lg border border-slate-300 py-2.5 pl-9 pr-3 text-sm outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-100"
                                        />

                                    </div>


                                    {/* Filters */}
                                    <div className="flex flex-wrap gap-2">

                                        {[
                                            "ALL",
                                            "MOVING",
                                            "IDLING",
                                            "STOPPED",
                                        ].map(status => (

                                            <button
                                                key={status}
                                                type="button"
                                                onClick={() =>
                                                    setStatusFilter(status)
                                                }
                                                className={
                                                    statusFilter === status
                                                        ? "rounded-lg bg-slate-900 px-4 py-2 text-xs font-medium text-white"
                                                        : "rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 transition hover:bg-slate-50"
                                                }
                                            >

                                                {status === "ALL"
                                                    ? `All (${counts.ALL})`
                                                    : status === "MOVING"
                                                        ? `Moving (${counts.MOVING})`
                                                        : status === "IDLING"
                                                            ? `Idle (${counts.IDLING})`
                                                            : `Stopped (${counts.STOPPED})`
                                                }

                                            </button>

                                        ))}

                                    </div>

                                </div>

                            </div>

                        </div>


                        {/* MAP */}
                        <div className="relative h-[calc(100vh-360px)] min-h-[450px] p-5 pt-3">

                            <div className="relative h-full overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">

                                <FleetMap
                                    vehicles={mapVehicles}
                                    selectedVehicleId={selectedVehicleId}
                                    selectedVehicle={selectedVehicle}
                                    onVehicleSelect={selectVehicle}
                                />

                                <div className="absolute bottom-4 left-4 z-[500] rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-md">

                                    <div className="flex items-center gap-2">

                                        <span className="h-2 w-2 rounded-full bg-green-500" />

                                        <span className="text-xs font-medium text-slate-700">
                                            {mapVehicles.length} vehicles on map
                                        </span>

                                    </div>

                                </div>


                            </div>
                        </div>


                        {/* VEHICLE LIST */}
                        <div className="px-5 pt-3 pb-5">

                            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">

                                <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">

                                    <div>

                                        <h2 className="text-sm font-semibold text-slate-900">
                                            Vehicles
                                        </h2>

                                        <p className="mt-1 text-xs text-slate-500">
                                            Select a vehicle to view its live details.
                                        </p>

                                    </div>

                                    <span className="text-xs text-slate-400">
                                        Showing <span className="font-medium text-slate-600">{filteredVehicles.length}</span> of <span className="font-medium text-slate-600">{vehicles.length}</span>
                                    </span>

                                </div>


                                {/* Vehicle Table / List */}
                                <div className="divide-y divide-slate-100">

                                    {loading && (

                                        <div className="p-6 text-center">

                                            <p className="text-xs text-slate-500">
                                                Loading vehicles...
                                            </p>

                                        </div>

                                    )}

                                    {error && (

                                        <div className="m-4 rounded-lg border border-red-200 bg-red-50 p-3">

                                            <p className="text-xs text-red-700">
                                                {error}
                                            </p>

                                        </div>

                                    )}

                                    {!loading && !error && filteredVehicles.length === 0 && (

                                        <div className="p-8 text-center">

                                            <p className="text-sm text-slate-500">
                                                No vehicles found
                                            </p>

                                        </div>

                                    )}

                                    {!loading && !error && filteredVehicles.map(vehicle => {

                                        const isSelected = vehicle.vehicle_id === selectedVehicleId

                                        return (

                                            <button
                                                key={vehicle.vehicle_id}
                                                type="button"
                                                onClick={() => selectVehicle(vehicle)}
                                                className={
                                                    isSelected
                                                        ? "w-full bg-slate-50 px-5 py-4 text-left transition"
                                                        : "w-full px-5 py-4 text-left transition hover:bg-slate-50"
                                                }
                                            >

                                                <div className="flex items-center justify-between">

                                                    <div className="flex items-center gap-3">

                                                        <span
                                                            className={
                                                                vehicle.status === "MOVING"
                                                                    ? "h-2.5 w-2.5 shrink-0 rounded-full bg-green-500"
                                                                    : vehicle.status === "IDLING"
                                                                        ? "h-2.5 w-2.5 shrink-0 rounded-full bg-orange-500"
                                                                        : "h-2.5 w-2.5 shrink-0 rounded-full bg-slate-400"
                                                            }
                                                        />

                                                        <div>

                                                            <p className="text-sm font-medium text-slate-900">
                                                                {vehicle.vehicle_name}
                                                            </p>

                                                            <div className="mt-0.5 flex items-center gap-2 text-[11px]">

                                                                <span
                                                                    className={
                                                                        vehicle.status === "MOVING"
                                                                            ? "text-green-600"
                                                                            : vehicle.status === "IDLING"
                                                                                ? "text-orange-600"
                                                                                : "text-slate-500"
                                                                    }
                                                                >
                                                                    {vehicle.status}
                                                                </span>

                                                                <span className="text-slate-300">
                                                                    •
                                                                </span>

                                                                <span className="text-slate-400">
                                                                    Ignition {vehicle.ignition ? "ON" : "OFF"}
                                                                </span>

                                                                {vehicle.status === "IDLING" && (

                                                                    <>
                                                                        <span className="text-slate-300">
                                                                            •
                                                                        </span>

                                                                        <span className="text-orange-500">
                                                                            Idle {vehicle.idle_duration_formatted}
                                                                        </span>
                                                                    </>

                                                                )}

                                                            </div>

                                                        </div>

                                                    </div>

                                                    <div className="text-right">

                                                        <span className="text-sm font-semibold text-slate-800">
                                                            {vehicle.speed} km/h
                                                        </span>

                                                        <p className="text-[11px] text-slate-400">
                                                            {vehicle.latitude} , {vehicle.longitude}
                                                        </p>

                                                    </div>

                                                </div>

                                            </button>

                                        )

                                    })}

                                </div>

                                {/* Panel footer */}
                                <div className="border-t border-slate-200 px-5 py-3">

                                    <p className="text-[11px] text-slate-400">
                                        Showing <span className="font-medium text-slate-600">{filteredVehicles.length}</span> of <span className="font-medium text-slate-600">{vehicles.length}</span> vehicles
                                    </p>

                                </div>

                            </div>

                        </div>

                    </div>

                </main>

            </div>

        </div>
        </SidebarProvider>

    )
}


export default Tracking

