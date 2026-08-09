package events

import (
	"sync"
	"time"

	waTypes "go.mau.fi/whatsmeow/types"
)

// GroupInfoFetcher resolves a group's live participant count, e.g. via
// whatsmeow's Client.GetGroupInfo. Abstracted as an interface so
// GroupInfoCache is unit-testable without a live WhatsApp connection.
type GroupInfoFetcher interface {
	ParticipantCount(jid waTypes.JID) (int, error)
}

type groupCacheEntry struct {
	count   int
	expires time.Time
}

const (
	// defaultFailureTTL is how long a fetch failure is negative-cached. Kept
	// short relative to the success TTL: a persistently-broken group (e.g.
	// the bot was removed) must not spawn a refresh goroutine on every
	// single message, but a real recovery (re-added, connectivity restored)
	// should be picked up again within a minute or two, not an hour.
	defaultFailureTTL = 60 * time.Second
)

// GroupInfoCache resolves and caches WhatsApp group participant counts with
// a TTL, so an active group doesn't trigger a GetGroupInfo network round
// trip (a request to WhatsApp's servers) on every single message.
//
// whatsmeow dispatches events serially on a single goroutine (one handler
// call at a time) — a blocking network fetch inside a message handler stalls
// delivery of every subsequent WhatsApp event, not just this group's. So
// ParticipantCount never blocks on the network: it returns the best
// available cached value immediately (falling back to 0/unknown on a cold
// cache) and refreshes stale/missing entries in a background goroutine,
// deduplicated per JID so a burst of messages in one group doesn't spawn a
// fetch per message.
type GroupInfoCache struct {
	fetcher    GroupInfoFetcher
	ttl        time.Duration
	failureTTL time.Duration

	mu         sync.Mutex
	entries    map[string]groupCacheEntry
	refreshing map[string]bool
}

// NewGroupInfoCache constructs a cache backed by fetcher with the given TTL
// for successful lookups. Failures use a shorter, fixed negative-cache TTL
// (see defaultFailureTTL) regardless of ttl.
func NewGroupInfoCache(fetcher GroupInfoFetcher, ttl time.Duration) *GroupInfoCache {
	return &GroupInfoCache{
		fetcher:    fetcher,
		ttl:        ttl,
		failureTTL: defaultFailureTTL,
		entries:    make(map[string]groupCacheEntry),
		refreshing: make(map[string]bool),
	}
}

// ParticipantCount returns the cached participant count for jid without
// ever blocking on a network call. On a cache miss or expired entry, it
// returns the best available value immediately (the stale count if one
// exists, otherwise 0/unknown) and kicks off a background refresh — the
// next call (this message or the next one) picks up the fresh value once
// the refresh completes.
//
// Callers should treat 0 as "omit the field", never as a real group size of
// zero — see BridgeEvent.ParticipantCount.
func (c *GroupInfoCache) ParticipantCount(jid waTypes.JID) int {
	key := jid.String()

	c.mu.Lock()
	entry, ok := c.entries[key]
	fresh := ok && time.Now().Before(entry.expires)
	if !fresh && !c.refreshing[key] {
		c.refreshing[key] = true
		go c.refresh(jid, key)
	}
	c.mu.Unlock()

	if ok {
		// Serve the cached value even if stale — better than 0/unknown while
		// the background refresh is in flight, and never wrong for longer
		// than one refresh cycle.
		return entry.count
	}
	return 0
}

// refresh fetches jid's participant count and updates the cache. Runs on
// its own goroutine; must never be called synchronously from the hot path.
func (c *GroupInfoCache) refresh(jid waTypes.JID, key string) {
	defer func() {
		c.mu.Lock()
		delete(c.refreshing, key)
		c.mu.Unlock()
	}()

	count, err := c.fetcher.ParticipantCount(jid)

	c.mu.Lock()
	defer c.mu.Unlock()
	if err != nil {
		// Negative-cache the failure so a persistently-broken group (bot
		// removed, permission revoked) doesn't retry on every message —
		// but only briefly, so a real recovery is picked up promptly.
		c.entries[key] = groupCacheEntry{count: 0, expires: time.Now().Add(c.failureTTL)}
		return
	}
	c.entries[key] = groupCacheEntry{count: count, expires: time.Now().Add(c.ttl)}
}
