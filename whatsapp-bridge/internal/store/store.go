// Package store implements a PostgreSQL-backed session store for the WhatsApp bridge.
// It manages the whatsapp_sessions table for tracking paired device sessions.
package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	// PostgreSQL driver
	_ "github.com/lib/pq"
)

// ErrNoSession is returned when no active session exists for a phone number.
var ErrNoSession = errors.New("no active whatsapp session found")

// Session represents a stored WhatsApp session.
type Session struct {
	ID          string          `json:"id"`
	PhoneNumber string          `json:"phone_number"`
	DeviceID    string          `json:"device_id"`
	SessionData json.RawMessage `json:"session_data"`
	PairedAt    time.Time       `json:"paired_at"`
	LastSeenAt  time.Time       `json:"last_seen_at"`
	Active      bool            `json:"active"`
}

// Store manages whatsapp_sessions in PostgreSQL.
type Store struct {
	db *sql.DB
}

// New opens a PostgreSQL connection and returns a Store.
func New(dsn string) (*Store, error) {
	db, err := sql.Open("postgres", dsn)
	if err != nil {
		return nil, fmt.Errorf("open postgres: %w", err)
	}
	db.SetMaxOpenConns(5)
	db.SetMaxIdleConns(2)
	db.SetConnMaxLifetime(5 * time.Minute)
	if err := db.Ping(); err != nil {
		db.Close()
		return nil, fmt.Errorf("ping postgres: %w", err)
	}
	return &Store{db: db}, nil
}

// NewWithDB wraps an existing *sql.DB (for testing).
func NewWithDB(db *sql.DB) *Store {
	return &Store{db: db}
}

// Close closes the database connection.
func (s *Store) Close() error {
	return s.db.Close()
}

// EnsureTable creates the whatsapp_sessions table if it does not exist.
// This is a fallback; migrations are normally handled by Alembic.
func (s *Store) EnsureTable(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, `
		CREATE TABLE IF NOT EXISTS whatsapp_sessions (
			id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
			phone_number TEXT NOT NULL,
			device_id    TEXT NOT NULL DEFAULT '',
			session_data JSONB NOT NULL DEFAULT '{}',
			paired_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
			last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
			active       BOOLEAN NOT NULL DEFAULT TRUE
		);
		CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_sessions_active_phone
			ON whatsapp_sessions (phone_number) WHERE active = TRUE;
	`)
	return err
}

// GetActive returns the active session for the given phone number.
// Returns ErrNoSession if no active session exists.
func (s *Store) GetActive(ctx context.Context, phone string) (*Session, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, phone_number, device_id, session_data, paired_at, last_seen_at, active
		  FROM whatsapp_sessions
		 WHERE phone_number = $1 AND active = TRUE
		 LIMIT 1
	`, phone)
	return scanSession(row)
}

// GetAnyActive returns any active session (used when phone number is not yet known).
func (s *Store) GetAnyActive(ctx context.Context) (*Session, error) {
	row := s.db.QueryRowContext(ctx, `
		SELECT id, phone_number, device_id, session_data, paired_at, last_seen_at, active
		  FROM whatsapp_sessions
		 WHERE active = TRUE
		 ORDER BY last_seen_at DESC
		 LIMIT 1
	`)
	return scanSession(row)
}

// SaveNew inserts a new active session and deactivates any previous session for
// the same phone number (session rotation on re-pair).
func (s *Store) SaveNew(ctx context.Context, phone, deviceID string, sessionData json.RawMessage) (*Session, error) {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return nil, fmt.Errorf("begin tx: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	// Deactivate existing sessions for this phone number.
	_, err = tx.ExecContext(ctx, `
		UPDATE whatsapp_sessions
		   SET active = FALSE
		 WHERE phone_number = $1 AND active = TRUE
	`, phone)
	if err != nil {
		return nil, fmt.Errorf("deactivate old sessions: %w", err)
	}

	var sess Session
	err = tx.QueryRowContext(ctx, `
		INSERT INTO whatsapp_sessions (phone_number, device_id, session_data, paired_at, last_seen_at, active)
		VALUES ($1, $2, $3, NOW(), NOW(), TRUE)
		RETURNING id, phone_number, device_id, session_data, paired_at, last_seen_at, active
	`, phone, deviceID, sessionData).Scan(
		&sess.ID, &sess.PhoneNumber, &sess.DeviceID, &sess.SessionData,
		&sess.PairedAt, &sess.LastSeenAt, &sess.Active,
	)
	if err != nil {
		return nil, fmt.Errorf("insert session: %w", err)
	}
	if err := tx.Commit(); err != nil {
		return nil, fmt.Errorf("commit: %w", err)
	}
	return &sess, nil
}

// UpdateSessionData updates the session_data and last_seen_at for an active session.
func (s *Store) UpdateSessionData(ctx context.Context, id string, sessionData json.RawMessage) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE whatsapp_sessions
		   SET session_data = $1, last_seen_at = NOW()
		 WHERE id = $2 AND active = TRUE
	`, sessionData, id)
	return err
}

// MarkInactive marks the session with the given ID as inactive.
func (s *Store) MarkInactive(ctx context.Context, id string) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE whatsapp_sessions
		   SET active = FALSE, last_seen_at = NOW()
		 WHERE id = $1
	`, id)
	return err
}

// TouchLastSeen updates last_seen_at to NOW() for the given session ID.
func (s *Store) TouchLastSeen(ctx context.Context, id string) error {
	_, err := s.db.ExecContext(ctx, `
		UPDATE whatsapp_sessions
		   SET last_seen_at = NOW()
		 WHERE id = $1
	`, id)
	return err
}

// GetStatus returns a summary of session state for the status CLI command.
func (s *Store) GetStatus(ctx context.Context) ([]Session, error) {
	rows, err := s.db.QueryContext(ctx, `
		SELECT id, phone_number, device_id, session_data, paired_at, last_seen_at, active
		  FROM whatsapp_sessions
		 ORDER BY last_seen_at DESC
		 LIMIT 10
	`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var sessions []Session
	for rows.Next() {
		var sess Session
		if err := rows.Scan(
			&sess.ID, &sess.PhoneNumber, &sess.DeviceID, &sess.SessionData,
			&sess.PairedAt, &sess.LastSeenAt, &sess.Active,
		); err != nil {
			return nil, err
		}
		sessions = append(sessions, sess)
	}
	return sessions, rows.Err()
}

func scanSession(row *sql.Row) (*Session, error) {
	var sess Session
	err := row.Scan(
		&sess.ID, &sess.PhoneNumber, &sess.DeviceID, &sess.SessionData,
		&sess.PairedAt, &sess.LastSeenAt, &sess.Active,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrNoSession
	}
	if err != nil {
		return nil, fmt.Errorf("scan session: %w", err)
	}
	return &sess, nil
}
