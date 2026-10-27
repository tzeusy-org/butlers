package events

import (
	"errors"
	"sync"
	"testing"
	"time"

	waTypes "go.mau.fi/whatsmeow/types"
)

// fakeGroupInfoFetcher announces a result before returning it. That notification
// does not mean GroupInfoCache has published the result; release can hold the
// fetch open to exercise that ordering explicitly.
type fakeGroupInfoFetcher struct {
	mu      sync.Mutex
	calls   int
	perCall []struct {
		count int
		err   error
	}
	done    chan struct{}
	release <-chan struct{}
}

func newFakeFetcher(results ...struct {
	count int
	err   error
}) *fakeGroupInfoFetcher {
	return &fakeGroupInfoFetcher{perCall: results, done: make(chan struct{}, 64)}
}

func (f *fakeGroupInfoFetcher) ParticipantCount(jid waTypes.JID) (int, error) {
	f.mu.Lock()
	idx := f.calls
	f.calls++
	f.mu.Unlock()

	if idx >= len(f.perCall) {
		idx = len(f.perCall) - 1
	}
	result := f.perCall[idx]
	f.done <- struct{}{}
	if f.release != nil {
		<-f.release
	}
	return result.count, result.err
}

func (f *fakeGroupInfoFetcher) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

// waitForFetchResult observes the fake's pre-return notification only.
func (f *fakeGroupInfoFetcher) waitForFetchResult(t *testing.T) {
	t.Helper()
	select {
	case <-f.done:
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for fetch result")
	}
}

// waitForPublication observes the cache entry and completed refresh under the
// cache mutex. In particular, a published failure (count 0) must be distinguished
// from an absent entry, which also returns 0 through ParticipantCount.
func waitForPublication(t *testing.T, cache *GroupInfoCache, jid waTypes.JID, want int) {
	t.Helper()
	timer := time.NewTimer(2 * time.Second)
	defer timer.Stop()
	ticker := time.NewTicker(time.Millisecond)
	defer ticker.Stop()
	for {
		cache.mu.Lock()
		entry, present := cache.entries[jid.String()]
		refreshing := cache.refreshing[jid.String()]
		cache.mu.Unlock()
		if present && entry.count == want && !refreshing {
			return
		}
		select {
		case <-ticker.C:
		case <-timer.C:
			t.Fatalf("cache publication: want %d, got %d (present=%t, refreshing=%t)", want, entry.count, present, refreshing)
		}
	}
}

func mustParseJID(t *testing.T, s string) waTypes.JID {
	t.Helper()
	jid, err := waTypes.ParseJID(s)
	if err != nil {
		t.Fatalf("parse JID %q: %v", s, err)
	}
	return jid
}

func newTestCache(fetcher GroupInfoFetcher, ttl time.Duration) *GroupInfoCache {
	return NewGroupInfoCache(fetcher, ttl)
}

func TestGroupInfoCache_ColdCacheReturnsZeroImmediatelyAndRefreshesInBackground(t *testing.T) {
	fetcher := newFakeFetcher(struct {
		count int
		err   error
	}{count: 15})
	release := make(chan struct{})
	var releaseOnce sync.Once
	releaseFetch := func() { releaseOnce.Do(func() { close(release) }) }
	t.Cleanup(releaseFetch)
	fetcher.release = release
	cache := newTestCache(fetcher, time.Hour)
	jid := mustParseJID(t, "123456-group@g.us")

	// The hot-path contract: a cold cache must never block on the network —
	// the first call returns 0 (unknown) immediately.
	first := cache.ParticipantCount(jid)
	if first != 0 {
		t.Errorf("expected 0 on cold cache (never block on the network), got %d", first)
	}

	fetcher.waitForFetchResult(t)
	// The old notification has arrived, but the fetch cannot return yet, so
	// the result cannot have been published. The public hot path stays cold.
	if pending := cache.ParticipantCount(jid); pending != 0 {
		t.Errorf("expected 0 before fetch returns, got %d", pending)
	}
	releaseFetch()
	waitForPublication(t, cache, jid, 15)

	second := cache.ParticipantCount(jid)
	if second != 15 {
		t.Errorf("expected 15 after background refresh lands, got %d", second)
	}
}

func TestGroupInfoCache_CachesWithinTTL(t *testing.T) {
	fetcher := newFakeFetcher(struct {
		count int
		err   error
	}{count: 15})
	cache := newTestCache(fetcher, time.Hour)
	jid := mustParseJID(t, "123456-group@g.us")

	cache.ParticipantCount(jid) // cold: 0, triggers refresh
	waitForPublication(t, cache, jid, 15)
	cache.ParticipantCount(jid) // now warm: 15

	// A burst of further calls within the TTL must not trigger additional
	// fetches.
	for range 5 {
		cache.ParticipantCount(jid)
	}

	if fetcher.callCount() != 1 {
		t.Errorf("expected exactly 1 fetch (rest should be cache hits), got %d", fetcher.callCount())
	}
}

func TestGroupInfoCache_RefetchesAfterTTLExpires(t *testing.T) {
	fetcher := newFakeFetcher(
		struct {
			count int
			err   error
		}{count: 15},
		struct {
			count int
			err   error
		}{count: 18},
	)
	cache := newTestCache(fetcher, 10*time.Millisecond)
	jid := mustParseJID(t, "123456-group@g.us")

	cache.ParticipantCount(jid)
	waitForPublication(t, cache, jid, 15)

	time.Sleep(20 * time.Millisecond)

	stale := cache.ParticipantCount(jid) // still served (stale-while-refreshing)
	if stale != 15 {
		t.Errorf("expected stale value 15 to still be served while refresh is in flight, got %d", stale)
	}
	waitForPublication(t, cache, jid, 18)

	// Check the two completed refreshes before another public lookup: if the
	// scheduler pauses longer than the unchanged 10ms TTL, that lookup may
	// legitimately start a third refresh while still returning the stored 18.
	if fetcher.callCount() != 2 {
		t.Errorf("expected 2 fetches after TTL expiry, got %d", fetcher.callCount())
	}

	fresh := cache.ParticipantCount(jid)
	if fresh != 18 {
		t.Errorf("expected 18 after TTL-triggered refresh lands, got %d", fresh)
	}
}

func TestGroupInfoCache_FetchFailureIsNegativeCachedBriefly(t *testing.T) {
	fetcher := newFakeFetcher(
		struct {
			count int
			err   error
		}{count: 0, err: errors.New("boom")},
		struct {
			count int
			err   error
		}{count: 20, err: nil},
	)
	cache := newTestCache(fetcher, time.Hour)
	jid := mustParseJID(t, "123456-group@g.us")

	cache.ParticipantCount(jid) // cold: 0, triggers refresh (fails)
	waitForPublication(t, cache, jid, 0)

	afterFailure := cache.ParticipantCount(jid)
	if afterFailure != 0 {
		t.Errorf("expected 0 after a fetch failure, got %d", afterFailure)
	}

	// A repeat call immediately after the failure must NOT trigger another
	// fetch — the failure is negative-cached (briefly), so a persistently
	// broken group doesn't spawn a fetch goroutine on every message.
	cache.ParticipantCount(jid)
	if fetcher.callCount() != 1 {
		t.Errorf("expected the failure to be negative-cached (no immediate retry), got %d calls", fetcher.callCount())
	}
}

func TestGroupInfoCache_ConcurrentCallsDedupeToOneInFlightFetch(t *testing.T) {
	fetcher := newFakeFetcher(struct {
		count int
		err   error
	}{count: 42})
	cache := newTestCache(fetcher, time.Hour)
	jid := mustParseJID(t, "123456-group@g.us")

	var wg sync.WaitGroup
	for range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			cache.ParticipantCount(jid)
		}()
	}
	wg.Wait()

	waitForPublication(t, cache, jid, 42)
	time.Sleep(20 * time.Millisecond) // let any (unwanted) duplicate fetches surface

	if fetcher.callCount() != 1 {
		t.Errorf("expected exactly 1 in-flight fetch for a burst of concurrent callers, got %d", fetcher.callCount())
	}

	final := cache.ParticipantCount(jid)
	if final != 42 {
		t.Errorf("expected 42 after the dedup'd fetch lands, got %d", final)
	}
}

func TestGroupInfoCache_DifferentJIDsCachedIndependently(t *testing.T) {
	fetcher := newFakeFetcher(
		struct {
			count int
			err   error
		}{count: 5},
		struct {
			count int
			err   error
		}{count: 300},
	)
	cache := newTestCache(fetcher, time.Hour)
	jidA := mustParseJID(t, "111-group@g.us")
	jidB := mustParseJID(t, "222-group@g.us")

	cache.ParticipantCount(jidA)
	waitForPublication(t, cache, jidA, 5)
	cache.ParticipantCount(jidB)
	waitForPublication(t, cache, jidB, 300)

	countA := cache.ParticipantCount(jidA)
	countB := cache.ParticipantCount(jidB)

	if countA != 5 {
		t.Errorf("group A: got %d want 5", countA)
	}
	if countB != 300 {
		t.Errorf("group B: got %d want 300", countB)
	}
	if fetcher.callCount() != 2 {
		t.Errorf("expected 2 fetches for 2 distinct groups, got %d", fetcher.callCount())
	}
}
