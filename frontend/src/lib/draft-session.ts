import { browserDrafts, DraftFailure, type DraftFence, type DraftRead, type DraftSubmissionChange } from "./draft-store";
import { emptyChatDraft, draftBundle, type DraftFields, type DraftSurface } from "./draft-surfaces";
export type DraftPresentation = {
    restored: boolean;
    unavailable: boolean;
    corrupt: boolean;
    conflict: boolean;
    baselineMismatch: boolean;
    closing: boolean;
    waiting: boolean;
    deletion: boolean;
    cleanup: boolean;
    submitting: boolean;
};
const initialPresentation: DraftPresentation = { restored: false, unavailable: false, corrupt: false, conflict: false, baselineMismatch: false, closing: false, waiting: false, deletion: false, cleanup: false, submitting: false };
export type DraftSubmission = {
    revision: string;
    instance: symbol;
};
/** One mounted eligible task. Storage owns ordering; the instance owns all UI callbacks. */
export class DraftSession {
    readonly instance = Symbol("draft surface");
    private active = false;
    private key = "";
    private baseline = "";
    private fence: DraftFence | null = null;
    private fields: DraftFields;
    private initial: string;
    private revision: string = crypto.randomUUID();
    private savedRevision: string | null = null;
    private pending: Promise<void> | null = null;
    private clearingSubmission: string | null = null;
    private localSubmissionChange?: DraftSubmissionChange;
    private resolving = false;
    private suppressEmpty = false;
    private submittedReset = false;
    private locallyEdited = false;
    private cleanupRevision: string | null = null;
    private listeners = new Set<() => void>();
    private closeChoices: ((confirmed: boolean) => void)[] = [];
    private unsubscribe: (() => void) | null = null;
    private serial = 0;
    private generation = 0;
    ready: Promise<void> = Promise.resolve();
    presentation = initialPresentation;
    readonly surface: DraftSurface;
    readonly context: string[];
    private restore: (fields: DraftFields) => void;
    private sourceRevision?: string | null;
    constructor(surface: DraftSurface, context: string[], fields: DraftFields, restore: (fields: DraftFields) => void, initialFields = fields, sourceRevision?: string | null) {
        this.surface = surface;
        this.context = context;
        this.restore = restore;
        this.sourceRevision = sourceRevision;
        this.fields = structuredClone(fields);
        this.initial = draftBundle(initialFields);
    }
    start() {
        this.active = true;
        const generation = ++this.generation;
        this.ready = this.initialize(generation);
    }
    subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
    private show(patch: Partial<DraftPresentation>) {
        if (!this.active)
            return;
        this.presentation = { ...this.presentation, ...patch };
        this.listeners.forEach((listener) => listener());
    }
    private get requiresBaseline() { return this.context.includes("edit"); }
    get mounted() { return this.active; }
    current(submission: DraftSubmission) { return this.active && submission.instance === this.instance && submission.revision === this.revision; }
    get dirty() { return !emptyChatDraft(this.surface, this.fields) && draftBundle(this.fields) !== this.initial; }
    private async initialize(generation: number) {
        try {
            this.key = await browserDrafts.key(this.surface, this.context);
            const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(this.initial));
            this.baseline = this.sourceRevision || Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            if (!this.active || generation !== this.generation)
                return;
            this.fence = read.fence;
            this.show({ corrupt: read.rejected });
            if (read.record?.state === "live") {
                if (this.requiresBaseline && read.record.sourceBaseline !== this.baseline)
                    this.show({ baselineMismatch: true });
                else if (this.dirty && draftBundle(read.record.fields!) !== draftBundle(this.fields))
                    this.show({ conflict: true });
                else
                    this.adopt(read);
            }
            else if (this.dirty)
                void this.flush().catch(() => { });
            this.unsubscribe = browserDrafts.subscribe((change) => {
                if (change?.key === this.key) this.localSubmissionChange = change;
                void this.reconcile();
            });
        }
        catch {
            if (generation === this.generation)
                this.show({ unavailable: true });
        }
    }
    private adopt(read: DraftRead) {
        if (!this.active)
            return;
        this.locallyEdited = false;
        this.fence = read.fence;
        const record = read.record;
        const fields = record?.state === "live" ? record.fields! : JSON.parse(this.initial) as DraftFields;
        this.fields = structuredClone(fields);
        this.revision = record?.contentRevision ?? crypto.randomUUID();
        this.savedRevision = this.revision;
        this.restore(structuredClone(fields));
        this.show({ restored: record?.state === "live", conflict: false, baselineMismatch: false, unavailable: false });
    }
    private async reconcile() {
        if (!this.active || !this.key || !this.fence || this.pending || this.resolving || this.clearingSubmission)
            return;
        try {
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            // Cleanup may have begun while this read was pending. Its own confirmation
            // owns adoption and completion; an older reconciliation cannot consume it.
            if (!this.active || this.clearingSubmission || (read.fence.storeEpoch === this.fence.storeEpoch && read.fence.writeRevision === this.fence.writeRevision))
                return;
            const change = this.localSubmissionChange;
            // A retired same-tab submission may clear durable C, but not the fields of
            // a different mounted instance that restored C while that request was pending.
            // Bind the exception to the exact acknowledged tombstone fence; remote or
            // subsequently changed records still follow ordinary adoption/conflict rules.
            if (change?.key === this.key && change.instance !== this.instance &&
                change.contentRevision === this.revision && read.record?.state === "tombstone" &&
                read.record.deletionReason === "submitted" &&
                read.fence.storeEpoch === change.fence.storeEpoch && read.fence.writeRevision === change.fence.writeRevision) {
                this.fence = read.fence;
                this.revision = crypto.randomUUID();
                this.savedRevision = null;
                return;
            }
            // Compaction preserves content identity and does not manufacture a conflict.
            if (read.record?.state === "live" && read.record.contentRevision === this.revision) {
                this.fence = read.fence;
                return;
            }
            if (!this.locallyEdited)
                this.adopt(read);
            else
                this.show({ conflict: true });
        }
        catch {
            this.show({ unavailable: true });
        }
    }
    edit(fields: DraftFields) {
        const serialized = draftBundle(fields);
        if (this.submittedReset && serialized === this.initial)
            return;
        // A queued render of the submitted fields is not a new edit and must not
        // release the reset barrier before React renders the acknowledged empty fields.
        if (!this.active || serialized === draftBundle(this.fields))
            return;
        this.submittedReset = false;
        if (this.suppressEmpty && Object.values(fields).every((value) => value === ""))
            return;
        this.suppressEmpty = false;
        this.fields = structuredClone(fields);
        this.locallyEdited = true;
        this.revision = crypto.randomUUID();
        this.show({ restored: false, cleanup: false, ...(this.clearingSubmission ? { submitting: false } : {}) });
        if (!this.resolving && !this.presentation.conflict && !this.presentation.baselineMismatch)
            void this.flush().catch(() => { });
    }
    async flush(): Promise<void> {
        if (!this.key || !this.fence) {
            await this.ready;
            if (!this.key || !this.fence)
                throw new DraftFailure("unavailable");
        }
        if (this.presentation.conflict || this.presentation.baselineMismatch)
            throw new DraftFailure("conflict");
        if (this.pending) {
            await this.pending;
            if (this.savedRevision !== this.revision)
                return this.flush();
            return;
        }
        const revision = this.revision;
        if (this.savedRevision === revision) {
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            if (this.dirty && read.record?.contentRevision !== revision) {
                this.show({ conflict: true });
                throw new DraftFailure("conflict");
            }
            this.fence = read.fence;
            return;
        }
        const fields = structuredClone(this.fields);
        const empty = emptyChatDraft(this.surface, fields) || draftBundle(fields) === this.initial;
        this.pending = (async () => {
            if (empty)
                await browserDrafts.remove(this.key, this.fence!, "empty", this.surface, this.context);
            else
                await browserDrafts.write(this.key, this.surface, this.context, fields, revision, this.baseline, this.fence!);
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            if (!empty && read.record?.contentRevision !== revision)
                throw new DraftFailure("conflict");
            this.fence = read.fence;
            this.savedRevision = revision;
            this.show({ unavailable: false });
        })().catch(async (error: unknown) => {
            if (error instanceof DraftFailure && error.category === "conflict") {
                await browserDrafts.read(this.key, this.surface, this.context).catch(() => null);
                this.show({ conflict: true });
            }
            else
                this.show({ unavailable: true });
            throw error;
        }).finally(() => { this.pending = null; });
        await this.pending;
        if (this.savedRevision !== this.revision)
            return this.flush();
    }
    async chooseOther() {
        const revision = this.revision;
        try {
            await this.ready;
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            if (this.active && revision === this.revision)
                this.adopt(read);
        }
        catch {
            this.show({ unavailable: true });
        }
    }
    async chooseThis() {
        if (this.resolving) return;
        this.resolving = true;
        // An explicit replacement is a successor even when its bytes match an old submission.
        const revision = crypto.randomUUID();
        this.revision = revision;
        const fields = structuredClone(this.fields);
        try {
            await this.ready;
            const current = await browserDrafts.read(this.key, this.surface, this.context);
            if (!this.active || revision !== this.revision)
                return;
            // The explicit choice observes the current epoch; ordinary writes never
            // receive this exemption. A later epoch still makes this transaction fail.
            const read = await browserDrafts.write(this.key, this.surface, this.context, fields, revision, this.baseline, current.fence, true);
            this.fence = read.fence;
            this.savedRevision = revision;
            this.show({ conflict: false, baselineMismatch: false, unavailable: false });

        }
        catch {
            this.show({ unavailable: true });
        }
        finally {
            this.resolving = false;
            if (this.revision !== revision) void this.flush().catch(() => {});
        }
    }
    async discard(closeSerial?: number): Promise<boolean> {
        if (this.resolving) return false;
        this.resolving = true;
        const revision = this.revision;
        try {
            await this.ready;
            if (this.pending)
                await this.pending.catch(() => { });
            const read = await browserDrafts.read(this.key, this.surface, this.context);
            // A cleanup retry acknowledges only the revision accepted by the server.
            // It must never discard a successor created in another tab.
            if (revision !== this.revision || (closeSerial !== undefined && closeSerial !== this.serial))
                return false;
            const cleanup = this.cleanupRevision;
            await browserDrafts.remove(this.key, cleanup ? { contentRevision: cleanup } : read.fence, "discarded", this.surface, this.context);
            const confirmation = await browserDrafts.read(this.key, this.surface, this.context);
            if (confirmation.record?.state === "live" && (!cleanup || confirmation.record.contentRevision === cleanup))
                throw new DraftFailure("conflict");
            this.fence = confirmation.fence;
            if (revision !== this.revision)
                return false;
            if (closeSerial === undefined || closeSerial === this.serial) {
                this.cleanupRevision = null;
                this.adopt(confirmation);
                this.show({ restored: false, cleanup: false });
            }
            return true;
        }
        catch {
            this.show({ unavailable: true });
            return false;
        }
        finally {
            this.resolving = false;
            if (this.revision !== revision && this.dirty) void this.flush().catch(() => {});
        }
    }
    prepare(): boolean | Promise<boolean> {
        if (this.presentation.submitting || this.presentation.cleanup)
            return false;
        this.show({ submitting: true });
        try {
            if (typeof indexedDB === "undefined") {
                this.show({ unavailable: true });
                return this.active;
            }
        }
        catch {
            this.show({ unavailable: true });
            return this.active;
        }
        return this.prepareStorage();
    }
    private async prepareStorage(): Promise<boolean> {
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
            await Promise.race([this.flush(), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new DraftFailure("timeout")), 1000); })]);
        }
        catch {
            this.show({ unavailable: true });
        }
        finally {
            clearTimeout(timer);
        }
        return this.active;
    }
    finish() { if (!this.clearingSubmission)
        this.show({ submitting: false }); }
    composerSent() { this.suppressEmpty = true; }
    capture(fields: DraftFields): DraftSubmission {
        this.edit(fields);
        return { revision: this.revision, instance: this.instance };
    }
    /** No callback executes after unmount/context replacement, including successful domain mutations. */
    async submitted(submission: DraftSubmission, complete: () => void): Promise<void> {
        if (submission.instance !== this.instance)
            return;
        // The domain effect is real even when browser cleanup is not confirmed.
        // Reset only this submitted view; preserve the private identity until cleanup resolves.
        if (this.active && this.revision === submission.revision) {
            this.clearingSubmission = submission.revision;
            this.cleanupRevision = submission.revision;
            this.submittedReset = true;
            this.restore(JSON.parse(this.initial) as DraftFields);
        }
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
            const read = await Promise.race([
                (async () => {
                    await this.ready;
                    if (this.pending)
                        await this.pending;
                    const removed = await browserDrafts.remove(this.key, { contentRevision: submission.revision }, "submitted", this.surface, this.context, this.instance);
                    const confirmation = await browserDrafts.read(this.key, this.surface, this.context);
                    if (removed && confirmation.record?.state === "live" && confirmation.record.contentRevision === submission.revision)
                        throw new DraftFailure("unavailable");
                    return confirmation;
                })(),
                new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new DraftFailure("timeout")), 1000); }),
            ]);
            if (!this.active || this.revision !== submission.revision)
                return;
            this.cleanupRevision = null;
            // A failed conditional deletion means someone else owns the key. Adopt that
            // successor before accepting further edits; never borrow its fence invisibly.
            if (read.record?.state === "live") {
                this.adopt(read);
                this.show({ cleanup: false, closing: false, deletion: false });
                return;
            }
            this.fence = read.fence;
            this.fields = JSON.parse(this.initial) as DraftFields;
            this.revision = crypto.randomUUID();
            this.savedRevision = this.revision;
            this.restore(structuredClone(this.fields));
            this.show({ restored: false, cleanup: false });
            complete();
        }
        catch {
            if (this.active && this.revision === submission.revision) {
                this.show({ unavailable: true, cleanup: true, closing: true, deletion: true });
                this.closeChoices.push((allowed) => { if (allowed && this.active && this.revision === submission.revision)
                    complete(); });
            }
        }
        finally {
            clearTimeout(timer);
            if (this.clearingSubmission === submission.revision) {
                this.clearingSubmission = null;
                this.finish();
                void this.reconcile();
            }
        }
    }
    requestClose(chat = false): Promise<boolean> {
        if (!this.active || (!this.dirty && !this.presentation.cleanup))
            return Promise.resolve(true);
        this.show({ closing: true, deletion: false });
        const decision = new Promise<boolean>((resolve) => { this.closeChoices.push(resolve); });
        if (chat)
            void this.confirmClose(false);
        return decision;
    }
    keepEditing() { this.serial++; this.show({ closing: false, waiting: false }); this.closeChoices.splice(0).forEach((resolve) => resolve(false)); }
    closeUnconfirmed() { this.serial++; this.show({ closing: false, waiting: false }); this.closeChoices.splice(0).forEach((resolve) => resolve(true)); }
    async confirmClose(deletion: boolean) {
        if (this.presentation.waiting)
            return;
        const serial = ++this.serial;
        this.show({ waiting: true, deletion });
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {
            await Promise.race([
                deletion ? this.discard(serial).then((ok) => { if (!ok)
                    throw new DraftFailure("unavailable"); }) : this.flush(),
                new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new DraftFailure("timeout")), 1000); }),
            ]);
            if (!this.active || serial !== this.serial)
                return;
            this.show({ closing: false, waiting: false });
            this.closeChoices.splice(0).forEach((resolve) => resolve(true));
        }
        catch {
            if (serial === this.serial)
                this.show({ unavailable: true, waiting: false });
        }
        finally {
            clearTimeout(timer);
        }
    }
    focus = () => { void this.reconcile(); };
    dispose() { this.active = false; this.serial++; this.generation++; this.unsubscribe?.(); this.closeChoices.splice(0).forEach((resolve) => resolve(false)); this.listeners.clear(); }
}
