package main

import (
	waWeb "go.mau.fi/whatsmeow/proto/waWeb"
	waTypes "go.mau.fi/whatsmeow/types"
	waEvents "go.mau.fi/whatsmeow/types/events"

	bridgeEvents "github.com/butlers/whatsapp-bridge/internal/events"
)

// historyMessageParser is the small public whatsmeow surface needed to turn a
// stored WebMessageInfo into the same Message event used by live delivery.
// Keeping it as an interface lets the conversion contract be tested without a
// live WhatsApp session.
type historyMessageParser interface {
	ParseWebMessage(waTypes.JID, *waWeb.WebMessageInfo) (*waEvents.Message, error)
}

// mapHistorySyncMessages normalizes WhatsApp history into the bridge event
// schema. It deliberately skips malformed or unsupported records: those lack a
// stable message identity or a representation the connector already consumes.
func mapHistorySyncMessages(
	parser historyMessageParser,
	history *waEvents.HistorySync,
) []*bridgeEvents.BridgeEvent {
	if parser == nil || history == nil || history.Data == nil {
		return nil
	}

	mapped := make([]*bridgeEvents.BridgeEvent, 0)
	for _, conversation := range history.Data.GetConversations() {
		chatJID, err := waTypes.ParseJID(conversation.GetID())
		if err != nil {
			continue
		}
		for _, historyMsg := range conversation.GetMessages() {
			webMsg := historyMsg.GetMessage()
			if webMsg == nil {
				continue
			}
			message, err := parser.ParseWebMessage(chatJID, webMsg)
			if err != nil {
				continue
			}
			if event := bridgeEvents.MapMessage(message); event != nil {
				mapped = append(mapped, event)
			}
		}
	}
	return mapped
}
