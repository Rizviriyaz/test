import { CLASSIFICATION_LABELS, classificationOf } from "./fuelLabels"

const STYLES = {
    recorded: "bg-emerald-50 text-emerald-800 border-emerald-200",
    derived: "bg-sky-50 text-sky-800 border-sky-200",
    estimated: "bg-amber-50 text-amber-800 border-amber-200",
}

const TITLES = {
    recorded: "Taken directly from fuel statements or Wialon reports",
    derived: "Calculated from recorded values (purchase-based, not measured consumption)",
    estimated: "Depends on an assumed rate (e.g. idle L/h)",
}

// Renders only what the backend's value_classification says; nothing when the field is not classified.
export default function ClassificationBadge({ classification, field }) {
    const kind = classificationOf(classification, field)
    if (!kind) return null
    return (
        <span
            className={`inline-flex items-center rounded px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wider border ${STYLES[kind]}`}
            title={TITLES[kind]}
        >
            {CLASSIFICATION_LABELS[kind]}
        </span>
    )
}
