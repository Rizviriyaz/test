import { CLASSIFICATION_LABELS, classificationOf } from "./fuelLabels"

const STYLES = {
    recorded: "border-slate-200 text-slate-600",
    derived: "border-sky-200 text-sky-800",
    estimated: "border-amber-200 text-amber-800",
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
            className={`inline-flex items-center rounded-sm border px-1 text-[10px] font-medium leading-4 ${STYLES[kind]}`}
            title={TITLES[kind]}
        >
            {CLASSIFICATION_LABELS[kind]}
        </span>
    )
}
