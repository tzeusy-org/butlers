package main

import (
	"encoding/json"
	"flag"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	waCommon "go.mau.fi/whatsmeow/proto/waCommon"
	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	waHistorySync "go.mau.fi/whatsmeow/proto/waHistorySync"
	waWeb "go.mau.fi/whatsmeow/proto/waWeb"
	waTypes "go.mau.fi/whatsmeow/types"
	waEvents "go.mau.fi/whatsmeow/types/events"
)

func TestBridgeFlagsDefaultToSelfContainedStandaloneSocket(t *testing.T) {
	t.Setenv("WA_LISTEN", "")

	fs, _, listenAddr := newBridgeFlagSet(flag.ContinueOnError)
	if err := fs.Parse(nil); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	if got, want := *listenAddr, "unix:///tmp/wa-bridge.sock"; got != want {
		t.Fatalf("default listen address = %q, want %q", got, want)
	}
}

func TestBridgeListenFlagOverridesEnvironment(t *testing.T) {
	t.Setenv("WA_LISTEN", "unix:///tmp/from-env.sock")

	fs, _, listenAddr := newBridgeFlagSet(flag.ContinueOnError)
	if err := fs.Parse([]string{"--listen", "unix:///tmp/from-flag.sock"}); err != nil {
		t.Fatalf("parse flags: %v", err)
	}

	if got, want := *listenAddr, "unix:///tmp/from-flag.sock"; got != want {
		t.Fatalf("listen address = %q, want %q", got, want)
	}
}

type fakeHistoryMessageParser struct {
	calls []waTypes.JID
}

func (f *fakeHistoryMessageParser) ParseWebMessage(
	chatJID waTypes.JID,
	webMsg *waWeb.WebMessageInfo,
) (*waEvents.Message, error) {
	f.calls = append(f.calls, chatJID)
	sender, _ := waTypes.ParseJID("15557654321@s.whatsapp.net")
	return &waEvents.Message{
		Info: waTypes.MessageInfo{
			MessageSource: waTypes.MessageSource{
				Chat:     chatJID,
				Sender:   sender,
				IsFromMe: true,
			},
			ID:        webMsg.GetKey().GetID(),
			Timestamp: time.Unix(int64(webMsg.GetMessageTimestamp()), 0),
		},
		Message: webMsg.GetMessage(),
	}, nil
}

func TestMapHistorySyncMessages_UsesLiveBridgeSchema(t *testing.T) {
	parser := &fakeHistoryMessageParser{}
	chat := "15551234567@s.whatsapp.net"
	messageID := "history-message-1"
	timestamp := uint64(1700000000)
	history := &waEvents.HistorySync{Data: &waHistorySync.HistorySync{
		Conversations: []*waHistorySync.Conversation{{
			ID: proto.String(chat),
			Messages: []*waHistorySync.HistorySyncMsg{{
				Message: &waWeb.WebMessageInfo{
					Key: &waCommon.MessageKey{ID: proto.String(messageID)},
					Message: &waE2E.Message{
						Conversation: proto.String("cached history"),
					},
					MessageTimestamp: &timestamp,
				},
			}},
		}},
	}}

	mapped := mapHistorySyncMessages(parser, history)
	if len(mapped) != 1 {
		t.Fatalf("mapped history events: got %d want 1", len(mapped))
	}
	event := mapped[0]
	if event.MessageID != messageID || event.ChatJID != chat || event.Type != "text" {
		t.Fatalf("unexpected bridge event: %+v", event)
	}
	if event.Timestamp != int64(timestamp) {
		t.Errorf("timestamp: got %d want %d", event.Timestamp, timestamp)
	}
	var raw map[string]any
	if err := json.Unmarshal(event.Raw, &raw); err != nil {
		t.Fatalf("decode raw summary: %v", err)
	}
	if raw["is_from_me"] != true {
		t.Errorf("history event lost owner metadata: %v", raw)
	}
	if len(parser.calls) != 1 || parser.calls[0].String() != chat {
		t.Errorf("history parser calls: got %v want %s", parser.calls, chat)
	}
}
