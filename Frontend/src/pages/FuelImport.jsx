import { useState, useEffect, useRef } from "react"
import { Link } from "react-router-dom"
import {
    UploadCloud,
    FileSpreadsheet,
    CheckCircle2,
    AlertTriangle,
    XCircle,
    Clock3,
    ArrowRight,
    Check,
    X,
    Loader2,
    Calendar,
    Table,
    History,
    RefreshCw,
} from "lucide-react"
import Sidebar, { SidebarProvider } from "../components/Layout/Sidebar"
import {
    previewFuelImport,
    executeFuelImport,
    getFuelImports,
} from "../api/fleetApi"
import { formatUAEDateTime } from "../utils/datetime"

export default function FuelImport() {
    const [selectedFile, setSelectedFile] = useState(null)
    const [isDragging, setIsDragging] = useState(false)
    const [previewLoading, setPreviewLoading] = useState(false)
    const [previewData, setPreviewData] = useState(null)
    const [previewError, setPreviewError] = useState(null)

    // Import execution state
    const [importing, setImporting] = useState(false)
    const [importResult, setImportResult] = useState(null)
    const [importError, setImportError] = useState(null)

    // Import history state
    const [history, setHistory] = useState([])
    const [loadingHistory, setLoadingHistory] = useState(true)
    const [historyError, setHistoryError] = useState(null)

    const fileInputRef = useRef(null)

    // Fetch previous imports history
    const loadHistory = async () => {
        try {
            setLoadingHistory(true)
            setHistoryError(null)
            const res = await getFuelImports(20, 0)
            setHistory(Array.isArray(res?.imports) ? res.imports : [])
        } catch (err) {
            // An outage must not read as "no previous imports"
            setHistory([])
            setHistoryError(err?.message || "Could not load import history.")
        } finally {
            setLoadingHistory(false)
        }
    }

    useEffect(() => {
        loadHistory()
    }, [])

    // Handle file selection (drag or click)
    const handleFileChosen = async (file) => {
        if (!file) return
        if (!file.name.toLowerCase().endsWith(".xlsx")) {
            setPreviewError("Unsupported file type. Please upload an Excel spreadsheet (.xlsx).")
            return
        }

        setSelectedFile(file)
        setPreviewError(null)
        setPreviewData(null)
        setImportResult(null)
        setImportError(null)

        try {
            setPreviewLoading(true)
            const preview = await previewFuelImport(file)
            setPreviewData(preview)
        } catch (err) {
            console.error("Preview error:", err)
            setPreviewError(err.message || "Failed to parse Excel file for preview.")
        } finally {
            setPreviewLoading(false)
        }
    }

    const handleDragOver = (e) => {
        e.preventDefault()
        setIsDragging(true)
    }

    const handleDragLeave = () => {
        setIsDragging(false)
    }

    const handleDrop = (e) => {
        e.preventDefault()
        setIsDragging(false)
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            handleFileChosen(e.dataTransfer.files[0])
        }
    }

    // Cancel preview
    const handleCancelPreview = () => {
        setSelectedFile(null)
        setPreviewData(null)
        setPreviewError(null)
        if (fileInputRef.current) fileInputRef.current.value = ""
    }

    // Confirm and execute import
    const handleConfirmImport = async () => {
        if (!selectedFile || !previewData) return

        try {
            setImporting(true)
            setImportError(null)
            const result = await executeFuelImport(selectedFile, previewData.suggested_mapping)
            setImportResult(result)
            setPreviewData(null)
            loadHistory() // Refresh history list
        } catch (err) {
            console.error("Import execution failed:", err)
            setImportError(err.message || "An error occurred while importing fuel statement.")
        } finally {
            setImporting(false)
        }
    }

    return (
        <SidebarProvider>
            <div className="flex h-screen overflow-hidden bg-white text-slate-900">
                <Sidebar />

                <div className="flex min-w-0 flex-1 flex-col">
                    <main className="flex-1 overflow-y-auto px-6 lg:px-8 py-8">
                        {/* Header */}
                        <section className="mb-6">
                            <div className="flex items-center gap-2">
                                <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#33758F]" />
                                <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
                                    Fuel Data Ingestion
                                </p>
                            </div>
                            <h1 className="mt-1 text-2xl font-black tracking-tight text-slate-900">
                                Import Fuel Data
                            </h1>
                            <p className="mt-1 text-xs text-slate-500">
                                Ingest verified Excel fuel statements (.xlsx) with automated column mapping and audit logging
                            </p>
                        </section>

                        {/* ─── IMPORT RESULT BANNER ────────────────────────────────── */}
                        {importResult && (
                            <div
                                className={`mb-6 rounded-2xl border p-5 shadow-xs ${
                                    importResult.status === "completed"
                                        ? "border-emerald-200 bg-emerald-50/70"
                                        : importResult.status === "completed_with_errors"
                                        ? "border-amber-200 bg-amber-50/70"
                                        : "border-rose-200 bg-rose-50/70"
                                }`}
                            >
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                                    <div className="flex items-start gap-3">
                                        {importResult.status === "completed" ? (
                                            <div className="rounded-xl bg-emerald-100 p-2 text-emerald-700">
                                                <CheckCircle2 className="h-6 w-6" />
                                            </div>
                                        ) : importResult.status === "completed_with_errors" ? (
                                            <div className="rounded-xl bg-amber-100 p-2 text-amber-700">
                                                <AlertTriangle className="h-6 w-6" />
                                            </div>
                                        ) : (
                                            <div className="rounded-xl bg-rose-100 p-2 text-rose-700">
                                                <XCircle className="h-6 w-6" />
                                            </div>
                                        )}

                                        <div>
                                            <h3 className="text-sm font-bold text-slate-900">
                                                {importResult.status === "completed"
                                                    ? "Import Completed Successfully"
                                                    : importResult.status === "completed_with_errors"
                                                    ? "Import Completed with Warnings"
                                                    : "Import Failed"}
                                            </h3>
                                            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-600">
                                                <span className="font-bold text-emerald-800">
                                                    {Number(importResult.rows_imported || 0).toLocaleString()} transactions imported
                                                </span>
                                                {importResult.rows_duplicate > 0 && (
                                                    <span>• {importResult.rows_duplicate} duplicate rows skipped</span>
                                                )}
                                                {importResult.rows_failed > 0 && (
                                                    <span className="text-rose-700">• {importResult.rows_failed} rows failed validation</span>
                                                )}
                                                {importResult.date_from && importResult.date_to && (
                                                    <span>• Period: {importResult.date_from} → {importResult.date_to}</span>
                                                )}
                                            </div>
                                        </div>
                                    </div>

                                    <div className="flex items-center gap-2.5">
                                        <button
                                            type="button"
                                            onClick={() => setImportResult(null)}
                                            className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer"
                                        >
                                            Dismiss
                                        </button>
                                        <Link
                                            to="/fuel"
                                            className="inline-flex items-center gap-1.5 rounded-xl bg-[#33758F] px-4 py-2 text-xs font-bold text-white hover:bg-[#285d73] shadow-xs"
                                        >
                                            <span>View Dashboard</span>
                                            <ArrowRight className="h-3.5 w-3.5" />
                                        </Link>
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Error Message */}
                        {(previewError || importError) && (
                            <div className="mb-6 flex items-center gap-3 rounded-2xl border border-rose-200 bg-rose-50/80 p-4 text-xs font-semibold text-rose-700">
                                <AlertTriangle className="h-5 w-5 shrink-0 text-rose-600" />
                                <span>{previewError || importError}</span>
                            </div>
                        )}

                        {/* ─── UPLOAD INTERFACE (DRAG & DROP) ───────────────────────── */}
                        {!previewData && !previewLoading && (
                            <div
                                onDragOver={handleDragOver}
                                onDragLeave={handleDragLeave}
                                onDrop={handleDrop}
                                onClick={() => fileInputRef.current?.click()}
                                className={`mb-8 rounded-3xl border-2 border-dashed p-10 text-center transition-all cursor-pointer ${
                                    isDragging
                                        ? "border-[#33758F] bg-[#33758F]/5 scale-[0.99]"
                                        : "border-slate-200 bg-white hover:border-[#33758F]/50 hover:bg-slate-50/50 shadow-xs"
                                }`}
                            >
                                <input
                                    ref={fileInputRef}
                                    type="file"
                                    accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                                    className="hidden"
                                    onChange={(e) => {
                                        if (e.target.files && e.target.files.length > 0) {
                                            handleFileChosen(e.target.files[0])
                                        }
                                    }}
                                />

                                <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-[#33758F]/10 text-[#33758F] mb-4">
                                    <FileSpreadsheet className="h-7 w-7" />
                                </div>

                                <h3 className="text-sm font-bold text-slate-800">
                                    Drag & drop Excel fuel file here
                                </h3>
                                <p className="mt-1 text-xs text-slate-500">
                                    or <span className="font-bold text-[#33758F] hover:underline">Choose Excel File</span> from your computer
                                </p>
                                <div className="mt-3 inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-[11px] font-semibold text-slate-500">
                                    <span>Supported format:</span>
                                    <span className="font-bold text-slate-700">.xlsx</span>
                                </div>
                            </div>
                        )}

                        {/* ─── PREVIEW LOADING STATE ─────────────────────────────────── */}
                        {previewLoading && (
                            <div className="mb-8 rounded-3xl border border-slate-200 bg-white p-12 text-center shadow-xs">
                                <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#33758F]" />
                                <h4 className="mt-3 text-sm font-bold text-slate-800">
                                    Analyzing Fuel Statement…
                                </h4>
                                <p className="mt-1 text-xs text-slate-500">
                                    Detecting column headers, identifying data rows, and matching schema
                                </p>
                            </div>
                        )}

                        {/* ─── PREVIEW PANEL (WHEN FILE ANALYZED) ───────────────────── */}
                        {previewData && (
                            <div className="mb-8 rounded-2xl border border-slate-200/80 bg-white p-6 shadow-xs">
                                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between pb-4 border-b border-slate-100 gap-3">
                                    <div>
                                        <div className="flex items-center gap-2">
                                            <span className="rounded-md bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                                                Preview Ready
                                            </span>
                                            <h3 className="text-sm font-bold text-slate-900">
                                                {previewData.file_name}
                                            </h3>
                                        </div>
                                        <p className="text-xs text-slate-500 mt-0.5">
                                            Worksheet: <span className="font-semibold text-slate-700">{previewData.worksheet_name}</span> • Header row: #{previewData.detected_header_row}
                                        </p>
                                    </div>

                                    <div className="flex items-center gap-2.5">
                                        <button
                                            type="button"
                                            disabled={importing}
                                            onClick={handleCancelPreview}
                                            className="rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 cursor-pointer disabled:opacity-50"
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            type="button"
                                            disabled={importing}
                                            onClick={handleConfirmImport}
                                            className="inline-flex items-center gap-1.5 rounded-xl bg-[#33758F] px-4 py-2 text-xs font-bold text-white hover:bg-[#285d73] shadow-xs cursor-pointer disabled:opacity-50"
                                        >
                                            {importing ? (
                                                <>
                                                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                                    <span>Importing to Supabase…</span>
                                                </>
                                            ) : (
                                                <>
                                                    <UploadCloud className="h-3.5 w-3.5" />
                                                    <span>Import Data</span>
                                                </>
                                            )}
                                        </button>
                                    </div>
                                </div>

                                {/* Row statistics */}
                                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 my-4">
                                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-center">
                                        <span className="text-[10px] font-bold uppercase text-slate-400 block">Total Rows</span>
                                        <span className="text-lg font-black text-slate-900">
                                            {previewData.rows_found.toLocaleString()}
                                        </span>
                                    </div>
                                    <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-3 text-center">
                                        <span className="text-[10px] font-bold uppercase text-emerald-600 block">Valid Candidate Rows</span>
                                        <span className="text-lg font-black text-emerald-700">
                                            {previewData.rows_candidate.toLocaleString()}
                                        </span>
                                    </div>
                                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-center">
                                        <span className="text-[10px] font-bold uppercase text-slate-400 block">Ignored / Headers</span>
                                        <span className="text-lg font-black text-slate-500">
                                            {previewData.rows_ignored.toLocaleString()}
                                        </span>
                                    </div>
                                    <div className="rounded-xl border border-slate-100 bg-slate-50/70 p-3 text-center">
                                        <span className="text-[10px] font-bold uppercase text-slate-400 block">Mapped Columns</span>
                                        <span className="text-lg font-black text-[#33758F]">
                                            {Object.keys(previewData.suggested_mapping || {}).length}
                                        </span>
                                    </div>
                                </div>

                                {/* Validation warnings if any */}
                                {previewData.validation_warnings?.length > 0 && (
                                    <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50/60 p-3 text-xs text-amber-800">
                                        <div className="flex items-center gap-2 font-bold mb-1">
                                            <AlertTriangle className="h-4 w-4 text-amber-600" />
                                            <span>Validation Notes ({previewData.validation_warnings.length})</span>
                                        </div>
                                        <ul className="list-disc list-inside space-y-0.5 text-[11px] text-amber-700">
                                            {previewData.validation_warnings.slice(0, 3).map((w, idx) => (
                                                <li key={idx}>{w}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}

                                {/* Detected Columns Mapping details */}
                                <div className="space-y-2 mb-4">
                                    <h4 className="text-xs font-bold text-slate-700 uppercase tracking-wider">
                                        Detected Column Mappings
                                    </h4>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                                        {previewData.mapping_details?.map((m) => (
                                            <div
                                                key={m.canonical_field}
                                                className={`rounded-lg border p-2 text-xs flex items-center justify-between ${
                                                    m.excel_header
                                                        ? "border-emerald-200 bg-emerald-50/30"
                                                        : m.required
                                                        ? "border-rose-200 bg-rose-50/30"
                                                        : "border-slate-200 bg-slate-50/50"
                                                }`}
                                            >
                                                <div className="min-w-0">
                                                    <span className="font-bold text-slate-800 block truncate">
                                                        {m.canonical_field}
                                                    </span>
                                                    <span className="text-[10px] text-slate-400 truncate block">
                                                        ↳ {m.excel_header || "Not mapped"}
                                                    </span>
                                                </div>
                                                {m.excel_header ? (
                                                    <Check className="h-3.5 w-3.5 text-emerald-600 shrink-0 ml-1" />
                                                ) : m.required ? (
                                                    <X className="h-3.5 w-3.5 text-rose-500 shrink-0 ml-1" />
                                                ) : null}
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {/* Sample Rows Table */}
                                {previewData.sample_rows?.length > 0 && (
                                    <div>
                                        <h4 className="text-xs font-bold text-slate-700 uppercase tracking-wider mb-2">
                                            Sample Data Rows (First {previewData.sample_rows.length})
                                        </h4>
                                        <div className="overflow-x-auto rounded-xl border border-slate-200">
                                            <table className="w-full text-left text-xs">
                                                <thead className="bg-slate-50 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                                                    <tr>
                                                        {previewData.detected_columns?.slice(0, 7).map((col) => (
                                                            <th key={col} className="px-3 py-2 whitespace-nowrap">
                                                                {col}
                                                            </th>
                                                        ))}
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-slate-100 font-mono text-[11px] text-slate-700">
                                                    {previewData.sample_rows.slice(0, 4).map((row, idx) => (
                                                        <tr key={idx} className="hover:bg-slate-50/60">
                                                            {previewData.detected_columns?.slice(0, 7).map((col) => (
                                                                <td key={col} className="px-3 py-2 whitespace-nowrap">
                                                                    {String(row[col] ?? "—")}
                                                                </td>
                                                            ))}
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* ─── IMPORT AUDIT HISTORY SECTION ─────────────────────────── */}
                        <section className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-xs">
                            <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                                <div className="flex items-center gap-2">
                                    <div className="rounded-xl bg-[#33758F]/10 p-1.5 text-[#33758F]">
                                        <History className="h-4 w-4" />
                                    </div>
                                    <h3 className="text-sm font-bold text-slate-900">
                                        Import History & Audit Log
                                    </h3>
                                </div>
                                <button
                                    type="button"
                                    onClick={loadHistory}
                                    className="p-1 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100"
                                    title="Refresh import history"
                                >
                                    <RefreshCw className="h-3.5 w-3.5" />
                                </button>
                            </div>

                            <div className="divide-y divide-slate-100 pt-2">
                                {loadingHistory ? (
                                    <div className="py-8 text-center text-xs text-slate-400">
                                        <Loader2 className="mx-auto h-5 w-5 animate-spin text-[#33758F] mb-2" />
                                        Loading import history…
                                    </div>
                                ) : historyError ? (
                                    <div role="alert" className="py-8 text-center text-xs text-rose-600">
                                        {historyError}
                                    </div>
                                ) : history.length === 0 ? (
                                    <div className="py-8 text-center text-xs text-slate-400">
                                        No previous fuel statement imports found.
                                    </div>
                                ) : (
                                    history.map((item) => (
                                        <div
                                            key={item.id}
                                            className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs"
                                        >
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <span className="font-bold text-slate-900">{item.file_name}</span>
                                                    <span
                                                        className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                                                            item.status === "completed"
                                                                ? "bg-emerald-100 text-emerald-800"
                                                                : item.status === "completed_with_errors"
                                                                ? "bg-amber-100 text-amber-800"
                                                                : "bg-rose-100 text-rose-800"
                                                        }`}
                                                    >
                                                        {item.status}
                                                    </span>
                                                </div>
                                                <div className="text-[11px] text-slate-500 mt-0.5 flex flex-wrap items-center gap-3">
                                                    <span>
                                                        Imported: {formatUAEDateTime(item.created_at)}
                                                    </span>
                                                    {item.date_from && item.date_to && (
                                                        <span>
                                                            • Period: {item.date_from} → {item.date_to}
                                                        </span>
                                                    )}
                                                </div>
                                            </div>

                                            <div className="flex items-center gap-4 text-right">
                                                <div>
                                                    <span className="font-bold text-slate-900">
                                                        {Number(item.rows_imported || 0).toLocaleString()}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Imported</span>
                                                </div>
                                                <div>
                                                    <span className="font-semibold text-slate-600">
                                                        {item.rows_duplicate || 0}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Duplicates</span>
                                                </div>
                                                <div>
                                                    <span className="font-semibold text-slate-600">
                                                        {item.rows_failed || 0}
                                                    </span>
                                                    <span className="block text-[10px] text-slate-400">Failed</span>
                                                </div>
                                            </div>
                                        </div>
                                    ))
                                )}
                            </div>
                        </section>
                    </main>
                </div>
            </div>
        </SidebarProvider>
    )
}
