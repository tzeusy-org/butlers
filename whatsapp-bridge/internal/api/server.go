// Package api implements the Unix socket HTTP server for the WhatsApp bridge.
// It exposes SSE /events, POST /send, POST /backfill, GET /status,
// POST /disconnect, POST /pair/start, and GET /pair/poll endpoints.
package api

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"sync"
	"time"

	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
	"github.com/skip2/go-qrcode"
)

// BridgeState represents the current connection state of the bridge.
type BridgeState string

const (
	StateConnected    BridgeState = "connected"
	StateConnecting   BridgeState = "connecting"
	StateDisconnected BridgeState = "disconnected"
	StatePairRequired BridgeState = "pair_required"
)

const (
	backfillSchemaVersion  = "whatsapp.backfill.v1"
	maxBackfillWindowHours = 24 * 7
	maxReplayEvents        = 1024
	sseSubscriberBuffer    = maxReplayEvents + 32
)

// PairStatus is the status for pair/poll responses.
type PairStatus string

const (
	PairStatusWaiting PairStatus = "waiting"
	PairStatusPaired  PairStatus = "paired"
	PairStatusExpired PairStatus = "expired"
)

// Server is the Unix socket HTTP server for the bridge.
type Server struct {
	socketPath string
	listener   net.Listener
	server     *http.Server

	mu          sync.RWMutex
	state       BridgeState
	phone       string
	startTime   time.Time
	lastEventAt *time.Time

	// SSE subscriber management
	subsMu      sync.Mutex
	subscribers map[chan *bridgeEvents.BridgeEvent]struct{}

	// Replay state is deliberately in-memory and bounded. The bridge only
	// replays normalised message events it has already received; it never asks
	// WhatsApp for history as a consequence of an HTTP request.
	replayMu         sync.Mutex
	replayEvents     []*bridgeEvents.BridgeEvent
	replayEventIDs   map[string]struct{}
	pendingReplay    []*bridgeEvents.BridgeEvent
	pendingReplayIDs map[string]struct{}
	backfillCutoff   *time.Time
	// liveHandoffArmed closes the accepted-/backfill-to-first-/events gap for
	// normal live messages. It is consumed by the first subscriber drain, so
	// later subscribers never receive a second replay of those live events.
	liveHandoffArmed bool
	// afterBackfillSnapshot is an internal-test barrier invoked while the
	// subscriber and replay transition locks are held, immediately after a
	// backfill request selects its snapshot and before it hands that snapshot
	// off. It is nil in production.
	afterBackfillSnapshot func()
	// beforeReplayRecordLock is an internal-test hook invoked immediately before
	// recordReplayEvent calls replayMu.Lock. It is nil in production.
	beforeReplayRecordLock func()

	// Pairing state
	pairMu     sync.Mutex
	pairStatus PairStatus
	pairPhone  string
	pairExpiry time.Time
	pairActive bool

	// Shutdown callback
	shutdownFn func()

	// sendFn is injected by the bridge to relay outbound messages.
	// Signature: (ctx, recipient JID string, text string, replyTo message ID) -> (msgID, unixTs, error)
	sendFn func(ctx context.Context, recipient, text, replyTo string) (string, int64, error)

	// livenessFn reports the live whatsmeow link state at request time
	// (connected = websocket up, loggedIn = session valid). It is injected by
	// the bridge and queried by /status so liveness reflects the actual client
	// rather than the last connection event we happened to handle. A missed or
	// unhandled event (e.g. StreamReplaced) can leave the event-driven `state`
	// field stale; this callback cannot.
	livenessFn func() (connected bool, loggedIn bool)

	// lastQRData holds the most recently received QR code string.
	lastQRData string
}

// NewServer creates a new API server but does not start it.
func NewServer(socketPath string, shutdownFn func()) *Server {
	s := &Server{
		socketPath:       socketPath,
		state:            StateConnecting,
		startTime:        time.Now(),
		subscribers:      make(map[chan *bridgeEvents.BridgeEvent]struct{}),
		replayEventIDs:   make(map[string]struct{}),
		pendingReplayIDs: make(map[string]struct{}),
		shutdownFn:       shutdownFn,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /events", s.handleEvents)
	mux.HandleFunc("POST /send", s.handleSend)
	mux.HandleFunc("POST /backfill", s.handleBackfill)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("POST /disconnect", s.handleDisconnect)
	mux.HandleFunc("POST /pair/start", s.handlePairStart)
	mux.HandleFunc("GET /pair/poll", s.handlePairPoll)
	s.server = &http.Server{
		Handler: mux,
		// ReadHeaderTimeout guards against slow-header attacks.
		// WriteTimeout is intentionally unset: the /events SSE stream is long-lived.
		ReadHeaderTimeout: 5 * time.Second,
	}
	return s
}

// Start binds to the Unix socket and begins serving requests.
func (s *Server) Start(ctx context.Context) error {
	// Remove stale socket file.
	if err := os.Remove(s.socketPath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("remove stale socket: %w", err)
	}

	ln, err := net.Listen("unix", s.socketPath)
	if err != nil {
		return fmt.Errorf("listen on unix socket: %w", err)
	}

	// Restrict to owner-only access (0600).
	if err := os.Chmod(s.socketPath, 0600); err != nil {
		ln.Close()
		return fmt.Errorf("chmod socket: %w", err)
	}

	s.listener = ln
	go func() {
		if err := s.server.Serve(ln); err != nil && err != http.ErrServerClosed {
			log.Printf("api server error: %v", err)
		}
	}()

	// Start keepalive ticker.
	go s.keepalivePump(ctx)

	return nil
}

// Stop gracefully shuts down the HTTP server and removes the socket file.
func (s *Server) Stop(ctx context.Context) error {
	err := s.server.Shutdown(ctx)
	os.Remove(s.socketPath)
	return err
}

// SetState updates the current bridge connection state.
func (s *Server) SetState(state BridgeState, phone string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.state = state
	s.phone = phone
}

// PublishEvent sends an event to all SSE subscribers.
func (s *Server) PublishEvent(evt *bridgeEvents.BridgeEvent) {
	recorded := s.recordReplayEvent(evt)

	s.mu.Lock()
	now := time.Now()
	s.lastEventAt = &now
	s.mu.Unlock()

	s.publishLiveEvent(evt, recorded)
}

// RecordHistoryEvent retains a normalised history-sync message for a later,
// explicit /backfill request. It intentionally does not emit the event live:
// consuming historical content requires the connector to opt in first.
func (s *Server) RecordHistoryEvent(evt *bridgeEvents.BridgeEvent) {
	if !s.recordReplayEvent(evt) || !s.matchesRequestedBackfill(evt) {
		return
	}
	s.enqueueReplay([]*bridgeEvents.BridgeEvent{evt})
}

// publishKeepalive fans a keepalive frame out to subscribers WITHOUT advancing
// last_event_at. Keepalives keep the SSE pipe warm on a fixed timer regardless
// of the WhatsApp link, so counting them as activity would mask a dead link —
// last_event_at must mean "last real WhatsApp event".
func (s *Server) publishKeepalive(evt *bridgeEvents.BridgeEvent) {
	s.fanout(evt)
}

// fanout delivers an event to all SSE subscribers, dropping for slow consumers.
func (s *Server) fanout(evt *bridgeEvents.BridgeEvent) {
	s.subsMu.Lock()
	defer s.subsMu.Unlock()
	s.fanoutLocked(evt)
}

// fanoutLocked delivers an event while subsMu is held.
func (s *Server) fanoutLocked(evt *bridgeEvents.BridgeEvent) {
	for ch := range s.subscribers {
		select {
		case ch <- evt:
		default:
			// Subscriber is slow; drop the event rather than blocking.
		}
	}
}

// publishLiveEvent atomically chooses between immediate fanout and the
// one-shot first-subscriber handoff. Holding subsMu across the choice prevents
// a subscriber from both draining a queued event and receiving it through
// ordinary fanout.
func (s *Server) publishLiveEvent(evt *bridgeEvents.BridgeEvent, recorded bool) {
	s.subsMu.Lock()
	defer s.subsMu.Unlock()

	if len(s.subscribers) != 0 {
		s.fanoutLocked(evt)
		return
	}
	if !recorded {
		return
	}

	s.replayMu.Lock()
	defer s.replayMu.Unlock()
	if s.liveHandoffArmed {
		s.appendPendingReplayLocked([]*bridgeEvents.BridgeEvent{evt})
	}
}

// NotifyPaired marks pairing as complete with the given phone number.
func (s *Server) NotifyPaired(phone string) {
	s.pairMu.Lock()
	defer s.pairMu.Unlock()
	if s.pairActive {
		s.pairStatus = PairStatusPaired
		s.pairPhone = phone
	}
}

// NotifyPairExpired marks pairing as expired.
func (s *Server) NotifyPairExpired() {
	s.pairMu.Lock()
	defer s.pairMu.Unlock()
	if s.pairActive {
		s.pairStatus = PairStatusExpired
	}
}

// SetQRCode is a callback invoked by the QR channel loop to update the active QR code.
// It resets the expiry based on the timeout provided by whatsmeow.
func (s *Server) SetQRCode(qrData string, expiry time.Time) {
	s.pairMu.Lock()
	defer s.pairMu.Unlock()
	s.pairActive = true
	s.pairStatus = PairStatusWaiting
	s.pairExpiry = expiry
	// Store the raw QR data string so /pair/start can re-encode on demand.
	s.lastQRData = qrData
}

// SetSendFn injects the outbound message send function.
func (s *Server) SetSendFn(fn func(ctx context.Context, recipient, text, replyTo string) (string, int64, error)) {
	s.sendFn = fn
}

// SetLivenessFn injects the live link-state probe queried by /status.
func (s *Server) SetLivenessFn(fn func() (connected bool, loggedIn bool)) {
	s.livenessFn = fn
}

// recordReplayEvent adds a valid, normalised message to the bounded replay
// cache. Stable chat/message identifiers are used only to avoid replay-buffer
// duplication; downstream Switchboard idempotency remains authoritative.
func (s *Server) recordReplayEvent(evt *bridgeEvents.BridgeEvent) bool {
	if evt == nil || evt.MessageID == "" || evt.ChatJID == "" || evt.Timestamp <= 0 {
		return false
	}

	key := replayEventKey(evt)
	if s.beforeReplayRecordLock != nil {
		s.beforeReplayRecordLock()
	}
	s.replayMu.Lock()
	defer s.replayMu.Unlock()
	if _, exists := s.replayEventIDs[key]; exists {
		return false
	}

	s.replayEvents = append(s.replayEvents, cloneBridgeEvent(evt))
	s.replayEventIDs[key] = struct{}{}
	if len(s.replayEvents) > maxReplayEvents {
		evicted := s.replayEvents[0]
		delete(s.replayEventIDs, evicted.ChatJID+"\x00"+evicted.MessageID)
		s.replayEvents = s.replayEvents[1:]
	}
	return true
}

func replayEventKey(evt *bridgeEvents.BridgeEvent) string {
	return evt.ChatJID + "\x00" + evt.MessageID
}

func (s *Server) matchesRequestedBackfill(evt *bridgeEvents.BridgeEvent) bool {
	s.replayMu.Lock()
	defer s.replayMu.Unlock()
	return s.backfillCutoff != nil && evt.Timestamp >= s.backfillCutoff.Unix()
}

func (s *Server) requestBackfill(windowHours int) []*bridgeEvents.BridgeEvent {
	cutoff := time.Now().Add(-time.Duration(windowHours) * time.Hour)

	// The request boundary must be linearized against both first-SSE
	// registration and live publication. In particular, do not expose a
	// snapshot before its matching pending replay and live handoff state exist:
	// a live event in that seam could otherwise be in neither path. Keep the
	// established lock order (subsMu -> replayMu) so handleEvents and live
	// publication make the same all-or-nothing choice.
	s.subsMu.Lock()
	defer s.subsMu.Unlock()
	s.replayMu.Lock()
	defer s.replayMu.Unlock()
	s.backfillCutoff = &cutoff

	replay := make([]*bridgeEvents.BridgeEvent, 0, len(s.replayEvents))
	for _, evt := range s.replayEvents {
		if evt.Timestamp >= cutoff.Unix() {
			replay = append(replay, cloneBridgeEvent(evt))
		}
	}
	if s.afterBackfillSnapshot != nil {
		s.afterBackfillSnapshot()
	}

	if len(s.subscribers) == 0 {
		s.appendPendingReplayLocked(replay)
		s.liveHandoffArmed = true
		return replay
	}

	// A current subscriber receives the snapshot directly while subsMu is held,
	// so any racing PublishEvent is ordered after this replay instead of being
	// queued for a later subscriber.
	for ch := range s.subscribers {
		for _, evt := range replay {
			select {
			case ch <- evt:
			default:
				// Preserve the existing slow-consumer behavior for replay frames.
			}
		}
	}
	s.liveHandoffArmed = false
	return replay
}

// enqueueReplay sends replay events to current SSE consumers. If the connector
// has requested replay before it subscribes, the events are held until that
// first subscription is established.
func (s *Server) enqueueReplay(events []*bridgeEvents.BridgeEvent) {
	if len(events) == 0 {
		return
	}

	s.subsMu.Lock()
	defer s.subsMu.Unlock()
	if len(s.subscribers) == 0 {
		s.replayMu.Lock()
		s.appendPendingReplayLocked(events)
		s.replayMu.Unlock()
		return
	}

	for ch := range s.subscribers {
		for _, evt := range events {
			select {
			case ch <- evt:
			default:
				// Preserve the existing slow-consumer behavior for replay frames.
			}
		}
	}
}

// appendPendingReplayLocked adds valid replay or live-handoff events while
// replayMu is held. The shared cap and identity map preserve the existing
// first-subscriber queue bounds and deduplication semantics.
func (s *Server) appendPendingReplayLocked(events []*bridgeEvents.BridgeEvent) {
	for _, evt := range events {
		if evt == nil || evt.MessageID == "" || evt.ChatJID == "" {
			continue
		}
		key := replayEventKey(evt)
		if _, exists := s.pendingReplayIDs[key]; exists {
			continue
		}
		if len(s.pendingReplay) >= maxReplayEvents {
			break
		}
		s.pendingReplay = append(s.pendingReplay, cloneBridgeEvent(evt))
		s.pendingReplayIDs[key] = struct{}{}
	}
}

func cloneBridgeEvent(evt *bridgeEvents.BridgeEvent) *bridgeEvents.BridgeEvent {
	if evt == nil {
		return nil
	}
	clone := *evt
	clone.Content = append([]byte(nil), evt.Content...)
	clone.Raw = append([]byte(nil), evt.Raw...)
	return &clone
}

// ------------------------------------------------------------------
// Handlers
// ------------------------------------------------------------------

// handleEvents streams SSE events to the caller.
func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// A complete bounded replay must fit alongside the ordinary live-event
	// allowance; otherwise a request from an already-subscribed connector could
	// silently lose frames to the normal slow-consumer drop policy.
	ch := make(chan *bridgeEvents.BridgeEvent, sseSubscriberBuffer)
	s.subsMu.Lock()
	s.subscribers[ch] = struct{}{}
	s.replayMu.Lock()
	pendingReplay := s.pendingReplay
	s.pendingReplay = nil
	s.pendingReplayIDs = make(map[string]struct{})
	s.liveHandoffArmed = false
	s.replayMu.Unlock()
	s.subsMu.Unlock()

	defer func() {
		s.subsMu.Lock()
		delete(s.subscribers, ch)
		s.subsMu.Unlock()
	}()

	for _, evt := range pendingReplay {
		writeSSEEvent(w, evt)
		flusher.Flush()
	}

	for {
		select {
		case <-r.Context().Done():
			return
		case evt, ok := <-ch:
			if !ok {
				return
			}
			writeSSEEvent(w, evt)
			flusher.Flush()
		}
	}
}

// handleBackfill schedules already-received normalised history for replay over
// /events. The owner-only Unix socket is the authentication boundary; the
// connected/logged-in checks additionally prevent replay from a stale session.
func (s *Server) handleBackfill(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	state := s.state
	s.mu.RUnlock()
	connected := state == StateConnected
	loggedIn := state == StateConnected
	if s.livenessFn != nil {
		connected, loggedIn = s.livenessFn()
	}
	if !connected || !loggedIn {
		writeBackfillError(w, http.StatusServiceUnavailable, "not connected")
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 4*1024)
	var req struct {
		SchemaVersion string `json:"schema_version"`
		WindowHours   int    `json:"window_hours"`
	}
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		writeBackfillError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if req.SchemaVersion != backfillSchemaVersion {
		writeBackfillError(w, http.StatusBadRequest, "unsupported schema_version")
		return
	}
	if req.WindowHours < 1 || req.WindowHours > maxBackfillWindowHours {
		writeBackfillError(w, http.StatusBadRequest, "window_hours must be between 1 and 168")
		return
	}

	replay := s.requestBackfill(req.WindowHours)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"schema_version":     backfillSchemaVersion,
		"status":             "accepted",
		"window_hours":       req.WindowHours,
		"replay_event_count": len(replay),
	})
}

func writeBackfillError(w http.ResponseWriter, status int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"error": message})
}

// handleSend relays an outbound message via the whatsmeow send function injected
// at construction time via SetSendFn.
func (s *Server) handleSend(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	state := s.state
	s.mu.RUnlock()

	if state != StateConnected {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "not connected"})
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, 64*1024)
	var req struct {
		Recipient string `json:"recipient"`
		Text      string `json:"text"`
		ReplyTo   string `json:"reply_to"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON body", http.StatusBadRequest)
		return
	}
	if req.Recipient == "" || req.Text == "" {
		http.Error(w, "recipient and text are required", http.StatusBadRequest)
		return
	}

	if s.sendFn == nil {
		http.Error(w, "send not available", http.StatusServiceUnavailable)
		return
	}

	msgID, ts, err := s.sendFn(r.Context(), req.Recipient, req.Text, req.ReplyTo)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"message_id": msgID,
		"timestamp":  ts,
	})
}

// handleStatus returns the current bridge status as JSON.
func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	state := s.state
	phone := s.phone
	uptime := time.Since(s.startTime).Seconds()
	lastEvt := s.lastEventAt
	s.mu.RUnlock()

	var lastEvtStr *string
	if lastEvt != nil {
		ts := lastEvt.Format(time.RFC3339)
		lastEvtStr = &ts
	}

	phonePtr := (*string)(nil)
	if phone != "" {
		phonePtr = &phone
	}

	// Probe the live whatsmeow link state. This is the authoritative liveness
	// signal: the event-driven `state` field can go stale if a connection event
	// is never delivered (e.g. StreamReplaced), but IsConnected/IsLoggedIn query
	// the client directly. When no probe is injected, fall back to the
	// event-driven state so older callers keep working.
	connected := state == StateConnected
	loggedIn := state == StateConnected
	if s.livenessFn != nil {
		connected, loggedIn = s.livenessFn()
	}

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"state":         string(state),
		"phone":         phonePtr,
		"uptime_s":      int(uptime),
		"last_event_at": lastEvtStr,
		"connected":     connected,
		"logged_in":     loggedIn,
	})
}

// handleDisconnect gracefully disconnects the bridge and exits.
func (s *Server) handleDisconnect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "disconnecting"})
	go func() {
		time.Sleep(100 * time.Millisecond)
		if s.shutdownFn != nil {
			s.shutdownFn()
		}
	}()
}

// handlePairStart generates a QR code and returns it as a base64 PNG data URI.
func (s *Server) handlePairStart(w http.ResponseWriter, r *http.Request) {
	s.mu.RLock()
	state := s.state
	s.mu.RUnlock()

	if state == StateConnected {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "active session already exists"})
		return
	}

	s.pairMu.Lock()
	qrRaw := s.lastQRData
	expiry := s.pairExpiry
	s.pairMu.Unlock()

	if qrRaw == "" {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "no QR code available yet; pairing not started"})
		return
	}

	png, err := qrcode.Encode(qrRaw, qrcode.Medium, 256)
	if err != nil {
		http.Error(w, "QR encode error: "+err.Error(), http.StatusInternalServerError)
		return
	}

	dataURI := "data:image/png;base64," + base64.StdEncoding.EncodeToString(png)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"qr_data_uri": dataURI,
		"expires_at":  expiry.Format(time.RFC3339),
	})
}

// handlePairPoll returns the current pairing status.
func (s *Server) handlePairPoll(w http.ResponseWriter, r *http.Request) {
	s.pairMu.Lock()
	active := s.pairActive
	status := s.pairStatus
	phone := s.pairPhone
	s.pairMu.Unlock()

	w.Header().Set("Content-Type", "application/json")

	if !active {
		w.WriteHeader(http.StatusBadRequest)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": "no pairing in progress"})
		return
	}

	resp := map[string]any{"status": string(status)}
	if status == PairStatusPaired && phone != "" {
		resp["phone"] = phone
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// ------------------------------------------------------------------
// SSE helpers
// ------------------------------------------------------------------

func writeSSEEvent(w io.Writer, evt *bridgeEvents.BridgeEvent) {
	data, err := json.Marshal(evt)
	if err != nil {
		return
	}
	fmt.Fprintf(w, "event: %s\ndata: %s\n\n", evt.Type, data)
}

// keepalivePump sends a keepalive SSE event every 30 seconds.
func (s *Server) keepalivePump(ctx context.Context) {
	ticker := time.NewTicker(30 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			s.publishKeepalive(bridgeEvents.MapKeepalive())
		}
	}
}
