package events_test

import (
	"errors"
	"sync"
	"testing"
	"time"

	waTypes "go.mau.fi/whatsmeow/types"

	"github.com/butlers/whatsapp-bridge/internal/events"
)

// fakeGroupInfoFetcher signals each completed fetch on a channel so tests
// can deterministically wait for GroupInfoCache's background refresh
// goroutine to land, instead of racing it with a sleep.
type fakeGroupInfoFetcher struct {
	mu      sync.Mutex
	calls   int
	perCall []struct {
		count int
		err   error
	}
	done chan struct{}
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
	return result.count, result.err
}

func (f *fakeGroupInfoFetcher) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.calls
}

// waitForFetch blocks until the fetcher completes at least one more fetch,
// or fails the test after a generous timeout — the deterministic
// alternative to sleeping past GroupInfoCache's background goroutine.
func (f *fakeGroupInfoFetcher) waitForFetch(t *testing.T) {
	t.Helper()
	select {
	case <-f.done:
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for background fetch to complete")
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

func newTestCache(fetcher events.GroupInfoFetcher, ttl time.Duration) *events.GroupInfoCache {
	return events.NewGroupInfoCache(fetcher, ttl)
}

func TestGroupInfoCache_ColdCacheReturnsZeroImmediatelyAndRefreshesInBackground(t *testing.T) {
	fetcher := newFakeFetcher(struct {
		count int
		err   error
	}{count: 15})
	cache := newTestCache(fetcher, time.Hour)
	jid := mustParseJID(t, "123456-group@g.us")

	// The hot-path contract: a cold cache must never block on the network —
	// the first call returns 0 (unknown) immediately.
	first := cache.ParticipantCount(jid)
	if first != 0 {
		t.Errorf("expected 0 on cold cache (never block on the network), got %d", first)
	}

	fetcher.waitForFetch(t)

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
	fetcher.waitForFetch(t)
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
	fetcher.waitForFetch(t)

	time.Sleep(20 * time.Millisecond)

	stale := cache.ParticipantCount(jid) // still served (stale-while-refreshing)
	if stale != 15 {
		t.Errorf("expected stale value 15 to still be served while refresh is in flight, got %d", stale)
	}
	fetcher.waitForFetch(t)

	fresh := cache.ParticipantCount(jid)
	if fresh != 18 {
		t.Errorf("expected 18 after TTL-triggered refresh lands, got %d", fresh)
	}
	if fetcher.callCount() != 2 {
		t.Errorf("expected 2 fetches after TTL expiry, got %d", fetcher.callCount())
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
	fetcher.waitForFetch(t)

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

	fetcher.waitForFetch(t)
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
	fetcher.waitForFetch(t)
	cache.ParticipantCount(jidB)
	fetcher.waitForFetch(t)

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
