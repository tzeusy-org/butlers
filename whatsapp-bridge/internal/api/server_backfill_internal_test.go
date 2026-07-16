package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
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
