---
name: interactive-response
description: General-butler response-mode selection and examples for responding to interactive messaging channels.
version: 1.0.0
tools_required:
  - notify
  - collection_create
  - item_create
  - item_search
---

# General Interactive Response Skill

## Purpose

Load this skill when a message originates from an interactive messaging channel
and you need to select a response mode for a General-butler action.

### Response Mode Selection

Choose the appropriate response mode based on the message type and action taken:

1. **React**: Quick acknowledgment without text (emoji only)
   - Use when: The action is simple and self-explanatory
   - Example: User says "Add milk to shopping list" → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: The action needs a short confirmation
   - Example: "Added to your reading list" or "Note saved"

3. **Follow-up**: Proactive question or suggestion
   - Use when: You need more information or can offer organization help
   - Example: "Saved to your ideas collection. Should I create a dedicated project collection?"

4. **Answer**: Substantive information in response to a question
   - Use when: User asked a direct question
   - Example: User asks "What's on my shopping list?" → List the items

5. **React + Reply**: Combined emoji acknowledgment with message
   - Use when: You want immediate visual feedback plus substantive response
   - Example: React with ✅ then reply "Added 'Learn Rust' to your goals collection"
