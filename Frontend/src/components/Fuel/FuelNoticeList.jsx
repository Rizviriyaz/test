import { AlertTriangle, Info } from "lucide-react"

// Concise data notices (see fuelNotices.js), shown above the fuel dashboards' KPIs
export default function FuelNoticeList({ notices }) {
    if (!notices?.length) return null
    return (
        <ul role="status" className="space-y-1 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-xs">
            {notices.map((n) => (
                <li key={n.key} className={`flex items-start gap-2 ${n.tone === "warn" ? "text-amber-900" : "text-slate-600"}`} title={n.title}>
                    {n.tone === "warn"
                        ? <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
                        : <Info className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />}
                    <span className="min-w-0">{n.text}</span>
                </li>
            ))}
        </ul>
    )
}
