package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
)

func TestServer_Backfill_RepeatedAcceptedRequestsKeepPendingReplayUniqueAndBounded(t *testing.T) {
	srv := NewServer("/tmp/test-wa-bridge-repeated-backfill.sock", func() {})
	srv.SetState(StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return true, true })

	now := time.Now().Unix()
	for i := range maxReplayEvents {
		srv.RecordHistoryEvent(&bridgeEvents.BridgeEvent{
			Type:      "text",
			MessageID: fmt.Sprintf("message-%d", i),
			ChatJID:   "chat@s.whatsapp.net",
			Timestamp: now,
		})
	}

	const body = `{"schema_version":"whatsapp.backfill.v1","window_hours":1}`
	for range 2 {
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, "/backfill", strings.NewReader(body))
		srv.handleBackfill(recorder, request)

		if recorder.Code != http.StatusOK {
			t.Fatalf("POST /backfill status: got %d want %d", recorder.Code, http.StatusOK)
		}
		var acknowledgement map[string]any
		if err := json.NewDecoder(recorder.Body).Decode(&acknowledgement); err != nil {
			t.Fatalf("decode acknowledgement: %v", err)
		}
		if got := acknowledgement["replay_event_count"]; got != float64(maxReplayEvents) {
			t.Fatalf("replay_event_count: got %v want %d", got, maxReplayEvents)
		}
	}

	if got := len(srv.pendingReplay); got != maxReplayEvents {
		t.Fatalf("pending replay length: got %d want %d", got, maxReplayEvents)
	}
	seen := make(map[string]struct{}, len(srv.pendingReplay))
	for _, event := range srv.pendingReplay {
		key := event.ChatJID + "\x00" + event.MessageID
		if _, duplicate := seen[key]; duplicate {
			t.Fatalf("pending replay contains duplicate event %q", key)
		}
		seen[key] = struct{}{}
	}
}

func TestServer_Backfill_BoundsAndDeduplicatesLiveHandoffBeforeFirstSubscription(t *testing.T) {
	srv := NewServer("/tmp/test-wa-bridge-live-handoff.sock", func() {})
	srv.SetState(StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return true, true })

	acknowledgement := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/backfill",
		strings.NewReader(`{"schema_version":"whatsapp.backfill.v1","window_hours":1}`),
	)
	srv.handleBackfill(acknowledgement, request)
	if acknowledgement.Code != http.StatusOK {
		t.Fatalf("POST /backfill status: got %d want %d", acknowledgement.Code, http.StatusOK)
	}

	now := time.Now().Unix()
	for i := range maxReplayEvents {
		srv.PublishEvent(&bridgeEvents.BridgeEvent{
			Type:      "text",
			MessageID: fmt.Sprintf("live-%d", i),
			ChatJID:   "chat@s.whatsapp.net",
			Timestamp: now,
		})
	}
	srv.PublishEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "live-overflow",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now,
	})
	// The replay cache has evicted live-0 by now. A later HistorySync copy must
	// still not duplicate the first-subscriber handoff queue.
	srv.RecordHistoryEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "live-0",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now,
	})

	if got := len(srv.pendingReplay); got != maxReplayEvents {
		t.Fatalf("pending handoff length: got %d want %d", got, maxReplayEvents)
	}
	if got, want := srv.pendingReplay[0].MessageID, "live-0"; got != want {
		t.Fatalf("first pending handoff: got %q want %q", got, want)
	}
	if got, want := srv.pendingReplay[len(srv.pendingReplay)-1].MessageID,
		fmt.Sprintf("live-%d", maxReplayEvents-1); got != want {
		t.Fatalf("last pending handoff: got %q want %q", got, want)
	}
	seen := make(map[string]struct{}, len(srv.pendingReplay))
	for _, event := range srv.pendingReplay {
		key := replayEventKey(event)
		if _, duplicate := seen[key]; duplicate {
			t.Fatalf("pending handoff contains duplicate event %q", key)
		}
		seen[key] = struct{}{}
	}
	if _, found := seen["chat@s.whatsapp.net\x00live-overflow"]; found {
		t.Fatal("pending handoff retained event beyond its bounded capacity")
	}

	drainCtx, cancelDrain := context.WithCancel(context.Background())
	cancelDrain()
	drainRequest := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(drainCtx)
	drainRecorder := httptest.NewRecorder()
	srv.handleEvents(drainRecorder, drainRequest)
	if !strings.Contains(drainRecorder.Body.String(), `"message_id":"live-0"`) {
		t.Fatal("first subscriber did not receive the live handoff")
	}
	if got := len(srv.pendingReplay); got != 0 {
		t.Fatalf("pending handoff after first subscriber drain: got %d want 0", got)
	}
	if srv.liveHandoffArmed {
		t.Fatal("live handoff remained armed after the first subscriber drain")
	}

	srv.PublishEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "after-first-subscriber",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now,
	})
	if got := len(srv.pendingReplay); got != 0 {
		t.Fatalf("later live event was queued after first subscriber drain: got %d want 0", got)
	}
}

func TestServer_Backfill_QueuesLivePublishBetweenSnapshotAndHandoffExactlyOnce(t *testing.T) {
	srv := NewServer("/tmp/test-wa-bridge-snapshot-handoff.sock", func() {})
	srv.SetState(StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return true, true })

	now := time.Now()
	historical := &bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "snapshot-message",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now.Add(-30 * time.Minute).Unix(),
	}
	srv.RecordHistoryEvent(historical)

	live := &bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "between-snapshot-and-handoff",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now.Unix(),
	}

	type snapshotBarrierObservation struct {
		subsMuHeld         bool
		replayMuHeld       bool
		pendingReplayCount int
		liveHandoffArmed   bool
	}
	barrierReached := make(chan snapshotBarrierObservation, 1)
	releaseHandoff := make(chan struct{})
	publisherAtReplayLockBoundary := make(chan struct{})
	publisherReplayLockContended := make(chan bool, 1)
	allowPublisherReplayLock := make(chan struct{})
	publisherReleasedToReplayLock := make(chan struct{})
	publisherDone := make(chan struct{})
	defer func() {
		select {
		case <-allowPublisherReplayLock:
		default:
			close(allowPublisherReplayLock)
		}
		select {
		case <-releaseHandoff:
		default:
			close(releaseHandoff)
		}
	}()
	var publisherReplayLockOnce sync.Once
	srv.beforeReplayRecordLock = func() {
		publisherReplayLockOnce.Do(func() {
			// This runs in PublishEvent's recordReplayEvent call immediately
			// before its production replayMu.Lock. A failed TryLock is the
			// deterministic proof that the publisher made a real attempt on
			// the exact transition mutex while requestBackfill still owns it.
			contended := !srv.replayMu.TryLock()
			if !contended {
				srv.replayMu.Unlock()
			}
			publisherReplayLockContended <- contended
			close(publisherAtReplayLockBoundary)
			<-allowPublisherReplayLock
			close(publisherReleasedToReplayLock)
		})
	}
	srv.afterBackfillSnapshot = func() {
		subsMuHeld := !srv.subsMu.TryLock()
		if !subsMuHeld {
			srv.subsMu.Unlock()
		}
		replayMuHeld := !srv.replayMu.TryLock()
		pendingReplayCount := len(srv.pendingReplay)
		liveHandoffArmed := srv.liveHandoffArmed
		if !replayMuHeld {
			srv.replayMu.Unlock()
		}
		barrierReached <- snapshotBarrierObservation{
			subsMuHeld:         subsMuHeld,
			replayMuHeld:       replayMuHeld,
			pendingReplayCount: pendingReplayCount,
			liveHandoffArmed:   liveHandoffArmed,
		}
		go func() {
			srv.PublishEvent(live)
			// This HistorySync duplicate cannot restore a lost handoff: the
			// replay cache already contains the live event.
			srv.RecordHistoryEvent(live)
			close(publisherDone)
		}()
		<-releaseHandoff
	}

	acknowledgement := httptest.NewRecorder()
	request := httptest.NewRequest(
		http.MethodPost,
		"/backfill",
		strings.NewReader(`{"schema_version":"whatsapp.backfill.v1","window_hours":1}`),
	)
	backfillDone := make(chan struct{})
	go func() {
		srv.handleBackfill(acknowledgement, request)
		close(backfillDone)
	}()

	select {
	case observation := <-barrierReached:
		if !observation.subsMuHeld || !observation.replayMuHeld {
			t.Fatalf(
				"snapshot barrier must hold the subscriber and replay transition locks: subsMuHeld=%t replayMuHeld=%t",
				observation.subsMuHeld,
				observation.replayMuHeld,
			)
		}
		if observation.pendingReplayCount != 0 || observation.liveHandoffArmed {
			t.Fatalf(
				"snapshot barrier must precede pending replay and handoff arm: pendingReplayCount=%d liveHandoffArmed=%t",
				observation.pendingReplayCount,
				observation.liveHandoffArmed,
			)
		}
	case <-time.After(time.Second):
		t.Fatal("backfill did not reach the snapshot barrier")
	}

	// PublishEvent must make a real failed lock attempt at
	// recordReplayEvent's replay-lock boundary while the request still owns both
	// transition locks. A goroutine launch alone would let a serial post-arm
	// schedule pass without exercising this interleaving.
	select {
	case <-publisherAtReplayLockBoundary:
	case <-time.After(time.Second):
		t.Fatal("live publisher did not reach the recordReplayEvent replay-lock boundary")
	}
	if contended := <-publisherReplayLockContended; !contended {
		t.Fatal("live publisher reached an unlocked replay mutex before the handoff transition")
	}
	close(allowPublisherReplayLock)
	select {
	case <-publisherReleasedToReplayLock:
	case <-time.After(time.Second):
		t.Fatal("live publisher did not advance from the contended replay-lock boundary")
	}
	close(releaseHandoff)
	select {
	case <-backfillDone:
	case <-time.After(time.Second):
		t.Fatal("backfill did not complete after releasing the transition barrier")
	}
	select {
	case <-publisherDone:
	case <-time.After(time.Second):
		t.Fatal("live publisher did not complete after backfill handoff")
	}
	if acknowledgement.Code != http.StatusOK {
		t.Fatalf("POST /backfill status: got %d want %d", acknowledgement.Code, http.StatusOK)
	}

	drainCtx, cancelDrain := context.WithCancel(context.Background())
	cancelDrain()
	drainRequest := httptest.NewRequest(http.MethodGet, "/events", nil).WithContext(drainCtx)
	drainRecorder := httptest.NewRecorder()
	srv.handleEvents(drainRecorder, drainRequest)

	var messageIDs []string
	for _, line := range strings.Split(drainRecorder.Body.String(), "\n") {
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var event struct {
			MessageID string `json:"message_id"`
		}
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &event); err != nil {
			t.Fatalf("decode SSE event: %v", err)
		}
		messageIDs = append(messageIDs, event.MessageID)
	}
	wantMessageIDs := []string{
		"snapshot-message",
		"between-snapshot-and-handoff",
	}
	if !slices.Equal(messageIDs, wantMessageIDs) {
		t.Fatalf("first SSE replay: got %v want %v", messageIDs, wantMessageIDs)
	}
}
