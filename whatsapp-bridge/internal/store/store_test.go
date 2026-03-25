package store_test

import (
	"database/sql"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/butlers/whatsapp-bridge/internal/store"
)

func TestErrNoSession_IsSentinel(t *testing.T) {
	// ErrNoSession is exported and must be a distinct sentinel error.
	if store.ErrNoSession == nil {
		t.Fatal("ErrNoSession must not be nil")
	}
	if store.ErrNoSession.Error() == "" {
		t.Fatal("ErrNoSession must have a non-empty message")
	}
	// errors.Is should work for direct comparison.
	if !errors.Is(store.ErrNoSession, store.ErrNoSession) {
		t.Fatal("errors.Is(ErrNoSession, ErrNoSession) must be true")
	}
}

func TestStoreNew_InvalidDSN(t *testing.T) {
	// New with an unreachable DSN should fail at Ping time.
	_, err := store.New("postgres://invalid-nonexistent-host-7z:5432/db?connect_timeout=1&sslmode=disable")
	if err == nil {
		t.Fatal("expected error for invalid DSN, got nil")
	}
}

func TestSession_JSONRoundTrip(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Second)
	sess := store.Session{
		ID:          "abc-123",
		PhoneNumber: "+15551234567",
		DeviceID:    "device-1",
		SessionData: json.RawMessage(`{"key":"value"}`),
		PairedAt:    now,
		LastSeenAt:  now,
		Active:      true,
	}

	b, err := json.Marshal(sess)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var got store.Session
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if got.ID != sess.ID {
		t.Errorf("ID: got %q want %q", got.ID, sess.ID)
	}
	if got.PhoneNumber != sess.PhoneNumber {
		t.Errorf("PhoneNumber: got %q want %q", got.PhoneNumber, sess.PhoneNumber)
	}
	if got.DeviceID != sess.DeviceID {
		t.Errorf("DeviceID: got %q want %q", got.DeviceID, sess.DeviceID)
	}
	if !got.Active {
		t.Error("Active should be true")
	}
	if string(got.SessionData) != `{"key":"value"}` {
		t.Errorf("SessionData: got %s", string(got.SessionData))
	}
}

func TestNewWithDB_Creation(t *testing.T) {
	// NewWithDB must not panic on creation even with a nil *sql.DB.
	s := store.NewWithDB((*sql.DB)(nil))
	if s == nil {
		t.Fatal("NewWithDB returned nil")
	}
}
