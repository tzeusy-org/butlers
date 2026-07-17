package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"slices"
	"strings"
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

	snapshotTaken := make(chan struct{})
	releaseHandoff := make(chan struct{})
	defer func() {
		select {
		case <-releaseHandoff:
		default:
			close(releaseHandoff)
		}
	}()
	srv.afterBackfillSnapshot = func() {
		close(snapshotTaken)
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
	case <-snapshotTaken:
	case <-time.After(time.Second):
		t.Fatal("backfill did not reach the snapshot barrier")
	}

	// Pause at the former snapshot-to-handoff seam. The corrected implementation
	// has already installed the matching pending replay and handoff state before
	// this barrier releases the live publisher.
	live := &bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "between-snapshot-and-handoff",
		ChatJID:   "chat@s.whatsapp.net",
		Timestamp: now.Unix(),
	}
	// A repeated HistorySync cannot repair a lost live event: the replay cache
	// deduplicates it before RecordHistoryEvent can enqueue it again.
	srv.PublishEvent(live)
	srv.RecordHistoryEvent(live)
	close(releaseHandoff)
	<-backfillDone
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
