# Indexing Strategy

Load when adding indexes or tuning query performance. Butler query patterns are
heavily biased toward **recent data** — design indexes accordingly.

## Rules

1. **Every timestamp column used in WHERE or ORDER BY gets a descending index.** Butlers almost always want "most recent first."
   ```sql
   CREATE INDEX idx_<table>_<col> ON <table> (<col> DESC);
   ```

2. **Compound indexes for filtered recency queries.** Filter by a category and sort by time:
   ```sql
   CREATE INDEX idx_<table>_<filter>_recent ON <table> (<filter_col>, <time_col> DESC);
   ```

3. **GIN indexes for JSONB columns you search inside.** Use `jsonb_path_ops` for containment queries (`@>`), plain `GIN` if you also need key-existence checks (`?`, `?|`):
   ```sql
   CREATE INDEX idx_<table>_<col>_gin ON <table> USING GIN (<col> jsonb_path_ops);
   -- or plain GIN (established pattern in codebase):
   CREATE INDEX idx_<table>_<col>_gin ON <table> USING GIN (<col>);
   ```

4. **GIN indexes for JSONB array columns:**
   ```sql
   CREATE INDEX idx_<table>_tags_gin ON <table> USING GIN (tags);
   ```

5. **Partial indexes for hot subsets.** For queries over only active items or pending tasks:
   ```sql
   CREATE INDEX idx_<table>_active ON <table> (<col>) WHERE status = 'active';
   CREATE INDEX idx_tasks_due ON scheduled_tasks (next_run_at) WHERE enabled = true;
   ```

6. **GiST indexes for time-range overlap queries** (used by calendar):
   ```sql
   CREATE INDEX idx_<table>_time_window_gist
       ON <table> USING GIST (tstzrange(starts_at, ends_at, '[)'));
   ```

7. **IVFFLAT indexes for vector embeddings** (used by memory):
   ```sql
   CREATE INDEX idx_<table>_embedding
       ON <table> USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20);
   ```

8. **Don't index columns you never filter or sort on.** No index on `detail` unless you actually run JSONB containment queries against it.

## Naming Convention

The codebase uses both `idx_` and `ix_` prefixes (both acceptable). Be
consistent within a single migration file. Pattern: `idx_<table>_<column(s)>`.
