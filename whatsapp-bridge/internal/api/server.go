// Package api implements the Unix socket HTTP server for the WhatsApp bridge.
// It exposes SSE /events, POST /send, GET /status, POST /disconnect,
// POST /pair/start, and GET /pair/poll endpoints.
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

	"github.com/skip2/go-qrcode"
	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
)

// BridgeState represents the current connection state of the bridge.
type BridgeState string

const (
	StateConnected    BridgeState = "connected"
	StateConnecting   BridgeState = "connecting"
	StateDisconnected BridgeState = "disconnected"
	StatePairRequired BridgeState = "pair_required"
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

	// lastQRData holds the most recently received QR code string.
	lastQRData string
}

// NewServer creates a new API server but does not start it.
func NewServer(socketPath string, shutdownFn func()) *Server {
	s := &Server{
		socketPath:  socketPath,
		state:       StateConnecting,
		startTime:   time.Now(),
		subscribers: make(map[chan *bridgeEvents.BridgeEvent]struct{}),
		shutdownFn:  shutdownFn,
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /events", s.handleEvents)
	mux.HandleFunc("POST /send", s.handleSend)
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
	s.mu.Lock()
	now := time.Now()
	s.lastEventAt = &now
	s.mu.Unlock()

	s.subsMu.Lock()
	defer s.subsMu.Unlock()
	for ch := range s.subscribers {
		select {
		case ch <- evt:
		default:
			// Subscriber is slow; drop the event rather than blocking.
		}
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

	ch := make(chan *bridgeEvents.BridgeEvent, 32)
	s.subsMu.Lock()
	s.subscribers[ch] = struct{}{}
	s.subsMu.Unlock()

	defer func() {
		s.subsMu.Lock()
		delete(s.subscribers, ch)
		s.subsMu.Unlock()
	}()

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

	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"state":         string(state),
		"phone":         phonePtr,
		"uptime_s":      int(uptime),
		"last_event_at": lastEvtStr,
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
			s.PublishEvent(bridgeEvents.MapKeepalive())
		}
	}
}
