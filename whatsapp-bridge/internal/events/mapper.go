// Package events maps whatsmeow events to bridge JSON event structures
// for emission on the SSE /events stream.
package events

import (
	"encoding/json"
	"time"

	waE2E "go.mau.fi/whatsmeow/proto/waE2E"
	waTypes "go.mau.fi/whatsmeow/types"
	waEvents "go.mau.fi/whatsmeow/types/events"
)

// BridgeEvent is the JSON structure emitted on the SSE stream.
type BridgeEvent struct {
	Type        string          `json:"type"`
	MessageID   string          `json:"message_id"`
	ChatJID     string          `json:"chat_jid"`
	SenderJID   string          `json:"sender_jid"`
	Timestamp   int64           `json:"timestamp"`
	Content     json.RawMessage `json:"content"`
	Raw         json.RawMessage `json:"raw,omitempty"`
}

// MapMessage maps a whatsmeow *events.Message to a BridgeEvent.
// Returns nil if the message should be ignored.
func MapMessage(evt *waEvents.Message) *BridgeEvent {
	msg := evt.Message
	if msg == nil {
		return nil
	}

	info := evt.Info
	be := &BridgeEvent{
		MessageID: info.ID,
		ChatJID:   info.Chat.String(),
		SenderJID: info.Sender.String(),
		Timestamp: info.Timestamp.Unix(),
	}

	// Determine type and content from the message.
	be.Type, be.Content = extractTypeAndContent(msg, info)
	if be.Type == "" {
		return nil // unknown/unsupported type
	}

	// Raw is a minimal summary (we don't include full protobuf bytes — just key info).
	rawSummary := map[string]any{
		"message_id": info.ID,
		"chat":       info.Chat.String(),
		"sender":     info.Sender.String(),
		"timestamp":  info.Timestamp.Unix(),
		"is_from_me": info.IsFromMe,
		"is_group":   info.IsGroup,
		"type":       be.Type,
	}
	be.Raw, _ = json.Marshal(rawSummary)

	return be
}

// MapSessionInvalidated creates a session_invalidated BridgeEvent.
func MapSessionInvalidated(phone string) *BridgeEvent {
	content, _ := json.Marshal(map[string]string{"phone": phone, "reason": "logged_out"})
	return &BridgeEvent{
		Type:      "session_invalidated",
		Timestamp: time.Now().Unix(),
		Content:   content,
	}
}

// MapKeepalive creates a keepalive BridgeEvent.
func MapKeepalive() *BridgeEvent {
	content, _ := json.Marshal(map[string]int64{"ts": time.Now().Unix()})
	return &BridgeEvent{
		Type:      "keepalive",
		Timestamp: time.Now().Unix(),
		Content:   content,
	}
}

// extractTypeAndContent returns the event type string and JSON content for a Message.
func extractTypeAndContent(msg *waE2E.Message, info waTypes.MessageInfo) (string, json.RawMessage) {
	// Text: plain conversation
	if conv := msg.GetConversation(); conv != "" {
		c, _ := json.Marshal(map[string]any{"text": conv, "quoted_message_id": quotedID(msg)})
		return "text", c
	}

	// Text: extended (with context / rich preview)
	if ext := msg.GetExtendedTextMessage(); ext != nil {
		text := ext.GetText()
		if text == "" {
			return "", nil
		}
		c, _ := json.Marshal(map[string]any{"text": text, "quoted_message_id": quotedID(msg)})
		return "text", c
	}

	// Image
	if img := msg.GetImageMessage(); img != nil {
		c, _ := json.Marshal(mediaContent("image", img.GetMimetype(), 0, img.GetCaption(), "", img.GetFileLength()))
		return "image", c
	}

	// Video
	if vid := msg.GetVideoMessage(); vid != nil {
		c, _ := json.Marshal(mediaContent("video", vid.GetMimetype(), vid.GetSeconds(), vid.GetCaption(), "", vid.GetFileLength()))
		return "video", c
	}

	// Audio / Voice note
	if aud := msg.GetAudioMessage(); aud != nil {
		msgType := "audio"
		if aud.GetPTT() {
			msgType = "voice_note"
		}
		c, _ := json.Marshal(mediaContent(msgType, aud.GetMimetype(), aud.GetSeconds(), "", "", aud.GetFileLength()))
		return msgType, c
	}

	// Document
	if doc := msg.GetDocumentMessage(); doc != nil {
		c, _ := json.Marshal(mediaContent("document", doc.GetMimetype(), 0, doc.GetCaption(), doc.GetFileName(), doc.GetFileLength()))
		return "document", c
	}

	// Sticker
	if stk := msg.GetStickerMessage(); stk != nil {
		c, _ := json.Marshal(mediaContent("sticker", stk.GetMimetype(), 0, "", "", stk.GetFileLength()))
		return "sticker", c
	}

	// Location
	if loc := msg.GetLocationMessage(); loc != nil {
		c, _ := json.Marshal(map[string]any{
			"latitude":  loc.GetDegreesLatitude(),
			"longitude": loc.GetDegreesLongitude(),
			"name":      loc.GetName(),
			"address":   loc.GetAddress(),
		})
		return "location", c
	}

	// Contact
	if ct := msg.GetContactMessage(); ct != nil {
		c, _ := json.Marshal(map[string]any{
			"display_name": ct.GetDisplayName(),
			"vcard":        ct.GetVcard(),
		})
		return "contact", c
	}

	// Reaction
	if rxn := msg.GetReactionMessage(); rxn != nil {
		c, _ := json.Marshal(map[string]any{
			"emoji":             rxn.GetText(),
			"target_message_id": rxn.GetKey().GetID(),
		})
		return "reaction", c
	}

	// Poll creation
	if poll := msg.GetPollCreationMessage(); poll != nil {
		options := make([]string, 0, len(poll.GetOptions()))
		for _, o := range poll.GetOptions() {
			options = append(options, o.GetOptionName())
		}
		c, _ := json.Marshal(map[string]any{
			"question":       poll.GetName(),
			"options":        options,
			"select_max":     poll.GetSelectableOptionsCount(),
		})
		return "poll", c
	}

	// Protocol message (e.g., message deletion/revoke)
	if proto := msg.GetProtocolMessage(); proto != nil {
		if proto.GetType() == waE2E.ProtocolMessage_REVOKE {
			c, _ := json.Marshal(map[string]any{
				"deleted_message_id": proto.GetKey().GetID(),
			})
			return "message_deleted", c
		}
		return "", nil
	}

	// Group invite
	if gi := msg.GetGroupInviteMessage(); gi != nil {
		c, _ := json.Marshal(map[string]any{
			"group_jid":  gi.GetGroupJID(),
			"group_name": gi.GetGroupName(),
			"caption":    gi.GetCaption(),
		})
		return "group_invite", c
	}

	// Unknown/unsupported — skip
	_ = info // suppress unused warning
	return "", nil
}

// mediaContent builds the content map for media messages.
func mediaContent(msgType, mimeType string, durationSec uint32, caption, filename string, fileSize uint64) map[string]any {
	m := map[string]any{
		"media_available": true,
		"mime_type":       mimeType,
		"file_size":       fileSize,
	}
	if caption != "" {
		m["caption"] = caption
	}
	if filename != "" {
		m["filename"] = filename
	}
	if durationSec > 0 {
		m["duration_s"] = durationSec
	}
	_ = msgType
	return m
}

// quotedID extracts the quoted message ID from context info if present.
func quotedID(msg *waE2E.Message) string {
	if msg == nil {
		return ""
	}
	if ext := msg.GetExtendedTextMessage(); ext != nil {
		if ctx := ext.GetContextInfo(); ctx != nil {
			if stanza := ctx.GetStanzaID(); stanza != "" {
				return stanza
			}
		}
	}
	return ""
}
