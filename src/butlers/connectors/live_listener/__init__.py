"""Live Listener connector for ambient voice ingestion.

Captures audio from local microphones, segments speech via VAD,
transcribes via faster-whisper, applies LLM-based discretion filtering,
and submits actionable utterances to the Switchboard as ingest.v1 envelopes.
"""
