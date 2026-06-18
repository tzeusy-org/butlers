package api

import (
	"testing"

	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
)

// TestKeepaliveDoesNotAdvanceLastEvent is a white-box test verifying that the
// keepalive fan-out path does NOT refresh lastEventAt, while the real-event path
// does. Keepalives fire on a fixed 30s timer independent of the WhatsApp link, so
// if they advanced lastEventAt a silently-dead link would always look fresh.
func TestKeepaliveDoesNotAdvanceLastEvent(t *testing.T) {
	s := NewServer("/tmp/test-wa-bridge-ka-internal.sock", func() {})

	if s.lastEventAt != nil {
		t.Fatalf("lastEventAt should start nil, got %v", s.lastEventAt)
	}

	// The real-event path (PublishEvent) advances lastEventAt. The event content
	// is irrelevant here — only which method is used matters.
	s.PublishEvent(bridgeEvents.MapKeepalive())
	if s.lastEventAt == nil {
		t.Fatal("lastEventAt should be set after a real-event publish")
	}
	baseline := *s.lastEventAt

	// A keepalive via the keepalive path must NOT advance lastEventAt.
	s.publishKeepalive(bridgeEvents.MapKeepalive())
	if !s.lastEventAt.Equal(baseline) {
		t.Errorf("keepalive advanced lastEventAt: baseline %v now %v", baseline, *s.lastEventAt)
	}
}
