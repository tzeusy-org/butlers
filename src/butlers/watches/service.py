"""Owner watch lifecycle on the one conditions evaluator (REQ-owner-watches-003/004; dark).

Every change that defines or widens a watch is an exact card the owner approves: the card digest
binds the predicate, source and category boundary, grant digest, delivery mode, expiry and
version. An instance fires once; the runner (S3c) re-arms the next generation. Only a model
proposes a card; mail, source text and read-check output are evidence and
produce none, and a proposal never installs a watch. A watch notifies the owner only (it has no
recipient field), at most ``MAX_ACTIVE`` are active, each lives 90 days from approval unless the
owner approves a renewal card in the last seven, and ``pause`` stops one without deleting
anything it observed. Delivery is not here: a fire is the evaluator's intent.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Protocol

from butlers.conditions import (
    ConditionEvaluator,
    Grant,
    Instance,
    RegistrationRefused,
    Snapshot,
    State,
    Tri,
)
from butlers.conditions.predicate import digest as predicate_digest
from butlers.conditions.service import check_registration
from butlers.core.goal_labels import CATEGORIES, Label
from butlers.watches.model import (
    DEFAULT_DELIVERY,
    DELIVERY_MODES,
    LIFETIME,
    MAX_ACTIVE,
    OWNER,
    SPEC_FIELDS,
    Action,
    OwnerApproval,
    Watch,
    WatchCard,
    WatchConflict,
    WatchRefused,
    WatchState,
)

GrantFor = Callable[[str], Grant]


class WatchStore(Protocol):
    def serialized(self, watch_id: str) -> AbstractAsyncContextManager[None]:
        """Hold the one lock for this watch across a read, its side effects and the CAS write."""
        ...

    async def get(self, watch_id: str) -> Watch | None: ...

    async def list_all(self) -> list[Watch]: ...

    async def write(self, watch: Watch, *, expected_version: int | None, cap: int) -> Watch: ...


@dataclass(frozen=True)
class WatchView:
    watch: Watch
    blind: bool  # an input is unmeasurable: shown blind, never healthy
    reason: str | None = None  # "grant_changed": paused because the live grant moved


def _label(categories: tuple[str, ...]) -> Label:
    return Label(frozenset({OWNER}), frozenset({OWNER}), frozenset(categories))


Withdraw = Callable[[Watch], Awaitable[None]]


class WatchService:
    def __init__(
        self,
        store: WatchStore,
        evaluator: ConditionEvaluator,
        grant_for: GrantFor,
        *,
        cap: int = MAX_ACTIVE,
        withdraw: Withdraw | None = None,
    ) -> None:
        # ``withdraw`` takes back a notice the owner has not yet been shown, for a watch that is
        # paused, deleted or expired: a closed watch must not leave a pending candidate behind.
        self.withdraw = withdraw
        self.store = store
        self.evaluator = evaluator
        self.grant_for = grant_for
        self.cap = cap

    # -- proposals: produce a card, change nothing ------------------------------------------

    def propose_create(
        self, spec: Mapping[str, Any], *, now: datetime, watch_id: str | None = None
    ) -> WatchCard:
        extra = set(spec) - SPEC_FIELDS
        if extra or "predicate" not in spec:
            raise WatchRefused(
                f"a watch spec has exactly {sorted(SPEC_FIELDS)}; got {sorted(spec)}"
            )
        delivery = spec.get("delivery", DEFAULT_DELIVERY)
        if delivery not in DELIVERY_MODES:
            raise WatchRefused("delivery must be digest or right_away")
        categories = spec.get("categories", [])
        if (
            type(categories) is not list
            or any(type(c) is not str for c in categories)
            or not set(categories) <= CATEGORIES
        ):
            raise WatchRefused("categories must be a list of known sensitivity categories")
        watch_id = watch_id or str(uuid.uuid4())
        grant = self.grant_for(watch_id)
        try:
            predicate = check_registration(
                spec["predicate"],
                grant,
                instance_id=f"{watch_id}:1",
                goal_id=watch_id,
                starts_at=now,
                expires_at=now + LIFETIME,  # validation only: the real expiry starts at approval
            )
        except RegistrationRefused as exc:
            raise WatchRefused(str(exc)) from None
        return WatchCard(
            action=Action.CREATE,
            watch_id=watch_id,
            target_version=1,
            predicate=predicate,
            grant_digest=grant.digest,
            sources=tuple(sorted(grant.sources)),
            categories=tuple(sorted(set(categories))),
            delivery=delivery,
            expires_at=None,
            lifetime=LIFETIME,
        )

    async def propose_update(
        self, watch_id: str, *, delivery: str | None = None, resume: bool = False
    ) -> WatchCard:
        watch = await self._open(watch_id)
        if delivery is not None and delivery not in DELIVERY_MODES:
            raise WatchRefused("delivery must be digest or right_away")
        return self._card(
            watch, Action.UPDATE, delivery or watch.delivery, watch.expires_at, resume
        )

    async def propose_renew(self, watch_id: str, *, now: datetime) -> WatchCard:
        watch = await self._open(watch_id)
        if not watch.due_for_renewal(now):
            raise WatchRefused("a renewal card is offered only in the last seven days")
        return self._card(watch, Action.RENEW, watch.delivery, None, lifetime=LIFETIME)

    async def propose_delete(self, watch_id: str) -> WatchCard:
        watch = await self._open(watch_id)
        return self._card(watch, Action.DELETE, watch.delivery, watch.expires_at)

    def _card(
        self,
        watch: Watch,
        action: Action,
        delivery: str,
        expires_at: datetime | None,
        resume: bool = False,
        lifetime: timedelta | None = None,
    ) -> WatchCard:
        return WatchCard(
            action=action,
            watch_id=watch.id,
            target_version=watch.version + 1,
            predicate=watch.predicate,
            grant_digest=watch.grant_digest,
            sources=watch.sources,
            categories=watch.categories,
            delivery=delivery,
            expires_at=expires_at,
            lifetime=lifetime,
            resume=resume,
        )

    # -- approval: the only way a watch changes beyond pause --------------------------------

    async def approve(self, card: WatchCard, approval: OwnerApproval, snapshot: Snapshot) -> Watch:
        if approval.origin != OWNER:
            raise WatchRefused("only the owner approves a watch")
        if approval.card_digest != card.digest:
            raise WatchRefused("the approval is for a different card")
        # Every transition of one watch is serialized: the read, the condition side effects and
        # the CAS write happen under one lock, so no two transitions interleave.
        async with self.store.serialized(card.watch_id):
            return await self._approve(card, snapshot)

    async def _approve(self, card: WatchCard, snapshot: Snapshot) -> Watch:
        now = snapshot.now
        if card.action is Action.CREATE:
            return await self._create(card, snapshot)
        current = await self.store.get(card.watch_id)
        if current is None:
            raise WatchRefused("unknown watch")
        if current.approval_digest == card.digest:
            # A replay repairs an interrupted transition.
            return await self._reconcile(current, snapshot)
        if current.state in (WatchState.EXPIRED, WatchState.DELETED):
            raise WatchRefused("the watch is closed")
        if card.target_version != current.version + 1:
            raise WatchRefused("the card is stale: the watch changed")
        if (card.predicate, card.grant_digest, card.sources, card.categories) != (
            current.predicate,
            current.grant_digest,
            current.sources,
            current.categories,
        ):
            raise WatchRefused("a card cannot change a watch's boundary; create a new watch")
        if card.action is Action.DELETE:
            return await self._delete(current, card, now)
        if card.action is Action.RENEW or (card.resume and current.state is WatchState.PAUSED):
            self._grant_is_current(card.watch_id, card.grant_digest, card.sources)
        if card.action is Action.RENEW:
            return await self._renew(current, card, snapshot)
        return await self._update(current, card, snapshot)

    async def _create(self, card: WatchCard, snapshot: Snapshot) -> Watch:
        existing = await self.store.get(card.watch_id)
        if existing is not None:
            if existing.approval_digest != card.digest:
                raise WatchRefused("the watch id is already in use")
            # A replay never re-inserts or re-registers: it only makes the condition match the
            # committed state, so a watch closed by a failed registration stays closed.
            return await self._reconcile(existing, snapshot)
        self._grant_is_current(card.watch_id, card.grant_digest, card.sources)
        watch = Watch(
            id=card.watch_id,
            version=1,
            generation=1,
            predicate=card.predicate,
            predicate_digest=predicate_digest(card.predicate),
            grant_digest=card.grant_digest,
            sources=card.sources,
            categories=card.categories,
            delivery=card.delivery,
            state=WatchState.ACTIVE,
            created_at=snapshot.now,
            expires_at=snapshot.now + (card.lifetime or LIFETIME),
            approval_digest=card.digest,
        )
        # Reserve the slot first: a refused cap registers no instance.
        stored = await self.store.write(watch, expected_version=None, cap=self.cap)
        if stored != watch:
            # Lost an insert race to the same approval.
            return await self._reconcile(stored, snapshot)
        try:
            await self._register(stored, snapshot)
        except Exception:
            closed = replace(stored, state=WatchState.DELETED, version=stored.version + 1)
            await self.store.write(closed, expected_version=stored.version, cap=self.cap)
            raise
        return stored

    def grant_is_current(self, watch: Watch) -> bool:
        """True while the live grant is the one the owner approved for this watch."""
        try:
            self._grant_is_current(watch.id, watch.grant_digest, watch.sources)
        except WatchRefused:
            return False
        return True

    async def _withdraw(self, watch: Watch) -> None:
        if self.withdraw is not None:
            await self.withdraw(watch)

    def _grant_is_current(self, watch_id: str, grant_digest: str, sources: tuple[str, ...]) -> None:
        """Refuse unless the live grant is the one the owner approved (digest and sources)."""
        grant = self.grant_for(watch_id)
        if grant.digest != grant_digest or tuple(sorted(grant.sources)) != tuple(sorted(sources)):
            raise WatchRefused("the grant changed since the card was proposed")

    async def _register(
        self,
        watch: Watch,
        snapshot: Snapshot,
        cursors: Mapping[str, int] | None = None,
    ) -> None:
        """Register this generation. Generation 1 may fire on the snapshot; every later one
        (a re-arm, a renewal, a resume or a repair) is edge-triggered: it starts unknown with the
        watch's last fire cursor as its floor, so only an event past it can fire it.
        """
        self._grant_is_current(watch.id, watch.grant_digest, watch.sources)
        seed = watch.generation == 1
        if not seed and cursors is None:
            cursors = await self._last_fire_cursors(watch)
        await self.evaluator.register(
            instance_id=watch.instance_id,
            predicate=watch.predicate,
            grant=self.grant_for(watch.id),
            goal_id=watch.id,
            label=_label(watch.categories),
            snapshot=snapshot,
            expires_at=watch.expires_at,
            cursors=dict(cursors) if cursors else None,
            seed=seed,
        )

    async def _last_fire_cursors(self, watch: Watch) -> dict[str, int]:
        """Cursors of the latest earlier generation that fired; empty when none ever did."""
        async with self.evaluator.store.transaction() as tx:
            for generation in range(watch.generation - 1, 0, -1):
                prior = await tx.get_instance(f"{watch.id}:{generation}")
                if prior is not None and prior.state is State.FIRED:
                    return dict(prior.cursors)
        return {}

    async def _cancel(self, watch: Watch, now: datetime) -> None:
        try:
            await self.evaluator.cancel(watch.instance_id, now=now)
        except KeyError:
            pass  # never registered: nothing to stop

    async def _has_instance(self, watch: Watch) -> bool:
        async with self.evaluator.store.transaction() as tx:
            return await tx.get_instance(watch.instance_id) is not None

    async def _reconcile(self, watch: Watch, snapshot: Snapshot) -> Watch:
        """Make the condition match the committed state: ACTIVE has a live one, others none.

        An ACTIVE watch that has no condition and whose grant has since changed cannot be
        registered, and must never stay ACTIVE without one: it is paused instead, and the view
        shows ``grant_changed`` until the owner approves a card on the current grant.
        """
        if watch.state is WatchState.ACTIVE:
            if not await self._has_instance(watch):
                if not self.grant_is_current(watch):
                    return await self._pause_for_grant(watch, snapshot.now)
                await self._register(watch, snapshot)
            if (
                watch.generation > 1
            ):  # a renewal that crashed before stopping the generation it replaced
                await self._cancel(replace(watch, generation=watch.generation - 1), snapshot.now)
            return watch
        await self._cancel(watch, snapshot.now)
        await self._withdraw(watch)
        return watch

    async def _pause_for_grant(self, watch: Watch, now: datetime) -> Watch:
        paused = replace(
            watch, state=WatchState.PAUSED, version=watch.version + 1, pause_reason="grant"
        )
        try:
            written = await self.store.write(paused, expected_version=watch.version, cap=self.cap)
        except WatchConflict:
            return await self.store.get(watch.id) or watch  # changed meanwhile: that row stands
        await self._withdraw(watch)
        return written

    async def _commit_with_registration(
        self,
        watch: Watch,
        expected_version: int,
        snapshot: Snapshot,
        cursors: Mapping[str, int] | None = None,
    ) -> Watch:
        """Register the new generation, then commit it; undo the registration if the CAS loses.

        Register-before-commit means a committed ACTIVE watch always has its condition.
        """
        await self._register(watch, snapshot, cursors)
        try:
            return await self.store.write(watch, expected_version=expected_version, cap=self.cap)
        except BaseException:
            committed = await self.store.get(watch.id)
            if committed is None or committed.generation != watch.generation:
                await self._cancel(watch, snapshot.now)  # nothing committed owns this generation
            raise

    async def _delete(self, current: Watch, card: WatchCard, now: datetime) -> Watch:
        closed = replace(
            current,
            state=WatchState.DELETED,
            version=current.version + 1,
            approval_digest=card.digest,
        )
        # Commit first, then stop the condition; a replay of the approval finishes a stop that
        # was interrupted, so a recorded deletion never keeps a live condition for long.
        written = await self.store.write(closed, expected_version=current.version, cap=self.cap)
        await self._cancel(current, now)
        await self._withdraw(current)
        return written

    async def _renew(self, current: Watch, card: WatchCard, snapshot: Snapshot) -> Watch:
        if not current.due_for_renewal(snapshot.now):
            raise WatchRefused("a renewal is approved only in the last seven days")
        renewed = replace(
            current,
            version=current.version + 1,
            generation=current.generation + 1,
            expires_at=snapshot.now + (card.lifetime or LIFETIME),
            approval_digest=card.digest,
        )
        written = await self._commit_with_registration(renewed, current.version, snapshot)
        await self._cancel(current, snapshot.now)
        return written

    async def _update(self, current: Watch, card: WatchCard, snapshot: Snapshot) -> Watch:
        if card.expires_at != current.expires_at:
            raise WatchRefused("an update cannot change expiry; renew instead")
        changed = replace(
            current,
            version=current.version + 1,
            delivery=card.delivery,
            approval_digest=card.digest,
        )
        if card.resume and current.state is WatchState.PAUSED:
            changed = replace(
                changed,
                state=WatchState.ACTIVE,
                generation=current.generation + 1,
                pause_reason=None,
            )
            return await self._commit_with_registration(changed, current.version, snapshot)
        return await self.store.write(changed, expected_version=current.version, cap=self.cap)

    # -- narrowing and housekeeping ---------------------------------------------------------

    async def pause(self, watch_id: str, *, origin: str, now: datetime) -> Watch:
        """``never`` or disable: stop the watch, keep everything it observed (a narrowing)."""
        if origin != OWNER:
            raise WatchRefused("only the owner pauses a watch")
        async with self.store.serialized(watch_id):
            watch = await self.store.get(watch_id)
            if watch is None:
                raise WatchRefused("unknown watch")
            if watch.state is WatchState.PAUSED:
                await self._cancel(watch, now)  # a repeat finishes an interrupted stop
                await self._withdraw(watch)
                return watch
            if watch.state is not WatchState.ACTIVE:
                raise WatchRefused("the watch is closed")
            paused = replace(
                watch, state=WatchState.PAUSED, version=watch.version + 1, pause_reason="owner"
            )
            written = await self.store.write(paused, expected_version=watch.version, cap=self.cap)
            await self._cancel(watch, now)
            await self._withdraw(watch)
            return written

    async def instance(self, watch: Watch) -> Instance | None:
        """The evaluator instance of this watch's current generation."""
        async with self.evaluator.store.transaction() as tx:
            return await tx.get_instance(watch.instance_id)

    async def rearm(
        self, watch: Watch, cursors: Mapping[str, int], snapshot: Snapshot
    ) -> Watch | None:
        """Register the next generation after a fire; its floor is the fired one's cursors.

        Serialized with every other transition of the watch and judged against the committed row,
        so a runner holding a stale read, a racing runner or a replay arms at most one next
        generation. An expired or no longer ACTIVE watch is not rearmed.
        """
        async with self.store.serialized(watch.id):
            current = await self.store.get(watch.id)
            if (
                current is None
                or current.state is not WatchState.ACTIVE
                or current.generation != watch.generation
                or snapshot.now >= current.expires_at
            ):
                return None
            following = replace(
                current, generation=current.generation + 1, version=current.version + 1
            )
            return await self._commit_with_registration(
                following, current.version, snapshot, cursors
            )

    async def repair(self, watch: Watch, snapshot: Snapshot) -> bool:
        """Register the condition of an ACTIVE watch that has none; True when it did."""
        async with self.store.serialized(watch.id):
            current = await self.store.get(watch.id)
            if current is None or current.state is not WatchState.ACTIVE:
                return False
            return await self.repair_locked(current, snapshot)

    async def repair_locked(self, current: Watch, snapshot: Snapshot) -> bool:
        """``repair`` for a caller that already holds this watch's transition lock."""
        if await self._has_instance(current):
            return False
        await self._register(current, snapshot)
        return True

    async def renewal_due(self, now: datetime) -> list[Watch]:
        return [w for w in await self.store.list_all() if w.due_for_renewal(now)]

    async def sweep_closed(self) -> int:
        """Withdraw pending notices of every watch that is paused, deleted or expired.

        Idempotent, so it runs at startup and on every tick: a crash between a close and its
        withdrawal, or a candidate proposed by a runner that lost the race, never outlives the
        next sweep. Each watch is re-read under its transition lock, so a watch that was resumed
        meanwhile keeps its notices. Returns how many closed watches it swept.
        """
        swept = 0
        for listed in await self.store.list_all():
            if listed.state is WatchState.ACTIVE:
                continue
            async with self.store.serialized(listed.id):
                current = await self.store.get(listed.id)
                if current is not None and current.state is not WatchState.ACTIVE:
                    await self._withdraw(current)
                    swept += 1
        return swept

    async def expire_due(self, now: datetime) -> list[Watch]:
        """Close every unrenewed watch past its expiry; observations and receipts stay."""
        expired = []
        for listed in await self.store.list_all():
            async with self.store.serialized(listed.id):
                watch = await self.store.get(listed.id)  # re-read: it may have changed
                if (
                    watch is None
                    or watch.state not in (WatchState.ACTIVE, WatchState.PAUSED)
                    or now < watch.expires_at
                ):
                    continue
                closed = replace(watch, state=WatchState.EXPIRED, version=watch.version + 1)
                expired.append(
                    await self.store.write(closed, expected_version=watch.version, cap=self.cap)
                )
                try:
                    await self.evaluator.evaluate(watch.instance_id, [], now=now)
                except KeyError:
                    pass
                await self._withdraw(watch)
        return expired

    async def view(self, watch_id: str) -> WatchView:
        watch = await self.store.get(watch_id)
        if watch is None:
            raise WatchRefused("unknown watch")
        async with self.evaluator.store.transaction() as tx:
            instance = await tx.get_instance(watch.instance_id)
        blind = (
            instance is not None and instance.state is State.OPEN and instance.result is Tri.UNKNOWN
        )
        reason = (
            "grant_changed"
            if watch.state is WatchState.PAUSED
            and watch.pause_reason != "owner"
            and not self.grant_is_current(watch)
            else None
        )
        return WatchView(watch, blind, reason)

    async def _open(self, watch_id: str) -> Watch:
        watch = await self.store.get(watch_id)
        if watch is None or watch.state in (WatchState.EXPIRED, WatchState.DELETED):
            raise WatchRefused("no such open watch")
        return watch
