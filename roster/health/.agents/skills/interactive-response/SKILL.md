---
name: interactive-response
description: Health-butler response-mode selection and examples for interactive health messages.
version: 1.0.0
tools_required:
  - notify
  - measurement_log
  - medication_log_dose
  - measurement_history
---

# Health Interactive Response Skill

## Purpose

Load this skill when an interactive health message needs a deliberate response
mode and domain-appropriate example.

### Response Mode Selection

Choose the appropriate response mode based on the message type and action taken:

1. **React**: Quick acknowledgment without text (emoji only)
   - Use when: The action is simple and self-explanatory
   - Example: User says "Logged my morning meds" → React with ✅

2. **Affirm**: Brief confirmation message
   - Use when: The action needs a short confirmation
   - Example: "Weight logged: 165 lbs" or "Medication dose recorded"

3. **Follow-up**: Proactive question or suggestion
   - Use when: You notice a pattern or can offer insights
   - Example: "Your weight has increased 3 lbs this week. Everything okay?"

4. **Answer**: Substantive information in response to a question
   - Use when: User asked a direct question
   - Example: User asks "What was my blood pressure yesterday?" → Answer with the measurement

5. **React + Reply**: Combined emoji acknowledgment with message
   - Use when: You want immediate visual feedback plus substantive response
   - Example: React with ✅ then reply "Blood pressure logged. Your average this week is 118/76."
