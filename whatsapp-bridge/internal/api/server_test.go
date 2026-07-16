package api_test

import (
	"bufio"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/butlers/whatsapp-bridge/internal/api"
	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
)

// dialUnix returns an *http.Client that connects to the given Unix socket path.
func dialUnix(sockPath string) *http.Client {
	return &http.Client{
		Transport: &http.Transport{
			DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, "unix", sockPath)
			},
		},
	}
}

func TestServer_Status_DefaultState(t *testing.T) {
	srv := api.NewServer("/tmp/test-wa-bridge-status.sock", func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix("/tmp/test-wa-bridge-status.sock")
	resp, err := client.Get("http://localhost/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("status code: got %d want %d", resp.StatusCode, http.StatusOK)
	}

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode body: %v", err)
	}

	if body["state"] != "connecting" {
		t.Errorf("state: got %v want connecting", body["state"])
	}
	if body["phone"] != nil {
		t.Errorf("phone: expected nil, got %v", body["phone"])
	}
	if _, ok := body["uptime_s"]; !ok {
		t.Error("uptime_s missing from status response")
	}
}

func TestServer_Status_AfterSetState(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-setstate.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	defer resp.Body.Close()

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode body: %v", err)
	}

	if body["state"] != "connected" {
		t.Errorf("state: got %v want connected", body["state"])
	}
	if body["phone"] == nil {
		t.Error("phone should not be nil when connected")
	}
}

func TestServer_Send_NotConnected(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-send-nc.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	body := strings.NewReader(`{"recipient":"1234567890@s.whatsapp.net","text":"hello"}`)
	resp, err := client.Post("http://localhost/send", "application/json", body)
	if err != nil {
		t.Fatalf("POST /send: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status code: got %d want %d", resp.StatusCode, http.StatusServiceUnavailable)
	}

	var errResp map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&errResp); err != nil {
		t.Fatalf("decode error body: %v", err)
	}
	if errResp["error"] != "not connected" {
		t.Errorf("error: got %q want %q", errResp["error"], "not connected")
	}
}

func TestServer_Send_Connected_NoSendFn(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-send-nosend.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")

	client := dialUnix(sockPath)
	body := strings.NewReader(`{"recipient":"1234567890@s.whatsapp.net","text":"hello"}`)
	resp, err := client.Post("http://localhost/send", "application/json", body)
	if err != nil {
		t.Fatalf("POST /send: %v", err)
	}
	defer resp.Body.Close()

	// No sendFn set — should return 503 with "send not available".
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status code: got %d want %d", resp.StatusCode, http.StatusServiceUnavailable)
	}
}

func TestServer_Send_WithSendFn(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-send-ok.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")
	srv.SetSendFn(func(_ context.Context, recipient, text, replyTo string) (string, int64, error) {
		return "fake-msg-id-123", 1700000000, nil
	})

	client := dialUnix(sockPath)
	body := strings.NewReader(`{"recipient":"1234567890@s.whatsapp.net","text":"hello"}`)
	resp, err := client.Post("http://localhost/send", "application/json", body)
	if err != nil {
		t.Fatalf("POST /send: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("status code: got %d want 200", resp.StatusCode)
	}
	var result map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if result["message_id"] != "fake-msg-id-123" {
		t.Errorf("message_id: got %v", result["message_id"])
	}
}

func TestServer_Backfill_ReplaysBufferedMessagesViaSSE(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-backfill.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return true, true })
	var sendCalls atomic.Int32
	srv.SetSendFn(func(context.Context, string, string, string) (string, int64, error) {
		sendCalls.Add(1)
		return "unexpected-send", 0, nil
	})
	now := time.Now()
	srv.RecordHistoryEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "fresh-message",
		ChatJID:   "chat@s.whatsapp.net",
		SenderJID: "sender@s.whatsapp.net",
		Timestamp: now.Add(-30 * time.Minute).Unix(),
		Content:   json.RawMessage(`{"text":"replay me"}`),
		Raw:       json.RawMessage(`{"is_from_me":true}`),
	})
	srv.RecordHistoryEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "stale-message",
		ChatJID:   "chat@s.whatsapp.net",
		SenderJID: "sender@s.whatsapp.net",
		Timestamp: now.Add(-2 * time.Hour).Unix(),
		Content:   json.RawMessage(`{"text":"do not replay"}`),
	})

	client := dialUnix(sockPath)
	for range 2 {
		requestBody := strings.NewReader(`{"schema_version":"whatsapp.backfill.v1","window_hours":1}`)
		resp, err := client.Post("http://localhost/backfill", "application/json", requestBody)
		if err != nil {
			t.Fatalf("POST /backfill: %v", err)
		}

		if resp.StatusCode != http.StatusOK {
			body, _ := io.ReadAll(resp.Body)
			resp.Body.Close()
			t.Fatalf("status code: got %d want %d; body: %s", resp.StatusCode, http.StatusOK, body)
		}
		var ack map[string]any
		if err := json.NewDecoder(resp.Body).Decode(&ack); err != nil {
			resp.Body.Close()
			t.Fatalf("decode acknowledgement: %v", err)
		}
		resp.Body.Close()
		if ack["schema_version"] != "whatsapp.backfill.v1" {
			t.Errorf("schema_version: got %v", ack["schema_version"])
		}
		if ack["status"] != "accepted" {
			t.Errorf("status: got %v", ack["status"])
		}
		if ack["replay_event_count"] != float64(1) {
			t.Errorf("replay_event_count: got %v want 1", ack["replay_event_count"])
		}
	}
	if sendCalls.Load() != 0 {
		t.Errorf("/backfill called live send %d times", sendCalls.Load())
	}

	streamCtx, streamCancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer streamCancel()
	streamReq, err := http.NewRequestWithContext(streamCtx, http.MethodGet, "http://localhost/events", nil)
	if err != nil {
		t.Fatalf("new SSE request: %v", err)
	}
	streamResp, err := client.Do(streamReq)
	if err != nil {
		t.Fatalf("GET /events: %v", err)
	}
	defer streamResp.Body.Close()

	// A live frame after subscription proves the deduplicated replay drains
	// completely before normal SSE traffic. Without pending-replay dedup, the
	// second frame would be a duplicate fresh-message instead.
	srv.PublishEvent(&bridgeEvents.BridgeEvent{
		Type:      "text",
		MessageID: "live-message",
		ChatJID:   "chat@s.whatsapp.net",
		SenderJID: "sender@s.whatsapp.net",
		Timestamp: now.Unix(),
		Content:   json.RawMessage(`{"text":"live after replay"}`),
	})

	scanner := bufio.NewScanner(streamResp.Body)
	var messageIDs []string
	for len(messageIDs) < 2 && scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "data: ") {
			continue
		}
		var replayed map[string]any
		if err := json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &replayed); err != nil {
			t.Fatalf("decode replayed SSE event: %v", err)
		}
		messageID, ok := replayed["message_id"].(string)
		if !ok {
			t.Fatalf("replayed message_id: got %v", replayed["message_id"])
		}
		messageIDs = append(messageIDs, messageID)
	}
	if err := scanner.Err(); err != nil {
		t.Fatalf("read replayed SSE event: %v", err)
	}
	if got, want := messageIDs, []string{"fresh-message", "live-message"}; !slices.Equal(got, want) {
		t.Errorf("SSE message order: got %v want %v", got, want)
	}
}

func TestServer_Backfill_RequiresConnectedV1Request(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-backfill-validation.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	body := `{"schema_version":"whatsapp.backfill.v1","window_hours":1}`
	resp, err := client.Post("http://localhost/backfill", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("POST /backfill while disconnected: %v", err)
	}
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("disconnected status code: got %d want 503", resp.StatusCode)
	}
	resp.Body.Close()

	srv.SetState(api.StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return true, true })
	resp, err = client.Post(
		"http://localhost/backfill",
		"application/json",
		strings.NewReader(`{"schema_version":"whatsapp.backfill.v0","window_hours":1}`),
	)
	if err != nil {
		t.Fatalf("POST /backfill with unsupported version: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("unsupported-version status code: got %d want 400", resp.StatusCode)
	}
}

func TestServer_PairPoll_NoPairingInProgress(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-none.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/pair/poll")
	if err != nil {
		t.Fatalf("GET /pair/poll: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status code: got %d want 400", resp.StatusCode)
	}
}

func TestServer_PairPoll_AfterSetQRCode(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-qr.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	expiry := time.Now().Add(20 * time.Second)
	srv.SetQRCode("2@someQRdata,key,ref", expiry)

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/pair/poll")
	if err != nil {
		t.Fatalf("GET /pair/poll: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("status code: got %d want 200", resp.StatusCode)
	}
	var result map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if result["status"] != "waiting" {
		t.Errorf("status: got %q want waiting", result["status"])
	}
}

func TestServer_PairPoll_AfterNotifyPaired(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-paired.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetQRCode("2@data", time.Now().Add(20*time.Second))
	srv.NotifyPaired("+15551234567")

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/pair/poll")
	if err != nil {
		t.Fatalf("GET /pair/poll: %v", err)
	}
	defer resp.Body.Close()

	var result map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if result["status"] != "paired" {
		t.Errorf("status: got %q want paired", result["status"])
	}
	if result["phone"] != "+15551234567" {
		t.Errorf("phone: got %q want +15551234567", result["phone"])
	}
}

func TestServer_PairStart_NoQRCode(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-start-none.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	resp, err := client.Post("http://localhost/pair/start", "application/json", nil)
	if err != nil {
		t.Fatalf("POST /pair/start: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("status code: got %d want 503", resp.StatusCode)
	}
}

func TestServer_PairStart_AlreadyConnected(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-start-conn.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")

	client := dialUnix(sockPath)
	resp, err := client.Post("http://localhost/pair/start", "application/json", nil)
	if err != nil {
		t.Fatalf("POST /pair/start: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusConflict {
		t.Errorf("status code: got %d want 409", resp.StatusCode)
	}
}

func TestServer_PairStart_WithQRCode(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-pair-start-qr.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	// Provide a minimal QR data string. The PNG generation should work with any string.
	srv.SetQRCode("test-qr-data-for-encoding", time.Now().Add(20*time.Second))

	client := dialUnix(sockPath)
	resp, err := client.Post("http://localhost/pair/start", "application/json", nil)
	if err != nil {
		t.Fatalf("POST /pair/start: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("status code: got %d want 200; body: %s", resp.StatusCode, body)
	}

	var result map[string]string
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if !strings.HasPrefix(result["qr_data_uri"], "data:image/png;base64,") {
		t.Errorf("qr_data_uri: expected data URI prefix, got %q", result["qr_data_uri"][:40])
	}
	if result["expires_at"] == "" {
		t.Error("expires_at should not be empty")
	}
}

func TestServer_SocketPermissions(t *testing.T) {
	sockPath := filepath.Join(os.TempDir(), "test-wa-bridge-perms.sock")
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	info, err := os.Stat(sockPath)
	if err != nil {
		t.Fatalf("stat socket: %v", err)
	}
	mode := info.Mode().Perm()
	if mode != 0600 {
		t.Errorf("socket permissions: got %04o want 0600", mode)
	}
}

func TestServer_SSE_KeepaliveEvent(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-sse-ka.sock"
	shutdownCalled := false
	srv := api.NewServer(sockPath, func() { shutdownCalled = true })
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
		_ = shutdownCalled
	}()

	// Publish a keepalive event and verify subscribers receive it.
	received := make(chan *bridgeEvents.BridgeEvent, 1)
	// We test PublishEvent indirectly by subscribing via SSE and reading.
	// For unit tests, we test that PublishEvent doesn't panic with zero subscribers.
	srv.PublishEvent(bridgeEvents.MapKeepalive())
	_ = received // no subscribers in this test — just verify no panic
}

// TestServer_Disconnect verifies the disconnect endpoint calls the shutdown fn.
func TestServer_Disconnect(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-disconnect.sock"
	shutdownCalled := make(chan struct{}, 1)
	srv := api.NewServer(sockPath, func() {
		shutdownCalled <- struct{}{}
	})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	resp, err := client.Post("http://localhost/disconnect", "application/json", nil)
	if err != nil {
		t.Fatalf("POST /disconnect: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("status code: got %d want 200", resp.StatusCode)
	}

	// Shutdown should be called asynchronously within ~500ms.
	select {
	case <-shutdownCalled:
		// success
	case <-time.After(2 * time.Second):
		t.Error("shutdown function was not called after /disconnect")
	}
}

// TestResponseContentType verifies JSON Content-Type on key endpoints.
func TestResponseContentType(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-ct.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	resp.Body.Close()

	ct := resp.Header.Get("Content-Type")
	if !strings.HasPrefix(ct, "application/json") {
		t.Errorf("Content-Type: got %q want application/json", ct)
	}
}

// TestServer_Status_LivenessProbe verifies /status reports the live link state
// from the injected probe rather than only the event-driven `state` field. This
// is the guard against the StreamReplaced false-green: the event-driven state can
// say "connected" while the actual whatsmeow link is down.
func TestServer_Status_LivenessProbe(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-liveness.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	// Event-driven state says connected, but the live probe reports a dead link
	// (as happens after a StreamReplaced that never produced a Disconnected event).
	srv.SetState(api.StateConnected, "+15551234567")
	srv.SetLivenessFn(func() (bool, bool) { return false, true })

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	defer resp.Body.Close()

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode body: %v", err)
	}

	if body["connected"] != false {
		t.Errorf("connected: got %v want false (live probe wins over state)", body["connected"])
	}
	if body["logged_in"] != true {
		t.Errorf("logged_in: got %v want true", body["logged_in"])
	}
	// The event-driven state field is unchanged — it is no longer the liveness source.
	if body["state"] != "connected" {
		t.Errorf("state: got %v want connected", body["state"])
	}
}

// TestServer_Status_LivenessFallback verifies that without an injected probe,
// /status falls back to deriving connected/logged_in from the event-driven state.
func TestServer_Status_LivenessFallback(t *testing.T) {
	sockPath := "/tmp/test-wa-bridge-liveness-fb.sock"
	srv := api.NewServer(sockPath, func() {})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := srv.Start(ctx); err != nil {
		t.Fatalf("Start: %v", err)
	}
	defer func() {
		shutCtx, c := context.WithTimeout(context.Background(), 2*time.Second)
		defer c()
		srv.Stop(shutCtx)
	}()

	srv.SetState(api.StateConnected, "+15551234567")

	client := dialUnix(sockPath)
	resp, err := client.Get("http://localhost/status")
	if err != nil {
		t.Fatalf("GET /status: %v", err)
	}
	defer resp.Body.Close()

	var body map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode body: %v", err)
	}
	if body["connected"] != true || body["logged_in"] != true {
		t.Errorf("fallback liveness: got connected=%v logged_in=%v want true/true",
			body["connected"], body["logged_in"])
	}
}

// Ensure httptest is used (compile check).
var _ = httptest.NewRecorder
