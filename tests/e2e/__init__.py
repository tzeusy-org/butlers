"""End-to-end tests for the butlers staging harness.

These tests provision a complete butler ecosystem with real PostgreSQL,
real Claude Code spawners, and actual LLM calls to validate multi-butler
flows, routing, classification, and integration behavior.

E2E tests are excluded from CI and require:
- ANTHROPIC_API_KEY environment variable
- claude binary on PATH
- Docker daemon running (for testcontainers)

Cost: ~$0.05-$0.20 per full run (Claude Haiku 4.5).
"""
