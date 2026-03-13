"""Main entrypoint for the live-listener connector.

Orchestrates the full pipeline for one or more microphone devices:
  audio capture → VAD → transcription → filter_gate → discretion → envelope → ingest

Each microphone runs as an independent asyncio pipeline within a single process.
All pipelines share the MCP client, metrics, health server, and heartbeat task.

Environment Variables
---------------------
Required:
  SWITCHBOARD_MCP_URL          SSE endpoint URL for Switchboard MCP server
  LIVE_LISTENER_DEVICES        JSON list of mic device specs
  LIVE_LISTENER_TRANSCRIPTION_URL  Transcription service URL

Optional:
  CONNECTOR_HEALTH_PORT        HTTP port for /health and /metrics (default: 40091)
  CONNECTOR_HEARTBEAT_INTERVAL_S  Heartbeat interval in seconds (default: 120)
  CONNECTOR_HEARTBEAT_ENABLED  Enable/disable heartbeat (default: true)
  LIVE_LISTENER_VAD_ONSET_THRESHOLD
  LIVE_LISTENER_VAD_OFFSET_THRESHOLD
  LIVE_LISTENER_VAD_ONSET_FRAMES
  LIVE_LISTENER_VAD_OFFSET_FRAMES
  LIVE_LISTENER_MIN_SEGMENT_MS
  LIVE_LISTENER_MAX_SEGMENT_MS
  LIVE_LISTENER_TRANSCRIPTION_PROTOCOL  wyoming | websocket | http (default: wyoming)
  LIVE_LISTENER_LANGUAGE               BCP-47 language hint (default: en)
  LIVE_LISTENER_MIN_CONFIDENCE         Min confidence threshold (default: 0.3)
  LIVE_LISTENER_DISCRETION_LLM_URL
  LIVE_LISTENER_DISCRETION_LLM_MODEL
  LIVE_LISTENER_DISCRETION_TIMEOUT_S
  LIVE_LISTENER_DISCRETION_WINDOW_SIZE
  LIVE_LISTENER_DISCRETION_WINDOW_SECONDS
  LIVE_LISTENER_SESSION_GAP_S

Spec references:
  openspec/changes/connector-live-listener/specs/connector-live-listener/spec.md
  § Connector Identity and Role, § Health State Derivation, § Prometheus Metrics,
  § Environment Variables
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from threading import Thread
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

import uvicorn
from fastapi import FastAPI
from prometheus_client import REGISTRY, generate_latest

from butlers.connectors.heartbeat import ConnectorHeartbeat, HeartbeatConfig
from butlers.connectors.live_listener.checkpoint import (
    load_voice_checkpoint,
    save_voice_checkpoint,
)
from butlers.connectors.live_listener.config import LiveListenerConfig, MicDeviceSpec
from butlers.connectors.live_listener.discretion import (
    DiscretionConfig,
    DiscretionEvaluator,
    DiscretionResult,
)
from butlers.connectors.live_listener.envelope import (
    build_voice_envelope,
    unix_ms_from_datetime,
)
from butlers.connectors.live_listener.filter_gate import (
    create_filter_evaluator,
    evaluate_voice_filter,
    warn_non_mic_id_rules,
)
from butlers.connectors.live_listener.metrics import LiveListenerMetrics
from butlers.connectors.live_listener.prefilter import PreFilter, PreFilterConfig
from butlers.connectors.live_listener.session import ConversationSession
from butlers.connectors.live_listener.transcription import (
    TranscriptionClient,
    create_transcription_client,
)
from butlers.connectors.live_listener.vad import (
    SileroVad,
    SpeechSegment,
    VadConfig,
    VadStateMachine,
)
from butlers.connectors.mcp_client import CachedMCPClient
from butlers.connectors.metrics import ConnectorMetrics
from butlers.core.logging import configure_logging

logger = logging.getLogger(__name__)

# Default health server port for the live-listener connector
DEFAULT_HEALTH_PORT = 40091

# Connector identity constants
CONNECTOR_TYPE = "live_listener"


# ---------------------------------------------------------------------------
# Per-mic pipeline state
# ---------------------------------------------------------------------------


class MicPipelineState:
    """Runtime state for a single microphone pipeline."""

    def __init__(self, mic_name: str) -> None:
        self.mic_name = mic_name
        self.connected: bool = False
        self.last_error: str | None = None
        self.transcription_healthy: bool = True
        self.discretion_healthy: bool = True


# ---------------------------------------------------------------------------
# Main connector class
# ---------------------------------------------------------------------------


class LiveListenerConnector:
    """Orchestrates one live-listener pipeline per configured microphone.

    Each microphone has an independent VAD, transcription client, discretion
    evaluator, session tracker, and metrics instance. All pipelines share the
    MCP client, ConnectorMetrics, health server, and heartbeat task.

    Args:
        config: Full connector configuration parsed from env vars.
        db_pool: asyncpg pool for filter gate and checkpoint persistence.
                 May be ``None`` — filter gates run fail-open (pass all).
        mcp_client: Pre-constructed MCP client (for testing / injection).
    """

    def __init__(
        self,
        config: LiveListenerConfig,
        db_pool: asyncpg.Pool | None = None,
        mcp_client: CachedMCPClient | None = None,
    ) -> None:
        self._config = config
        self._db_pool = db_pool

        # Shared MCP client
        if mcp_client is not None:
            self._mcp_client = mcp_client
        else:
            self._mcp_client = CachedMCPClient(
                config.switchboard_mcp_url,
                client_name="live-listener",
            )

        # Shared connector-level metrics (standard counters)
        # The endpoint_identity here uses the connector-level identity
        self._connector_metrics = ConnectorMetrics(
            connector_type=CONNECTOR_TYPE,
            endpoint_identity="live-listener:connector",
        )

        # Per-mic state
        self._mic_states: dict[str, MicPipelineState] = {}
        for spec in config.devices:
            self._mic_states[spec.name] = MicPipelineState(spec.name)

        # Per-mic pipeline components (built in start())
        self._transcription_clients: dict[str, TranscriptionClient] = {}
        self._discretion_evaluators: dict[str, DiscretionEvaluator] = {}
        self._prefilters: dict[str, PreFilter] = {}
        self._sessions: dict[str, ConversationSession] = {}
        self._ll_metrics: dict[str, LiveListenerMetrics] = {}

        # Pipeline tasks (one per mic)
        self._pipeline_tasks: dict[str, asyncio.Task] = {}

        # Background segment-processing tasks (fire-and-forget; held to prevent GC)
        self._background_tasks: set[asyncio.Task] = set()

        # Health server
        self._health_server: uvicorn.Server | None = None
        self._health_thread: Thread | None = None
        self._health_port = int(os.environ.get("CONNECTOR_HEALTH_PORT", str(DEFAULT_HEALTH_PORT)))

        # Heartbeat
        self._heartbeat: ConnectorHeartbeat | None = None

        # Process start time
        self._start_time = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start all microphone pipelines, health server, and heartbeat."""
        logger.info(
            "live-listener: starting connector with %d mic(s): %s",
            len(self._config.devices),
            [s.name for s in self._config.devices],
        )

        # Build per-mic components
        discretion_config = DiscretionConfig()
        for spec in self._config.devices:
            mic = spec.name

            # Transcription client
            client = create_transcription_client(
                protocol=self._config.transcription_protocol,
                url=self._config.transcription_url,
                mic_name=mic,
                language=self._config.language,
                min_confidence=self._config.min_confidence,
            )
            await client.connect()
            self._transcription_clients[mic] = client

            # Discretion evaluator
            self._discretion_evaluators[mic] = DiscretionEvaluator(
                mic_name=mic,
                config=discretion_config,
            )

            # Pre-filter (heuristic gate before discretion LLM)
            self._prefilters[mic] = PreFilter(
                mic_name=mic,
                config=PreFilterConfig.from_env(),
            )

            # Session tracker
            session = ConversationSession(
                device_name=mic,
                session_gap_s=self._config.session_gap_s,
            )
            self._sessions[mic] = session

            # Restore session state from checkpoint (fail-open: no-op if unavailable)
            if self._db_pool is not None:
                ckpt = await load_voice_checkpoint(self._db_pool, mic)
                session.restore(ckpt.session_id, ckpt.session_last_ts)

            # Metrics
            self._ll_metrics[mic] = LiveListenerMetrics(mic=mic)

        # Start health server
        self._start_health_server()

        # Start per-mic pipelines
        for spec in self._config.devices:
            task = asyncio.create_task(
                self._run_mic_pipeline(spec),
                name=f"live-listener-pipeline-{spec.name}",
            )
            self._pipeline_tasks[spec.name] = task

        # Start heartbeat
        self._start_heartbeat()

        logger.info("live-listener: all pipelines started")

    async def stop(self) -> None:
        """Stop all pipelines, heartbeat, and health server."""
        logger.info("live-listener: stopping connector")

        # Cancel pipeline tasks
        for mic, task in self._pipeline_tasks.items():
            task.cancel()
        for mic, task in self._pipeline_tasks.items():
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("live-listener: error stopping pipeline for mic=%s", mic)
        self._pipeline_tasks.clear()

        # Disconnect transcription clients
        for mic, client in self._transcription_clients.items():
            try:
                await client.disconnect()
            except Exception:
                logger.exception(
                    "live-listener: error disconnecting transcription client for mic=%s", mic
                )
        self._transcription_clients.clear()

        # Stop heartbeat
        if self._heartbeat is not None:
            await self._heartbeat.stop()

        # Close MCP client
        try:
            await self._mcp_client.aclose()
        except Exception:
            logger.exception("live-listener: error closing MCP client")

        logger.info("live-listener: connector stopped")

    async def run_forever(self) -> None:
        """Start all components and wait until a KeyboardInterrupt or cancellation."""
        await self.start()
        try:
            # Wait for all pipeline tasks; they loop forever until cancelled
            await asyncio.gather(*self._pipeline_tasks.values(), return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ------------------------------------------------------------------
    # Per-mic pipeline
    # ------------------------------------------------------------------

    async def _run_mic_pipeline(self, spec: MicDeviceSpec) -> None:
        """Run the full audio→VAD→transcription→…→ingest pipeline for one mic.

        Loops forever. Exceptions from ``_pipeline_once`` are caught, logged,
        and retried after a brief delay so a single mic failure never takes
        down the whole connector.
        """
        mic = spec.name
        backoff = self._config.reconnect_base_s
        max_backoff = self._config.reconnect_max_s

        while True:
            try:
                await self._pipeline_once(spec)
                backoff = self._config.reconnect_base_s
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._mic_states[mic].connected = False
                self._mic_states[mic].last_error = str(exc)
                logger.warning(
                    "live-listener: mic=%s pipeline error: %s; retrying in %.1fs",
                    mic,
                    exc,
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    raise
                backoff = min(backoff * 2, max_backoff)

    async def _pipeline_once(self, spec: MicDeviceSpec) -> None:
        """Run the pipeline for one mic until sounddevice raises or is cancelled.

        Opens a MicPipeline (uses sounddevice under the hood) and feeds frames
        through VAD → transcription → filter gate → discretion → ingest.
        """
        from butlers.connectors.live_listener.audio import MicPipeline

        mic = spec.name
        mic_state = self._mic_states[mic]
        ll_metrics = self._ll_metrics[mic]

        # Build VAD
        vad_config = VadConfig(
            onset_threshold=self._config.vad_onset_threshold,
            offset_threshold=self._config.vad_offset_threshold,
            onset_frames=self._config.vad_onset_frames,
            offset_frames=self._config.vad_offset_frames,
            min_segment_ms=self._config.min_segment_ms,
            max_segment_ms=self._config.max_segment_ms,
        )
        silero = SileroVad()  # no model_path → returns 0.0 (silence) until model loaded
        vad = VadStateMachine(config=vad_config, model=silero, mic_name=mic)
        vad.load()

        # Build filter evaluator (fail-open if no DB)
        filter_evaluator = create_filter_evaluator(
            device_name=spec.name,
            db_pool=self._db_pool,
        )

        # GAP-4: perform initial filter load before audio capture begins (per spec).
        await filter_evaluator.ensure_loaded()
        # GAP-3: warn once per rule ID for any non-mic_id rule type in this scope.
        warn_non_mic_id_rules(filter_evaluator)

        logger.info("live-listener: opening mic pipeline for mic=%s", mic)

        # Capture the running event loop here (safe: we're inside an async method).
        # The PortAudio callback thread has no event loop of its own; using
        # get_running_loop() in the async context avoids the deprecated
        # get_event_loop() call and ensures we always target the correct loop.
        loop = asyncio.get_running_loop()

        def on_frame(frame: bytes) -> None:
            """Synchronous frame callback; dispatches to VAD and queues segments."""
            vad_start = time.monotonic()
            offset_ts = time.monotonic()
            segments = vad.process_frame(frame, offset_ts=offset_ts)
            vad_elapsed = time.monotonic() - vad_start
            ll_metrics.observe_stage_latency("vad", vad_elapsed)

            for seg in segments:
                logger.info(
                    "live-listener: VAD segment mic=%s duration=%.0fms forced_split=%s",
                    mic, seg.duration_ms, seg.forced_split,
                )
                # Schedule async processing without blocking the PortAudio callback thread
                loop.call_soon_threadsafe(self._schedule_segment, spec, seg, filter_evaluator)

        mic_pipeline = MicPipeline(spec=spec, config=self._config, on_frame=on_frame)

        async with mic_pipeline:
            mic_state.connected = True
            mic_state.last_error = None
            logger.info("live-listener: mic=%s pipeline running", mic)
            # Keep running until cancelled
            while True:
                await asyncio.sleep(1.0)

        mic_state.connected = False

    def _schedule_segment(
        self,
        spec: MicDeviceSpec,
        segment: SpeechSegment,
        filter_evaluator: Any,
    ) -> None:
        """Schedule async segment processing from the event loop thread.

        Holds a strong reference to the created task to prevent it from being
        garbage-collected before completion (Python asyncio GC caveat).
        """
        task = asyncio.create_task(
            self._process_segment(spec, segment, filter_evaluator),
            name=f"segment-{spec.name}",
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _process_segment(
        self,
        spec: MicDeviceSpec,
        segment: SpeechSegment,
        filter_evaluator: Any,
    ) -> None:
        """Process a completed speech segment through the full pipeline.

        Pipeline: transcription → filter_gate → discretion → envelope → ingest
        """
        mic = spec.name
        ll_metrics = self._ll_metrics[mic]
        e2e_start = time.monotonic()

        # Record segment duration
        duration_s = segment.duration_ms / 1000.0
        ll_metrics.observe_segment_duration(duration_s)

        # --- Transcription ---
        transcription_client = self._transcription_clients.get(mic)
        if transcription_client is None:
            logger.warning("live-listener: no transcription client for mic=%s", mic)
            ll_metrics.inc_segments("transcription_failed")
            return

        t_start = time.monotonic()
        try:
            result = await transcription_client.transcribe(segment.audio_bytes)
        except Exception as exc:
            ll_metrics.inc_transcription_failure(type(exc).__name__.lower())
            ll_metrics.inc_segments("transcription_failed")
            self._mic_states[mic].transcription_healthy = False
            logger.warning("live-listener: transcription error for mic=%s: %s", mic, exc)
            return
        transcription_elapsed = time.monotonic() - t_start
        ll_metrics.observe_stage_latency("transcription", transcription_elapsed)

        if result is None:
            # Dropped by transcription client (empty/low confidence/service unavail)
            ll_metrics.inc_segments("transcribed")  # counted but result discarded
            self._mic_states[mic].transcription_healthy = transcription_client.healthy
            return

        self._mic_states[mic].transcription_healthy = True
        logger.info(
            "live-listener: transcription mic=%s confidence=%.2f text=%r",
            mic, result.confidence, result.text,
        )

        # --- Filter gate ---
        try:
            decision = evaluate_voice_filter(filter_evaluator, spec.name)
        except Exception as exc:
            logger.warning("live-listener: filter gate error for mic=%s: %s (fail-open)", mic, exc)
            decision = SimpleNamespace(allowed=True)  # fail-open

        if not decision.allowed:
            ll_metrics.inc_segments("discarded_silence")  # reuse "discarded_silence" for filtered
            return

        # --- Pre-filter (heuristic gate before expensive discretion LLM) ---
        prefilter = self._prefilters.get(mic)
        if prefilter is not None:
            pf_result = prefilter.evaluate(result.text, timestamp=time.time())
            ll_metrics.inc_prefilter(pf_result.reason)
            if not pf_result.allowed:
                ll_metrics.inc_segments("prefiltered")
                return

        # --- Discretion ---
        evaluator = self._discretion_evaluators.get(mic)
        if evaluator is None:
            logger.warning("live-listener: no discretion evaluator for mic=%s", mic)
            return

        d_start = time.monotonic()
        try:
            disc_result = await evaluator.evaluate(
                result.text,
                timestamp=time.time(),
            )
        except Exception as exc:
            ll_metrics.inc_discretion_failure(type(exc).__name__.lower())
            self._mic_states[mic].discretion_healthy = False
            logger.warning("live-listener: discretion error for mic=%s: %s (fail-open)", mic, exc)
            # fail-open: treat as FORWARD
            disc_result = DiscretionResult(
                verdict="FORWARD",
                reason=f"fail-open: {type(exc).__name__}",
                is_fail_open=True,
            )
        discretion_elapsed = time.monotonic() - d_start
        ll_metrics.observe_stage_latency("discretion", discretion_elapsed)

        self._mic_states[mic].discretion_healthy = True

        if disc_result.verdict == "IGNORE":
            ll_metrics.inc_discretion("ignore")
            ll_metrics.inc_segments("discarded_silence")
            return

        # Record verdict
        verdict_label = "error_forward" if disc_result.is_fail_open else "forward"
        ll_metrics.inc_discretion(verdict_label)

        # --- Envelope construction ---
        observed_at = datetime.fromtimestamp(segment.offset_ts, tz=UTC)
        unix_ms = unix_ms_from_datetime(observed_at)

        session = self._sessions.get(mic)
        if session is None:
            logger.warning("live-listener: no session tracker for mic=%s", mic)
            return

        session_id = session.get_or_create_session(unix_ms)

        envelope = build_voice_envelope(
            device_name=spec.name,
            unix_ms=unix_ms,
            session_id=session_id,
            observed_at=observed_at,
            transcript=result.text,
            confidence=result.confidence,
            duration_s=duration_s,
            language=result.language,
            discretion_reason=disc_result.reason,
        )

        # --- Ingest submission ---
        submission_start = time.monotonic()
        try:
            ingest_result = await self._mcp_client.call_tool("ingest", envelope)
            submission_elapsed = time.monotonic() - submission_start
            ll_metrics.observe_stage_latency("submission", submission_elapsed)

            # Check response
            status = "success"
            if isinstance(ingest_result, dict):
                status_str = ingest_result.get("status", "")
                if status_str == "duplicate":
                    status = "duplicate"
            self._connector_metrics.record_ingest_submission(status)
            ll_metrics.inc_segments("transcribed")

        except Exception as exc:
            submission_elapsed = time.monotonic() - submission_start
            ll_metrics.observe_stage_latency("submission", submission_elapsed)
            self._connector_metrics.record_ingest_submission("error")
            self._connector_metrics.record_error(type(exc).__name__.lower(), "ingest_submit")
            logger.warning("live-listener: ingest submission failed for mic=%s: %s", mic, exc)
            return

        # Persist checkpoint after successful submission (accepted or duplicate).
        # Kept outside the ingest try/except so a checkpoint DB error never
        # pollutes ingest metrics or logs.
        if self._db_pool is not None:
            try:
                await save_voice_checkpoint(
                    self._db_pool,
                    mic,
                    last_utterance_ts=unix_ms,
                    session_id=session.session_id,
                    session_last_ts=session.session_last_ts_ms,
                )
            except Exception:
                logger.exception(
                    "live-listener: unexpected error saving checkpoint for mic=%s", mic
                )

        # Record e2e latency
        e2e_elapsed = time.monotonic() - e2e_start
        ll_metrics.observe_e2e_latency(e2e_elapsed)

        logger.debug(
            "live-listener: submitted utterance mic=%s text=%r e2e=%.3fs",
            mic,
            result.text[:60],
            e2e_elapsed,
        )

    # ------------------------------------------------------------------
    # Health state
    # ------------------------------------------------------------------

    def get_health_state(self) -> tuple[str, str | None]:
        """Derive current health state and error message.

        Returns:
            (state, error_message) where state is 'healthy', 'degraded', or 'error'.

        Health state derivation (per spec):
        - ``error``: no audio devices are capturing (all failed / no devices)
        - ``degraded``: any mic has a failed device, OR transcription / discretion unhealthy
        - ``healthy``: all mic pipelines active and all services responsive
        """
        states = list(self._mic_states.values())

        if not states:
            return "error", "no microphone devices configured"

        # Check if any mic is connected
        connected_mics = [s for s in states if s.connected]
        if not connected_mics:
            msgs = [f"mic:{s.mic_name}={s.last_error or 'not_connected'}" for s in states]
            return "error", "; ".join(msgs)

        # Check for degraded state
        degraded_parts: list[str] = []
        for s in states:
            if not s.connected:
                degraded_parts.append(f"mic:{s.mic_name}=device_disconnected")
            elif not s.transcription_healthy:
                degraded_parts.append(f"mic:{s.mic_name}=transcription_degraded")
            elif not s.discretion_healthy:
                degraded_parts.append(f"mic:{s.mic_name}=discretion_degraded")

        if degraded_parts:
            return "degraded", ", ".join(degraded_parts)

        # All healthy — build positive status message
        health_parts = [f"mic:{s.mic_name}=healthy" for s in states]
        return "healthy", ", ".join(health_parts)

    # ------------------------------------------------------------------
    # Health server
    # ------------------------------------------------------------------

    def _start_health_server(self) -> None:
        """Start FastAPI health server in a background thread."""
        app = FastAPI(title="Live Listener Connector Health")

        connector = self  # closure ref

        @app.get("/health")
        async def health() -> dict[str, Any]:
            state, error_message = connector.get_health_state()
            return {
                "status": state,
                "error_message": error_message,
                "uptime_s": int(time.monotonic() - connector._start_time),
                "timestamp": datetime.now(UTC).isoformat(),
            }

        @app.get("/metrics")
        async def metrics() -> bytes:
            return generate_latest(REGISTRY)

        from butlers.connectors.health_socket import make_health_socket

        port = self._health_port
        sock = make_health_socket("127.0.0.1", port)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
        self._health_server = uvicorn.Server(config)

        def run_server() -> None:
            asyncio.run(self._health_server.serve(sockets=[sock]))

        self._health_thread = Thread(target=run_server, daemon=True)
        self._health_thread.start()
        logger.info("live-listener: health server started on port %d", port)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _start_heartbeat(self) -> None:
        """Initialise and start the heartbeat background task."""
        # Use a combined endpoint identity for the heartbeat
        ep_id = "live-listener:connector"

        heartbeat_config = HeartbeatConfig.from_env(
            connector_type=CONNECTOR_TYPE,
            endpoint_identity=ep_id,
        )

        self._heartbeat = ConnectorHeartbeat(
            config=heartbeat_config,
            mcp_client=self._mcp_client,
            metrics=self._connector_metrics,
            get_health_state=self.get_health_state,
        )
        self._heartbeat.start()


# ---------------------------------------------------------------------------
# Module entrypoint
# ---------------------------------------------------------------------------


async def run_connector() -> None:
    """Parse config, build connector, and run until interrupted."""
    configure_logging()

    logger.info("live-listener: loading configuration from environment")
    config = LiveListenerConfig.from_env()

    logger.info(
        "live-listener: configured %d mic(s): %s",
        len(config.devices),
        [d.name for d in config.devices],
    )

    if not config.devices:
        logger.error("live-listener: no microphone devices configured; exiting")
        raise SystemExit(1)

    # Optional: wait for Switchboard to be ready before starting
    from butlers.connectors.mcp_client import wait_for_switchboard_ready

    try:
        await wait_for_switchboard_ready(config.switchboard_mcp_url)
    except TimeoutError as exc:
        logger.error("live-listener: switchboard not ready: %s", exc)
        raise SystemExit(1) from exc

    connector = LiveListenerConnector(config=config)

    try:
        await connector.run_forever()
    except KeyboardInterrupt:
        logger.info("live-listener: received keyboard interrupt, shutting down")
    finally:
        await connector.stop()


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_connector())
