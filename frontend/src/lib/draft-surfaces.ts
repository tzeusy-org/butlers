/** Exact field eligibility from the adopted persist-safe-dashboard-unsent-drafts Decision 2.
 * Shared controls never register themselves. Names here describe UI bundles, not API payloads.
 */
export const DRAFT_FIELDS = {
    "chat.message": ["message"],
    "health.condition": ["name", "status", "diagnosedAt", "notes"],
    "health.meal": ["type", "description", "eatenAt", "calories", "protein", "carbs", "fat", "notes"],
    "health.measurement": ["type", "scalar", "systolic", "diastolic", "valueObject", "measuredAt", "notes"],
    "health.medication": ["name", "dosage", "frequency", "schedule", "active", "notes"],
    "health.research": ["title", "content", "tags", "sourceUrl"],
    "health.symptom": ["name", "severity", "occurredAt", "notes"],
    "schedule.form": ["name", "cron", "dispatchMode", "prompt", "jobName", "jobArgsRaw", "complexity"],
    "approvals.rule.create": ["tool_name", "description", "arg_constraints_text", "max_uses", "expires_days"],
    "relationship.interaction": ["type", "summary"],
    "relationship.gift": ["description", "occasion"],
    "relationship.reach-out": ["message", "channel"],
    "relationship.note": ["content"],
    "chronicles.correction": ["title", "privacy", "note"],
    "butler-state.set": ["key", "value"],
    "butler-state.edit": ["key", "value"],
    "calendar.butler-event": ["butlerName", "eventKind", "title", "startAtLocal", "endAtLocal", "timezone", "recurrenceFrequency", "cron", "hasUntilAt", "untilAtLocal"],
} as const;
export type DraftSurface = keyof typeof DRAFT_FIELDS;
export type DraftValue = string | number | boolean | string[] | null;
export type DraftFields = Record<string, DraftValue>;
/** Whole-bundle validation: unknown fields, nested objects, and mismatched value types refuse. */
export function validDraftFields(surface: DraftSurface, fields: unknown): fields is DraftFields {
    if (!Object.hasOwn(DRAFT_FIELDS, surface) || !fields || typeof fields !== "object" || Array.isArray(fields))
        return false;
    const keys = Object.keys(fields);
    const allowed: readonly string[] = DRAFT_FIELDS[surface];
    return keys.length === allowed.length && keys.every((key) => {
        if (!allowed.includes(key))
            return false;
        const value = (fields as DraftFields)[key];
        if (["active", "hasUntilAt", "enabled"].includes(key))
            return typeof value === "boolean";
        if (key === "severity")
            return typeof value === "number" && Number.isFinite(value) && value >= 1 && value <= 10;
        if (key === "entityIds")
            return Array.isArray(value) && value.every((entry) => typeof entry === "string");
        return typeof value === "string";
    });
}
/** Stable serialization is used inside records/baselines only, never as a content-derived key. */
export function draftBundle(fields: DraftFields): string {
    return JSON.stringify(Object.fromEntries(Object.entries(fields).sort(([a], [b]) => a.localeCompare(b))));
}

/** Only chat treats whitespace as empty; meaningful message bytes and form bundles stay exact. */
export function emptyChatDraft(surface: DraftSurface, fields: DraftFields): boolean {
    return surface === "chat.message" && typeof fields.message === "string" && fields.message.trim().length === 0;
}
