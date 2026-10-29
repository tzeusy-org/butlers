import { emptyChatDraft, DRAFT_FIELDS, validDraftFields, type DraftFields, type DraftSurface } from "./draft-surfaces";
const LIFETIME = 24 * 60 * 60 * 1000;
const TOMBSTONE_LIFETIME = 30 * LIFETIME;
const LIMIT = 64 * 1024;
const TIMEOUT = 1000;
type Reason = "empty" | "submitted" | "discarded" | "expired" | "invalid" | "auth_reset";
export type DraftFence = {
    storeEpoch: number;
    writeRevision: number;
};
export type DraftRecord = DraftFence & {
    schemaVersion: 1;
    state: "live" | "tombstone";
    draftKey: string;
    surfaceId: DraftSurface;
    context: string[];
    updatedAt: number;
    fields?: DraftFields;
    sourceBaseline?: string;
    contentRevision?: string;
    expiresAt?: number;
    deletedAt?: number;
    deletionReason?: Reason;
};
/** Same-tab submission provenance; never persisted or sent through BroadcastChannel. */
export type DraftSubmissionChange = {
    key: string;
    contentRevision: string;
    instance: symbol;
    fence: DraftFence;
};
export type DraftRead = {
    record: DraftRecord | null;
    fence: DraftFence;
    rejected: boolean;
};
export class DraftFailure extends Error {
    readonly category: "unavailable" | "blocked" | "timeout" | "quota" | "oversize" | "corrupt" | "conflict";
    constructor(category: DraftFailure["category"]) {
        super(category);
        this.category = category;
    }
}
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && Number(value) >= 0; }
function fence(record: DraftFence | null, epoch: number): DraftFence {
    return record ? { storeEpoch: record.storeEpoch, writeRevision: record.writeRevision } : { storeEpoch: epoch, writeRevision: 0 };
}
function same(a: DraftFence, b: DraftFence) { return a.storeEpoch === b.storeEpoch && a.writeRevision === b.writeRevision; }
function request<T>(r: IDBRequest<T>): Promise<T> {
    return new Promise((resolve, reject) => { r.onsuccess = () => resolve(r.result); r.onerror = () => reject(new DraftFailure("unavailable")); });
}
function normalize(error: unknown): DraftFailure {
    if (error instanceof DraftFailure)
        return error;
    return new DraftFailure(error instanceof DOMException && error.name === "QuotaExceededError" ? "quota" : "unavailable");
}
function validRecord(raw: unknown, key: string, epoch: number): raw is DraftRecord {
    if (!raw || typeof raw !== "object" || Array.isArray(raw))
        return false;
    const r = raw as DraftRecord;
    if (r.schemaVersion !== 1 || r.draftKey !== key || !Object.hasOwn(DRAFT_FIELDS, r.surfaceId) ||
        !Array.isArray(r.context) || !r.context.every((s) => typeof s === "string") ||
        !integer(r.storeEpoch) || r.storeEpoch !== epoch || !integer(r.writeRevision) ||
        !Number.isFinite(r.updatedAt) || r.updatedAt < 0 || r.updatedAt > Date.now() + 60000)
        return false;
    const common = ["schemaVersion", "state", "draftKey", "surfaceId", "context", "storeEpoch", "writeRevision", "updatedAt"];
    if (r.state === "tombstone")
        return Object.keys(r).every((k) => [...common, "deletedAt", "deletionReason"].includes(k)) &&
            r.deletedAt === r.updatedAt && ["empty", "submitted", "discarded", "expired", "invalid", "auth_reset"].includes(r.deletionReason ?? "");
    return r.state === "live" && Object.keys(r).every((k) => [...common, "fields", "sourceBaseline", "contentRevision", "expiresAt"].includes(k)) &&
        validDraftFields(r.surfaceId, r.fields) && typeof r.contentRevision === "string" && r.contentRevision.length > 0 &&
        typeof r.sourceBaseline === "string" && r.expiresAt === r.updatedAt + LIFETIME && new TextEncoder().encode(JSON.stringify(r)).length <= LIMIT;
}
/** A content-blind, per-origin database. No network, telemetry or access-authority dependency. */
export class DraftStore {
    private opening: Promise<IDBDatabase> | null = null;
    private listeners = new Set<(change?: DraftSubmissionChange) => void>();
    private channel: BroadcastChannel | null = null;
    subscribe(listener: (change?: DraftSubmissionChange) => void): () => void {
        this.listeners.add(listener);
        if (!this.channel && typeof BroadcastChannel !== "undefined") {
            this.channel = new BroadcastChannel("butlers-drafts-v1");
            this.channel.onmessage = () => this.listeners.forEach((notify) => notify());
        }
        return () => {
            this.listeners.delete(listener);
            if (!this.listeners.size) {
                this.channel?.close();
                this.channel = null;
            }
        };
    }
    private changed(change?: DraftSubmissionChange) { this.listeners.forEach((notify) => notify(change)); this.channel?.postMessage({ changed: true }); }
    private open(): Promise<IDBDatabase> {
        if (this.opening)
            return this.opening;
        this.opening = new Promise<IDBDatabase>((resolve, reject) => {
            if (typeof indexedDB === "undefined") {
                reject(new DraftFailure("unavailable"));
                return;
            }
            let finished = false;
            const finish = (error?: DraftFailure) => { finished = true; clearTimeout(timer); if (error)
                reject(error); };
            const timer = setTimeout(() => finish(new DraftFailure("timeout")), TIMEOUT);
            let r: IDBOpenDBRequest;
            try {
                r = indexedDB.open("butlers-drafts", 1);
            }
            catch {
                finish(new DraftFailure("unavailable"));
                return;
            }
            r.onupgradeneeded = () => { for (const name of ["drafts", "chatAttempts", "meta"])
                if (!r.result.objectStoreNames.contains(name))
                    r.result.createObjectStore(name); };
            r.onblocked = () => finish(new DraftFailure("blocked"));
            r.onerror = () => finish(new DraftFailure("unavailable"));
            r.onsuccess = () => {
                if (finished) {
                    r.result.close();
                    return;
                }
                finish();
                r.result.onversionchange = () => { r.result.close(); this.opening = null; };
                resolve(r.result);
            };
        }).catch((error: unknown) => { this.opening = null; throw normalize(error); });
        return this.opening!;
    }
    private async transaction<T>(operation: (tx: IDBTransaction) => Promise<T>): Promise<T> {
        const started = Date.now();
        const db = await this.open();
        return new Promise<T>((resolve, reject) => {
            const tx = db.transaction(["drafts", "chatAttempts", "meta"], "readwrite");
            let value: T;
            let failure: unknown;
            const timer = setTimeout(() => { failure = new DraftFailure("timeout"); try {
                tx.abort();
            }
            catch { /* already settled */ } reject(failure); }, Math.max(0, TIMEOUT - (Date.now() - started)));
            tx.oncomplete = () => { clearTimeout(timer); resolve(value); };
            tx.onabort = tx.onerror = () => { clearTimeout(timer); reject(normalize(failure ?? tx.error)); };
            void operation(tx).then((result) => { value = result; }).catch((error: unknown) => { failure = error; try {
                tx.abort();
            }
            catch {
                clearTimeout(timer);
                reject(normalize(error));
            } });
        });
    }
    private async epoch(tx: IDBTransaction): Promise<number> {
        const epoch: unknown = await request(tx.objectStore("meta").get("storeEpoch"));
        if (epoch === undefined) {
            tx.objectStore("meta").put(0, "storeEpoch");
            return 0;
        }
        if (!integer(epoch))
            throw new DraftFailure("corrupt");
        return epoch;
    }
    private tombstone(record: DraftRecord, epoch: number, reason: Reason): DraftRecord {
        const now = Date.now();
        return { schemaVersion: 1, state: "tombstone", draftKey: record.draftKey, surfaceId: record.surfaceId, context: record.context,
            storeEpoch: epoch, writeRevision: record.writeRevision + 1, updatedAt: now, deletedAt: now, deletionReason: reason };
    }
    private async compact(tx: IDBTransaction, epoch: number, force = false): Promise<number> {
        const stores = [tx.objectStore("drafts"), tx.objectStore("chatAttempts")];
        const entries = await Promise.all(stores.map(async (store) => ({ store, keys: await request(store.getAllKeys()), rows: await request(store.getAll()) as unknown[] })));
        const tombstones = entries.flatMap(({ rows }) => rows).filter((r): r is DraftRecord => !!r && typeof r === "object" && (r as DraftRecord).state === "tombstone");
        if (!force && tombstones.length < 256 && !tombstones.some((r) => Date.now() - (r.deletedAt ?? Date.now()) >= TOMBSTONE_LIFETIME))
            return epoch;
        const next = epoch + 1;
        tx.objectStore("meta").put(next, "storeEpoch");
        for (const { store, keys, rows } of entries)
            rows.forEach((raw, i) => {
                const key = keys[i];
                if (typeof key === "string" && validRecord(raw, key, epoch) && raw.state === "live")
                    store.put({ ...raw, storeEpoch: next }, key);
                else
                    store.delete(key);
            });
        return next;
    }
    /** Auth reset and rollback share one ordered, content-free retirement transaction. */
    async reset(): Promise<void> {
        await this.transaction(async (tx) => {
            const epoch = await this.epoch(tx);
            const next = epoch + 1;
            tx.objectStore("meta").put(next, "storeEpoch");
            for (const name of ["drafts", "chatAttempts"]) {
                const store = tx.objectStore(name);
                const keys = await request(store.getAllKeys());
                const rows: unknown[] = await request(store.getAll());
                rows.forEach((raw, i) => {
                    const key = keys[i];
                    if (typeof key === "string" && validRecord(raw, key, epoch))
                        store.put(this.tombstone(raw, next, "auth_reset"), key);
                    else
                        store.delete(key);
                });
            }
        });
        this.changed();
    }
    async key(surface: DraftSurface, context: string[]): Promise<string> {
        if (!Object.hasOwn(DRAFT_FIELDS, surface) || !context.length || !context.every((s) => typeof s === "string" && s.length > 0))
            throw new DraftFailure("corrupt");
        const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(context)));
        const hash = btoa(String.fromCharCode(...new Uint8Array(bytes))).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
        return `v1:${surface}:${hash}`;
    }
    async read(key: string, surface: DraftSurface, context: string[]): Promise<DraftRead> {
        return this.transaction(async (tx) => {
            let epoch = await this.epoch(tx);
            epoch = await this.compact(tx, epoch);
            const store = tx.objectStore("drafts");
            const raw: unknown = await request(store.get(key));
            if (raw === undefined)
                return { record: null, fence: fence(null, epoch), rejected: false };
            if (!validRecord(raw, key, epoch) || raw.surfaceId !== surface || JSON.stringify(raw.context) !== JSON.stringify(context)) {
                const r = raw as Partial<DraftRecord> | null;
                if (r && r.storeEpoch === epoch && integer(r.writeRevision)) {
                    const clean = this.tombstone({ draftKey: key, surfaceId: surface, context, writeRevision: r.writeRevision } as DraftRecord, epoch, "invalid");
                    store.put(clean, key);
                    return { record: clean, fence: fence(clean, epoch), rejected: true };
                }
                epoch = await this.compact(tx, epoch, true);
                store.delete(key);
                return { record: null, fence: fence(null, epoch), rejected: true };
            }
            if (raw.state === "live" && (raw.expiresAt ?? 0) <= Date.now()) {
                const expired = this.tombstone(raw, epoch, "expired");
                store.put(expired, key);
                return { record: expired, fence: fence(expired, epoch), rejected: false };
            }
            if (raw.state === "live" && emptyChatDraft(surface, raw.fields!)) {
                const empty = this.tombstone(raw, epoch, "empty");
                store.put(empty, key);
                return { record: empty, fence: fence(empty, epoch), rejected: false };
            }
            return { record: raw, fence: fence(raw, epoch), rejected: raw.state === "tombstone" && raw.deletionReason === "invalid" };
        });
    }
    async write(key: string, surface: DraftSurface, context: string[], fields: DraftFields, contentRevision: string, sourceBaseline: string, expected: DraftFence, override = false): Promise<DraftRead> {
        if (!validDraftFields(surface, fields))
            throw new DraftFailure("corrupt");
        const result = await this.transaction(async (tx) => {
            const epoch = await this.epoch(tx);
            const store = tx.objectStore("drafts");
            const raw: unknown = await request(store.get(key));
            if (raw !== undefined && !validRecord(raw, key, epoch))
                throw new DraftFailure("corrupt");
            const current = raw as DraftRecord | undefined;
            if (current?.state === "live" && (current.expiresAt ?? 0) <= Date.now())
                throw new DraftFailure("conflict");
            const actual = fence(current ?? null, epoch);
            if (expected.storeEpoch !== actual.storeEpoch || (!override && !same(expected, actual)))
                throw new DraftFailure("conflict");
            if (emptyChatDraft(surface, fields)) {
                const empty = this.tombstone(current ?? { draftKey: key, surfaceId: surface, context, writeRevision: 0 } as DraftRecord, epoch, "empty");
                store.put(empty, key);
                return { record: empty, fence: fence(empty, epoch), rejected: false };
            }
            const now = Date.now();
            const record: DraftRecord = { schemaVersion: 1, state: "live", draftKey: key, surfaceId: surface, context,
                fields, contentRevision, sourceBaseline, storeEpoch: epoch, writeRevision: actual.writeRevision + 1, updatedAt: now, expiresAt: now + LIFETIME };
            if (new TextEncoder().encode(JSON.stringify(record)).length > LIMIT)
                throw new DraftFailure("oversize");
            store.put(record, key);
            return { record, fence: fence(record, epoch), rejected: false };
        });
        this.changed();
        return result;
    }
    async remove(key: string, expected: DraftFence | {
        contentRevision: string;
    }, reason: Reason, surface: DraftSurface, context: string[], submittedInstance?: symbol): Promise<boolean> {
        const result = await this.transaction(async (tx) => {
            const epoch = await this.epoch(tx);
            const store = tx.objectStore("drafts");
            const raw: unknown = await request(store.get(key));
            if (raw !== undefined && !validRecord(raw, key, epoch))
                throw new DraftFailure("corrupt");
            const current = raw as DraftRecord | undefined;
            if ("contentRevision" in expected) {
                if (current?.state !== "live" || current.contentRevision !== expected.contentRevision || (current.expiresAt ?? 0) <= Date.now())
                    return { removed: false, fence: fence(current ?? null, epoch) };
            }
            else if (!same(expected, fence(current ?? null, epoch)))
                throw new DraftFailure("conflict");
            if (current?.state === "tombstone")
                return { removed: true, fence: fence(current, epoch) };
            const tombstone = this.tombstone(current ?? { draftKey: key, surfaceId: surface, context, writeRevision: 0 } as DraftRecord, epoch, reason);
            store.put(tombstone, key);
            return { removed: true, fence: fence(tombstone, epoch) };
        });
        const change = result.removed && reason === "submitted" && submittedInstance && "contentRevision" in expected
            ? { key, contentRevision: expected.contentRevision, instance: submittedInstance, fence: result.fence } : undefined;
        this.changed(change);
        return result.removed;
    }
}
export const browserDrafts = new DraftStore();
