package store_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"regexp"
	"testing"
	"time"

	"github.com/DATA-DOG/go-sqlmock"
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

func TestSaveNew_TargetsMessengerSchemaForSessionRotation(t *testing.T) {
	t.Parallel()

	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("create SQL mock: %v", err)
	}
	defer db.Close()

	pairedAt := time.Date(2026, time.July, 16, 1, 2, 3, 0, time.UTC)
	phone := "+15551234567"
	deviceID := "15551234567:1@s.whatsapp.net"
	sessionData := json.RawMessage(`{"jid":"15551234567:1@s.whatsapp.net"}`)

	mock.ExpectBegin()
	mock.ExpectExec(regexp.QuoteMeta(`
		UPDATE messenger.whatsapp_sessions
		   SET active = FALSE
		 WHERE phone_number = $1 AND active = TRUE
	`)).WithArgs(phone).WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectQuery(regexp.QuoteMeta(`
		INSERT INTO messenger.whatsapp_sessions (phone_number, device_id, session_data, paired_at, last_seen_at, active)
		VALUES ($1, $2, $3, NOW(), NOW(), TRUE)
		RETURNING id, phone_number, device_id, session_data, paired_at, last_seen_at, active
	`)).WithArgs(phone, deviceID, sessionData).WillReturnRows(
		sqlmock.NewRows([]string{
			"id", "phone_number", "device_id", "session_data", "paired_at", "last_seen_at", "active",
		}).AddRow("session-id", phone, deviceID, sessionData, pairedAt, pairedAt, true),
	)
	mock.ExpectCommit()

	sess, err := store.NewWithDB(db).SaveNew(context.Background(), phone, deviceID, sessionData)
	if err != nil {
		t.Fatalf("save session: %v", err)
	}
	if sess.ID != "session-id" {
		t.Fatalf("session ID = %q, want session-id", sess.ID)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatalf("SQL expectations: %v", err)
	}
}

func TestSaveNew_RollsBackWhenSessionInsertFails(t *testing.T) {
	t.Parallel()

	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("create SQL mock: %v", err)
	}
	defer db.Close()

	phone := "+15551234567"
	deviceID := "15551234567:1@s.whatsapp.net"
	sessionData := json.RawMessage(`{"jid":"15551234567:1@s.whatsapp.net"}`)

	mock.ExpectBegin()
	mock.ExpectExec(regexp.QuoteMeta(`
		UPDATE messenger.whatsapp_sessions
		   SET active = FALSE
		 WHERE phone_number = $1 AND active = TRUE
	`)).WithArgs(phone).WillReturnResult(sqlmock.NewResult(0, 1))
	mock.ExpectQuery(regexp.QuoteMeta(`
		INSERT INTO messenger.whatsapp_sessions (phone_number, device_id, session_data, paired_at, last_seen_at, active)
		VALUES ($1, $2, $3, NOW(), NOW(), TRUE)
		RETURNING id, phone_number, device_id, session_data, paired_at, last_seen_at, active
	`)).WithArgs(phone, deviceID, sessionData).WillReturnError(errors.New("insert failed"))
	mock.ExpectRollback()

	_, err = store.NewWithDB(db).SaveNew(context.Background(), phone, deviceID, sessionData)
	if err == nil || !regexp.MustCompile(`insert session: insert failed`).MatchString(err.Error()) {
		t.Fatalf("SaveNew error = %v, want wrapped insert failure", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Fatalf("SQL expectations: %v", err)
	}
}
