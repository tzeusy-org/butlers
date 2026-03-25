package events_test

import (
	"encoding/json"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	waCommon "go.mau.fi/whatsmeow/proto/waCommon"
	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	waTypes "go.mau.fi/whatsmeow/types"
	waEvents "go.mau.fi/whatsmeow/types/events"

	"github.com/butlers/whatsapp-bridge/internal/events"
)

func makeInfo(id, chat, sender string) waTypes.MessageInfo {
	chatJID, _ := waTypes.ParseJID(chat)
	senderJID, _ := waTypes.ParseJID(sender)
	return waTypes.MessageInfo{
		MessageSource: waTypes.MessageSource{
			Chat:   chatJID,
			Sender: senderJID,
		},
		ID:        id,
		Timestamp: time.Unix(1700000000, 0),
	}
}

func TestMapMessage_TextConversation(t *testing.T) {
	evt := &waEvents.Message{
		Info:    makeInfo("msg1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{Conversation: proto.String("Hello, World!")},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "text" {
		t.Errorf("Type: got %q want %q", be.Type, "text")
	}
	if be.MessageID != "msg1" {
		t.Errorf("MessageID: got %q want %q", be.MessageID, "msg1")
	}

	var content map[string]any
	if err := json.Unmarshal(be.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content["text"] != "Hello, World!" {
		t.Errorf("content.text: got %v", content["text"])
	}
}

func TestMapMessage_ExtendedText(t *testing.T) {
	evt := &waEvents.Message{
		Info: makeInfo("msg2", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			ExtendedTextMessage: &waE2E.ExtendedTextMessage{
				Text: proto.String("Extended text"),
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "text" {
		t.Errorf("Type: got %q want %q", be.Type, "text")
	}
}

func TestMapMessage_Image(t *testing.T) {
	mimeType := "image/jpeg"
	caption := "Nice photo"
	fileLen := uint64(102400)
	evt := &waEvents.Message{
		Info: makeInfo("img1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			ImageMessage: &waE2E.ImageMessage{
				Mimetype:   &mimeType,
				Caption:    &caption,
				FileLength: &fileLen,
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "image" {
		t.Errorf("Type: got %q want %q", be.Type, "image")
	}

	var content map[string]any
	if err := json.Unmarshal(be.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content["media_available"] != true {
		t.Error("content.media_available should be true")
	}
	if content["mime_type"] != "image/jpeg" {
		t.Errorf("content.mime_type: got %v", content["mime_type"])
	}
	if content["caption"] != "Nice photo" {
		t.Errorf("content.caption: got %v", content["caption"])
	}
}

func TestMapMessage_VoiceNote(t *testing.T) {
	mimeType := "audio/ogg; codecs=opus"
	ptt := true
	evt := &waEvents.Message{
		Info: makeInfo("vn1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			AudioMessage: &waE2E.AudioMessage{
				Mimetype: &mimeType,
				PTT:      &ptt,
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "voice_note" {
		t.Errorf("Type: got %q want %q", be.Type, "voice_note")
	}
}

func TestMapMessage_Audio_NotPTT(t *testing.T) {
	mimeType := "audio/mp4"
	ptt := false
	evt := &waEvents.Message{
		Info: makeInfo("aud1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			AudioMessage: &waE2E.AudioMessage{
				Mimetype: &mimeType,
				PTT:      &ptt,
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "audio" {
		t.Errorf("Type: got %q want %q", be.Type, "audio")
	}
}

func TestMapMessage_Reaction(t *testing.T) {
	emoji := "👍"
	targetID := "target-msg-id"
	evt := &waEvents.Message{
		Info: makeInfo("rxn1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			ReactionMessage: &waE2E.ReactionMessage{
				Text: &emoji,
				Key: &waCommon.MessageKey{
					ID: &targetID,
				},
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "reaction" {
		t.Errorf("Type: got %q want %q", be.Type, "reaction")
	}

	var content map[string]any
	if err := json.Unmarshal(be.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content["emoji"] != "👍" {
		t.Errorf("content.emoji: got %v", content["emoji"])
	}
	if content["target_message_id"] != "target-msg-id" {
		t.Errorf("content.target_message_id: got %v", content["target_message_id"])
	}
}

func TestMapMessage_NilMessage(t *testing.T) {
	evt := &waEvents.Message{
		Info:    makeInfo("nil1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: nil,
	}
	be := events.MapMessage(evt)
	if be != nil {
		t.Errorf("expected nil for nil Message, got %+v", be)
	}
}

func TestMapMessage_EmptyMessage_ReturnsNil(t *testing.T) {
	evt := &waEvents.Message{
		Info:    makeInfo("empty1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{},
	}
	be := events.MapMessage(evt)
	if be != nil {
		t.Errorf("expected nil for empty Message, got type=%q", be.Type)
	}
}

func TestMapMessage_TimestampIsUnixEpoch(t *testing.T) {
	evt := &waEvents.Message{
		Info:    makeInfo("ts1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{Conversation: proto.String("hello")},
	}
	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Timestamp != 1700000000 {
		t.Errorf("Timestamp: got %d want 1700000000", be.Timestamp)
	}
}

func TestMapSessionInvalidated(t *testing.T) {
	be := events.MapSessionInvalidated("+15551234567")
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "session_invalidated" {
		t.Errorf("Type: got %q want %q", be.Type, "session_invalidated")
	}
	var content map[string]string
	if err := json.Unmarshal(be.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content["phone"] != "+15551234567" {
		t.Errorf("content.phone: got %q", content["phone"])
	}
}

func TestMapKeepalive(t *testing.T) {
	be := events.MapKeepalive()
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "keepalive" {
		t.Errorf("Type: got %q want %q", be.Type, "keepalive")
	}
	if be.Timestamp == 0 {
		t.Error("Timestamp should be set")
	}
}

func TestMapMessage_Document(t *testing.T) {
	mimeType := "application/pdf"
	filename := "report.pdf"
	evt := &waEvents.Message{
		Info: makeInfo("doc1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			DocumentMessage: &waE2E.DocumentMessage{
				Mimetype: &mimeType,
				FileName: &filename,
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "document" {
		t.Errorf("Type: got %q want %q", be.Type, "document")
	}
	var content map[string]any
	if err := json.Unmarshal(be.Content, &content); err != nil {
		t.Fatalf("unmarshal content: %v", err)
	}
	if content["filename"] != "report.pdf" {
		t.Errorf("content.filename: got %v", content["filename"])
	}
}

func TestMapMessage_MessageDeletion(t *testing.T) {
	deletedID := "deleted-msg-id"
	revokeType := waE2E.ProtocolMessage_REVOKE
	evt := &waEvents.Message{
		Info: makeInfo("del1", "1234567890@s.whatsapp.net", "9876543210@s.whatsapp.net"),
		Message: &waE2E.Message{
			ProtocolMessage: &waE2E.ProtocolMessage{
				Type: &revokeType,
				Key:  &waCommon.MessageKey{ID: &deletedID},
			},
		},
	}

	be := events.MapMessage(evt)
	if be == nil {
		t.Fatal("expected non-nil BridgeEvent")
	}
	if be.Type != "message_deleted" {
		t.Errorf("Type: got %q want %q", be.Type, "message_deleted")
	}
}
