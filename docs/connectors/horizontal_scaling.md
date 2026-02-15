# Connector Horizontal Scaling and Coordinated Checkpointing

Status: Normative (Target State for Future Enhancement)
Last updated: 2026-02-15
Priority: P3 (single-instance deployment works for v1)

## 1. Purpose

This document defines patterns for horizontally scaling connector deployments while maintaining exactly-once ingestion semantics through coordinated checkpoint management. For high-throughput or high-availability deployments, a single connector instance per endpoint identity may become a bottleneck or single point of failure. This guide provides architectural patterns and implementation strategies for scaling beyond the single-instance model.

**Prerequisites:**
- Familiarity with `docs/connectors/interface.md` (connector contract)
- Understanding of `docs/runbooks/connector_operations.md` (single-instance operations)
- Knowledge of the current checkpoint model (durable file-based cursors)

**When to Consider Horizontal Scaling:**
- Ingest throughput exceeds single-instance capacity (>1000 events/sec sustained)
- Source API rate limits require parallelization across multiple client identities
- High-availability requirements demand active-active or active-standby redundancy
- Geographic distribution benefits from region-local connector instances
- Operational resilience requires zero-downtime deployments and rolling updates

**Current State (v1):**
Connectors use a simple single-process-per-endpoint-identity model with file-based checkpoints. This design trades simplicity for scalability and assumes:
- One process owns the checkpoint file exclusively
- No concurrent writers to the same checkpoint
- Process-local semaphores for in-flight concurrency control

Horizontal scaling introduces coordination requirements not present in the current implementation.

## 2. Current Single-Instance Deployment Model

### 2.1 Architecture

**Deployment Topology:**
```
┌─────────────────────────────────────┐
│  Source System (Telegram/Gmail)     │
└───────────────┬─────────────────────┘
                │ (updates/events)
                ▼
┌─────────────────────────────────────┐
│  Connector Process                  │
│  ┌───────────────────────────────┐  │
│  │ Event Loop                    │  │
│  │ - Poll/subscribe to source    │  │
│  │ - Normalize to ingest.v1      │  │
│  │ - Submit to Switchboard API   │  │
│  │ - Update checkpoint on success│  │
│  └───────────────────────────────┘  │
│                                     │
│  Checkpoint: cursor.json            │
│  {"last_update_id": 12345}          │
└───────────────┬─────────────────────┘
                │ (ingest.v1 HTTP POST)
                ▼
┌─────────────────────────────────────┐
│  Switchboard Ingest API             │
│  - Dedupe + assign request_id       │
│  - Route to specialist butlers      │
└─────────────────────────────────────┘
```

**Key Characteristics:**
- Single owner of checkpoint state
- No distributed coordination required
- Crash recovery: replay from last saved checkpoint
- At-least-once delivery from source to connector
- Exactly-once effect at Switchboard (via dedupe)

### 2.2 Checkpoint Model

**Storage:** File-based JSON (e.g., `telegram_cursor.json`, `gmail_cursor.json`)

**Write Pattern:**
1. Fetch batch from source
2. Submit each event to Switchboard ingest API
3. On success (or duplicate): advance in-memory cursor
4. Persist cursor atomically (write to `.tmp`, then rename)

**Safety Properties:**
- Atomic writes prevent partial checkpoint corruption
- Checkpoint advancement only after confirmed ingest acceptance
- Process-local synchronization (no external locks)
- Restart-safe: replay from last checkpoint (duplicates are idempotent)

**Limitations:**
- No support for multiple concurrent writers
- No lease/lock mechanism for checkpoint ownership
- File-based state doesn't scale beyond single instance

## 3. Horizontal Scaling Patterns

### 3.1 Pattern 1: Lease-Based Coordination

**Use Case:** Active-standby high availability with automatic failover.

**Architecture:**
```
┌──────────────────────────────────────────────────┐
│  Coordination Store (Redis/etcd/Consul)          │
│  - Lease: "telegram:bot123" → instance-A (TTL)   │
│  - Checkpoint: {"last_update_id": 12345}         │
└──────────────────┬───────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌──────────────┐      ┌──────────────┐
│ Instance A   │      │ Instance B   │
│ (active)     │      │ (standby)    │
│ - Holds lease│      │ - Waits for  │
│ - Ingests    │      │   lease      │
│ - Updates ckpt      │ - No work    │
└──────────────┘      └──────────────┘
```

**Implementation Strategy:**

1. **Lease Acquisition:**
   - On startup, attempt to acquire lease for `{channel}:{endpoint_identity}`
   - Lease TTL: 10-30 seconds (balance failover latency vs heartbeat overhead)
   - Lease renewal: every TTL/3 interval while active
   - If acquisition fails, enter standby mode

2. **Checkpoint Management:**
   - Active instance reads checkpoint from coordination store on lease acquisition
   - Updates checkpoint in coordination store after successful batch ingestion
   - Standby instances do not read or write checkpoint

3. **Failover:**
   - On active instance failure: lease expires after TTL
   - Standby instance acquires lease and reads last checkpoint
   - Resumes ingestion from checkpoint (at-least-once replay)
   - Switchboard dedupe ensures exactly-once effect

4. **Lease Renewal:**
   ```python
   async def renew_lease_loop(self):
       while self.active:
           await asyncio.sleep(self.lease_ttl / 3)
           try:
               await self.coordination_client.renew_lease(
                   key=self.lease_key,
                   instance_id=self.instance_id,
                   ttl=self.lease_ttl
               )
           except LeaseExpired:
               logger.warning("Lost lease, entering standby mode")
               self.active = False
               await self.enter_standby_mode()
   ```

**Trade-offs:**
- ✅ Simple failover model (active-standby)
- ✅ No event ordering concerns (single active writer)
- ✅ Compatible with existing checkpoint semantics
- ❌ No throughput gain (only availability improvement)
- ❌ Lease heartbeat overhead
- ❌ Failover latency bounded by lease TTL

**Recommended Technology:**
- Redis with `SET EX NX` for lease acquisition
- etcd with lease primitives
- Consul with session-based KV locking

### 3.2 Pattern 2: Distributed Locking with Sharded Checkpoints

**Use Case:** Active-active parallelization with work distribution across multiple instances.

**Architecture:**
```
┌──────────────────────────────────────────────────────┐
│  Coordination Store                                   │
│  - Lock: "telegram:bot123:shard-0" → instance-A      │
│  - Lock: "telegram:bot123:shard-1" → instance-B      │
│  - Checkpoint: {"shard-0": {"offset": 100}, ...}     │
└──────────────────┬───────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌──────────────┐      ┌──────────────┐
│ Instance A   │      │ Instance B   │
│ Shard 0      │      │ Shard 1      │
│ - Process    │      │ - Process    │
│   offset 0-99│      │   offset 100+│
└──────────────┘      └──────────────┘
```

**Implementation Strategy:**

1. **Work Partitioning:**
   - Divide source event stream into deterministic shards
   - Sharding key examples:
     - Telegram: `update_id % num_shards`
     - Gmail: `hash(message_id) % num_shards`
     - Generic: `hash(external_event_id) % num_shards`

2. **Shard Assignment:**
   - Each instance attempts to acquire locks for available shards
   - Dynamic shard rebalancing on instance join/leave
   - Bounded shard count per instance (prevent overloading)

3. **Per-Shard Checkpoints:**
   - Each shard maintains independent checkpoint
   - Checkpoint update only when shard lock is held
   - Global checkpoint = union of all shard checkpoints

4. **Coordination Example:**
   ```python
   async def acquire_shards(self, target_shard_count: int):
       acquired = []
       for shard_id in range(self.total_shards):
           if len(acquired) >= target_shard_count:
               break
           lock_key = f"{self.endpoint_identity}:shard-{shard_id}"
           if await self.lock_manager.try_acquire(lock_key, ttl=30):
               checkpoint = await self.load_shard_checkpoint(shard_id)
               acquired.append((shard_id, checkpoint))
       return acquired
   ```

5. **Event Processing:**
   - Each instance polls/subscribes to full event stream
   - Filter events by shard assignment: `hash(event_id) % total_shards in assigned_shards`
   - Process only events belonging to owned shards
   - Update shard-specific checkpoint on success

**Trade-offs:**
- ✅ Horizontal throughput scaling (N instances = N× throughput)
- ✅ Dynamic rebalancing on topology changes
- ✅ Fault isolation (shard failure doesn't affect others)
- ❌ Increased coordination overhead (lock heartbeats per shard)
- ❌ Event filtering waste (fetch full stream, process subset)
- ❌ Complex failure recovery (multiple checkpoints to reconcile)
- ❌ Ordering guarantees weaker (per-shard ordering only)

**Recommended Technology:**
- Redis with RedLock algorithm for distributed locking
- etcd with lease-based locking
- PostgreSQL advisory locks (if coordination store already uses Postgres)

### 3.3 Pattern 3: Partition-Based Scaling (Source-Native Sharding)

**Use Case:** Source systems that natively support partitioning or multiple independent feeds.

**Architecture:**
```
┌──────────────────────────────────────────┐
│  Source System with Native Partitions    │
│  - Partition 0: updates 0-999            │
│  - Partition 1: updates 1000-1999        │
│  - Partition 2: updates 2000-2999        │
└────┬─────────────────┬──────────────┬────┘
     │                 │              │
     ▼                 ▼              ▼
┌──────────┐    ┌──────────┐   ┌──────────┐
│Instance A│    │Instance B│   │Instance C│
│Partition 0    │Partition 1   │Partition 2
│Checkpoint:    │Checkpoint:   │Checkpoint:
│{"offset":999} │{"offset":1999│{"offset":2999}
└──────────┘    └──────────┘   └──────────┘
```

**Implementation Strategy:**

1. **Static Partition Assignment:**
   - Each instance configured with specific partition ID(s)
   - Environment variable: `CONNECTOR_PARTITIONS=0,1` (comma-separated)
   - No dynamic rebalancing (operator-managed)

2. **Independent Checkpoints:**
   - Each partition has dedicated checkpoint file or key
   - No coordination required between instances
   - Checkpoint path: `{cursor_path}.partition-{id}`

3. **Deployment:**
   - Deploy N instances with disjoint partition assignments
   - Kubernetes: use StatefulSet with partition ID derived from pod ordinal
   - Docker: use compose with environment override per service

4. **Example Sources:**
   - Kafka topics with native partitions
   - Gmail with multiple mailbox aliases (one instance per alias)
   - Telegram with multiple bot tokens (one instance per bot)

**Trade-offs:**
- ✅ Zero coordination overhead
- ✅ Simple operational model (statically partitioned)
- ✅ No event filtering waste (source delivers only assigned partition)
- ✅ Clean failure isolation
- ❌ Requires source-native partitioning support
- ❌ No dynamic rebalancing (manual scaling)
- ❌ Partition assignment must be statically configured
- ❌ Not applicable to single-feed sources (e.g., single Gmail mailbox)

**Recommended Use Cases:**
- Multiple independent endpoint identities (e.g., bot fleet)
- Multi-tenant deployments (one connector per tenant)
- Geographic sharding (one connector per region-specific endpoint)

### 3.4 Pattern 4: Kafka-Style Consumer Group Coordination

**Use Case:** High-throughput multi-instance deployment with automatic work rebalancing.

**Architecture:**
```
┌──────────────────────────────────────────────────────┐
│  Coordination Store (e.g., Kafka-compatible broker)  │
│  - Consumer Group: "telegram-bot123-connectors"      │
│  - Partition 0 → Instance A                          │
│  - Partition 1 → Instance B                          │
│  - Partition 2 → Instance A                          │
│  - Offsets: {0: 1234, 1: 5678, 2: 9012}              │
└──────────────────┬───────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
        ▼                     ▼
┌──────────────┐      ┌──────────────┐
│ Instance A   │      │ Instance B   │
│ Partitions:  │      │ Partitions:  │
│   0, 2       │      │   1          │
└──────────────┘      └──────────────┘
```

**Implementation Strategy:**

1. **Intermediate Queue Layer:**
   - Deploy Kafka/Pulsar/RabbitMQ between source and connectors
   - Single producer: fetches from source, writes to queue with deterministic partitioning
   - Multiple consumers: connector instances form consumer group

2. **Producer Responsibilities:**
   - Fetch events from source API (Telegram getUpdates, Gmail history)
   - Partition events: `hash(event_id) % num_partitions`
   - Publish to queue with event as message body
   - Track source checkpoint separately (producer-side only)

3. **Consumer Responsibilities (Connector Instances):**
   - Join consumer group: `telegram-{endpoint_identity}-connectors`
   - Subscribe to all partitions (coordinator assigns subset)
   - Consume messages, normalize to `ingest.v1`, submit to Switchboard
   - Commit offsets after successful Switchboard acceptance

4. **Automatic Rebalancing:**
   - Consumer group protocol handles partition reassignment
   - On instance join: partitions rebalanced across N+1 instances
   - On instance leave/crash: orphaned partitions reassigned to survivors

5. **Example with Kafka:**
   ```python
   from aiokafka import AIOKafkaConsumer

   consumer = AIOKafkaConsumer(
       f"telegram-{self.endpoint_identity}",
       bootstrap_servers=os.environ["KAFKA_BROKERS"],
       group_id=f"telegram-{self.endpoint_identity}-connectors",
       enable_auto_commit=False,  # Manual commit after Switchboard acceptance
       auto_offset_reset="earliest",
   )

   async for msg in consumer:
       event = json.loads(msg.value)
       await self.submit_to_switchboard(event)
       await consumer.commit()  # Only after Switchboard accepts
   ```

**Trade-offs:**
- ✅ Proven rebalancing algorithms (Kafka, Pulsar)
- ✅ Horizontal scaling with automatic work distribution
- ✅ Strong at-least-once delivery guarantees
- ✅ Decouples source fetch from ingestion (buffering)
- ❌ Operational complexity (queue infrastructure required)
- ❌ Additional latency (source → queue → connector → Switchboard)
- ❌ Cost overhead (queue cluster)
- ❌ Two-phase commit semantics (queue offset + Switchboard dedupe)

**Recommended Technology:**
- Kafka for high-throughput multi-partition scenarios
- Pulsar for geo-replication and tiered storage
- RabbitMQ for simpler deployments with moderate scale

## 4. Coordinated Checkpoint Management

### 4.1 Checkpoint Storage Backends

**File-Based (Current v1 Model):**
- Pros: Simple, no dependencies, local atomicity
- Cons: Single-instance only, no distributed coordination
- Recommendation: v1 baseline, not suitable for horizontal scaling

**Redis:**
- Pros: Low latency, built-in expiration/TTL, pub/sub for coordination
- Cons: Requires Redis deployment, data durability depends on persistence config
- Recommendation: Good for lease-based or distributed locking patterns
- Example:
  ```python
  # Atomic checkpoint update with compare-and-set
  await redis.set(
      f"checkpoint:{endpoint_identity}",
      json.dumps({"last_update_id": new_offset}),
      ex=86400,  # Expire after 1 day of inactivity
  )
  ```

**etcd:**
- Pros: Strong consistency, native lease primitives, watch-based coordination
- Cons: Higher operational complexity, requires etcd cluster
- Recommendation: Best for lease-based coordination with strict consistency
- Example:
  ```python
  # Lease-based checkpoint with automatic expiration
  lease = await etcd.lease(ttl=30)
  await etcd.put(
      f"checkpoint/{endpoint_identity}",
      json.dumps(checkpoint),
      lease=lease.id
  )
  ```

**PostgreSQL:**
- Pros: Transactional consistency, advisory locks, already used by Butlers
- Cons: Higher write latency than Redis/etcd, coordination overhead
- Recommendation: Acceptable if avoiding additional infrastructure; use advisory locks
- Example:
  ```sql
  -- Acquire distributed lock
  SELECT pg_try_advisory_lock(hashtext('telegram:bot123'));

  -- Update checkpoint in transaction
  BEGIN;
  INSERT INTO connector_checkpoints (endpoint_identity, checkpoint, updated_at)
  VALUES ('telegram:bot123', '{"last_update_id": 12345}', NOW())
  ON CONFLICT (endpoint_identity) DO UPDATE
  SET checkpoint = EXCLUDED.checkpoint, updated_at = NOW();
  COMMIT;

  -- Release lock
  SELECT pg_advisory_unlock(hashtext('telegram:bot123'));
  ```

**DynamoDB / Cloud KV Stores:**
- Pros: Managed service, auto-scaling, regional replication
- Cons: Cloud-specific, cost, eventual consistency (unless strongly consistent reads)
- Recommendation: Suitable for cloud-native deployments with budget for managed services

### 4.2 Checkpoint Schema Evolution

**Single-Instance Checkpoint (v1):**
```json
{
  "last_update_id": 12345,
  "last_updated_at": "2026-02-15T10:00:00Z"
}
```

**Lease-Based Checkpoint:**
```json
{
  "checkpoint": {
    "last_update_id": 12345,
    "last_updated_at": "2026-02-15T10:00:00Z"
  },
  "lease": {
    "holder_instance_id": "instance-abc123",
    "acquired_at": "2026-02-15T09:55:00Z",
    "expires_at": "2026-02-15T10:00:30Z"
  }
}
```

**Shard-Based Checkpoint:**
```json
{
  "global_metadata": {
    "total_shards": 4,
    "last_rebalance_at": "2026-02-15T09:00:00Z"
  },
  "shards": {
    "0": {
      "offset": 1000,
      "last_updated_at": "2026-02-15T10:00:00Z",
      "holder_instance_id": "instance-A"
    },
    "1": {
      "offset": 1500,
      "last_updated_at": "2026-02-15T10:00:05Z",
      "holder_instance_id": "instance-B"
    },
    "2": {
      "offset": 1200,
      "last_updated_at": "2026-02-15T10:00:02Z",
      "holder_instance_id": "instance-A"
    },
    "3": {
      "offset": 1400,
      "last_updated_at": "2026-02-15T10:00:03Z",
      "holder_instance_id": "instance-B"
    }
  }
}
```

**Consumer Group Checkpoint (Kafka-style):**
- Stored in Kafka consumer offsets topic (managed by broker)
- Application-level checkpoint may still track Switchboard submission state separately

### 4.3 Conflict Resolution and Fencing

**Problem:** Two instances believe they own the same checkpoint and both try to update it.

**Fencing Strategies:**

1. **Generation Numbers (Epoch Fencing):**
   ```python
   # Checkpoint includes generation number
   checkpoint = {
       "generation": 42,
       "last_update_id": 12345
   }

   # Update only succeeds if generation matches
   def update_checkpoint(new_checkpoint, expected_generation):
       current = load_checkpoint()
       if current["generation"] != expected_generation:
           raise FencingTokenExpired("Generation mismatch")
       new_checkpoint["generation"] = expected_generation + 1
       save_checkpoint(new_checkpoint)
   ```

2. **Compare-And-Swap (CAS):**
   ```python
   # Redis CAS example
   def update_checkpoint_cas(key, new_value):
       with redis.pipeline() as pipe:
           while True:
               try:
                   pipe.watch(key)
                   current = pipe.get(key)
                   pipe.multi()
                   pipe.set(key, new_value)
                   pipe.execute()
                   break
               except redis.WatchError:
                   # Retry if value changed during transaction
                   continue
   ```

3. **Lease-Based Fencing:**
   - Only instance holding valid lease can update checkpoint
   - Lease validation before every checkpoint write
   - Lease renewal failure triggers immediate work stoppage

**Recommendation:** Use lease-based fencing for active-standby, CAS for active-active sharding.

## 5. Operational Recommendations

### 5.1 When to Scale Horizontally

**Proceed with caution if:**
- Single-instance deployment meets current and projected load (recommended for v1)
- Team lacks operational experience with distributed systems
- Source API rate limits are not a bottleneck
- Cost of coordination infrastructure outweighs benefits

**Consider horizontal scaling when:**
- Sustained ingest rate exceeds 1000 events/sec per endpoint
- Single-instance CPU/memory utilization consistently >80%
- High-availability SLO requires <1 minute failover time
- Source system supports native partitioning (low coordination cost)
- Multi-region deployment requires local ingestion instances

### 5.2 Scaling Decision Matrix

| Scenario | Recommended Pattern | Coordination Complexity | Throughput Gain | HA Benefit |
|----------|---------------------|-------------------------|-----------------|------------|
| High-availability only | Lease-based active-standby | Low | None | High |
| Moderate throughput increase | Distributed locking + sharding | Medium | 2-4× | Medium |
| High throughput, native partitions | Partition-based static assignment | None | Linear | High |
| Very high throughput (>10k/sec) | Kafka-style consumer groups | High | Linear | High |

### 5.3 Monitoring and Observability

**Key Metrics for Scaled Deployments:**

1. **Checkpoint Lag per Shard/Instance:**
   - `connector_checkpoint_lag_seconds{instance_id, shard_id}`
   - Alert if lag >60 seconds for any shard

2. **Lease Health:**
   - `connector_lease_renewals_total{instance_id, status=success|failure}`
   - `connector_lease_holder{endpoint_identity}` (gauge: which instance holds lease)
   - Alert on repeated renewal failures

3. **Coordination Store Latency:**
   - `connector_coordination_operation_duration_seconds{operation=acquire|renew|release}`
   - Alert if p95 >500ms

4. **Shard Rebalancing:**
   - `connector_shard_rebalance_total{reason=join|leave|failure}`
   - `connector_assigned_shards{instance_id}` (gauge)

5. **Duplicate Detection Rate:**
   - `connector_ingest_duplicates_total` (should be low; high rate indicates checkpoint issues)

**Logging:**
- Log all lease acquisitions/releases/expirations with instance ID
- Log shard assignments on rebalancing
- Log checkpoint updates with shard ID, offset, and instance ID

### 5.4 Deployment Patterns

**Kubernetes Deployment (Lease-Based):**
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: telegram-bot-connector
spec:
  replicas: 2  # Active-standby
  template:
    spec:
      containers:
      - name: connector
        image: butlers/telegram-connector:latest
        env:
        - name: SWITCHBOARD_API_BASE_URL
          value: "http://switchboard:8000"
        - name: CONNECTOR_COORDINATION_BACKEND
          value: "redis"
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: redis-config
              key: url
        - name: CONNECTOR_LEASE_TTL_S
          value: "30"
```

**Kubernetes StatefulSet (Partition-Based):**
```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: telegram-bot-connector
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: connector
        image: butlers/telegram-connector:latest
        env:
        - name: CONNECTOR_PARTITION_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.labels['apps.kubernetes.io/pod-index']
        - name: CONNECTOR_TOTAL_PARTITIONS
          value: "3"
        - name: CONNECTOR_CURSOR_PATH
          value: "/data/cursor-$(CONNECTOR_PARTITION_ID).json"
        volumeMounts:
        - name: data
          mountPath: /data
  volumeClaimTemplates:
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 1Gi
```

## 6. Migration Path from Single-Instance

### 6.1 Phase 1: Validation (No Code Changes)

1. Deploy second instance in standby mode (no lease acquisition)
2. Verify both instances can read checkpoint
3. Monitor metrics to confirm no cross-instance interference
4. Run for 7 days to establish baseline

### 6.2 Phase 2: Active-Standby Deployment

1. Deploy coordination store (Redis/etcd)
2. Migrate checkpoint from file to coordination store (one-time copy)
3. Update connector code to use lease-based checkpoint reads/writes
4. Deploy two instances with lease coordination enabled
5. Verify automatic failover: kill active instance, standby should take over within TTL
6. Run canary for 14 days

### 6.3 Phase 3: Active-Active Sharding (Optional)

1. Implement shard-based event filtering
2. Define static shard count (start with 2-4 shards)
3. Update checkpoint schema to per-shard format
4. Deploy N instances with shard assignment logic
5. Monitor for balanced load distribution
6. Gradually increase shard count as load grows

### 6.4 Rollback Plan

At any phase, rollback to single-instance:
1. Stop all scaled instances
2. Copy latest checkpoint from coordination store to file
3. Start single v1 instance with file-based checkpoint
4. Verify ingestion resumes from correct offset
5. Duplicates during rollback are safe (Switchboard dedupe)

## 7. Security Considerations

**Coordination Store Access Control:**
- Use authentication for Redis/etcd (TLS + password/client certs)
- Restrict network access to connector instances only
- Encrypt checkpoint data at rest if it contains sensitive metadata

**Lease Hijacking Prevention:**
- Include cryptographic instance identity in lease holder field
- Validate lease holder on every checkpoint write
- Audit all lease acquisitions and releases

**Checkpoint Tampering:**
- Use signed checkpoints (HMAC or digital signatures)
- Verify signature before accepting checkpoint on failover
- Log all checkpoint modifications with instance ID and timestamp

## 8. Testing Strategies

**Unit Tests:**
- Lease acquisition/renewal/release logic
- Checkpoint CAS operations
- Shard assignment determinism

**Integration Tests:**
- Deploy 2-3 instances with real coordination store (testcontainers Redis/etcd)
- Simulate active instance crash → verify standby takeover
- Inject duplicate events → verify Switchboard dedupe prevents double-processing
- Test shard rebalancing on instance join/leave

**Chaos Engineering:**
- Random instance termination during active ingestion
- Network partition between instances and coordination store
- Coordination store restart during checkpoint update
- Clock skew simulation (lease expiration edge cases)

## 9. Future Enhancements (Beyond P3)

**Auto-Scaling Based on Lag:**
- Monitor checkpoint lag per shard
- Trigger horizontal scale-up when lag >threshold
- Automatic shard rebalancing on scale events

**Cross-Region Replication:**
- Deploy connector instances in multiple regions
- Coordinate checkpoints via globally-replicated store (DynamoDB Global Tables, Cosmos DB)
- Regional failover for disaster recovery

**Dynamic Shard Count Adjustment:**
- Start with small shard count (e.g., 4)
- Automatically split shards when per-shard load exceeds threshold
- Requires checkpoint migration and rebalancing

**Backpressure Signaling:**
- Connector monitors Switchboard ingest API latency/errors
- Dynamically throttles source polling rate
- Coordination store broadcasts backpressure state to all instances

## 10. References

- `docs/connectors/interface.md` - Connector contract and ingest.v1 format
- `docs/runbooks/connector_operations.md` - Single-instance operations
- `docs/connectors/telegram_bot.md` - Telegram connector specifics
- `docs/connectors/gmail.md` - Gmail connector specifics
- `docs/switchboard/api_authentication.md` - Token management for scaled deployments

**External References:**
- [Redis Distributed Locks (RedLock)](https://redis.io/docs/manual/patterns/distributed-locks/)
- [etcd Lease Documentation](https://etcd.io/docs/latest/learning/api/#lease-api)
- [Kafka Consumer Groups](https://kafka.apache.org/documentation/#consumerconfigs)
- [PostgreSQL Advisory Locks](https://www.postgresql.org/docs/current/explicit-locking.html#ADVISORY-LOCKS)
