-- Private, host-owned authentication state. No raw browser secret is persisted.
CREATE SCHEMA dashboard_auth;
REVOKE ALL ON SCHEMA dashboard_auth FROM PUBLIC;
DO $$ BEGIN
 IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='dashboard_auth_api') THEN
  CREATE ROLE dashboard_auth_api NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
 END IF;
END $$;
CREATE TABLE dashboard_auth.instance (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 instance_id uuid NOT NULL DEFAULT gen_random_uuid(),
 origin text, rp_id text, key_generation text,
 state text NOT NULL DEFAULT 'keyless_unenrolled' CHECK(state IN
 ('keyless_unenrolled','keyless_enrolled','configured_key','recovery_pending')),
 credential_epoch bigint NOT NULL DEFAULT 1 CHECK(credential_epoch>0),
 session_epoch bigint NOT NULL DEFAULT 1 CHECK(session_epoch>0)
);
INSERT INTO dashboard_auth.instance(singleton) VALUES(true);
CREATE TABLE dashboard_auth.contexts (
 digest text PRIMARY KEY, csrf_digest text NOT NULL, expires_at timestamptz NOT NULL,
 credential_epoch bigint NOT NULL, revoked boolean NOT NULL DEFAULT false,
 finish_minute timestamptz, finish_count integer NOT NULL DEFAULT 0
);
CREATE TABLE dashboard_auth.intents (
 id text PRIMARY KEY, context_digest text NOT NULL,
 operation text NOT NULL CHECK(operation IN ('enroll','recover')),
 credential_epoch bigint NOT NULL, expires_at timestamptz NOT NULL,
 authorized boolean NOT NULL DEFAULT false, consumed boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX one_authorized_intent ON dashboard_auth.intents((true))
 WHERE authorized AND NOT consumed;
CREATE TABLE dashboard_auth.ceremonies (
 id text PRIMARY KEY, context_digest text NOT NULL, intent_id text,
 operation text NOT NULL CHECK(operation IN ('enroll','recover','login')),
 credential_epoch bigint NOT NULL, session_epoch bigint NOT NULL,
 challenge text NOT NULL, user_handle text, expires_at timestamptz NOT NULL,
 consumed boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE dashboard_auth.credentials (
 credential_epoch bigint PRIMARY KEY, credential_id text UNIQUE,
 credential_data text, user_handle text, backup_eligible boolean NOT NULL,
 backup_state boolean NOT NULL, counter bigint NOT NULL CHECK(counter>=0),
 active boolean NOT NULL DEFAULT true, retired_at timestamptz,
 CHECK(NOT backup_state OR backup_eligible)
);
CREATE UNIQUE INDEX one_active_credential ON dashboard_auth.credentials((true)) WHERE active;
CREATE TABLE dashboard_auth.sessions (
 digest text PRIMARY KEY, credential_epoch bigint NOT NULL, session_epoch bigint NOT NULL,
 expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE dashboard_auth.csrf (
 session_digest text NOT NULL, digest text NOT NULL, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(session_digest,digest)
);
CREATE TABLE dashboard_auth.rate_buckets (
 kind text PRIMARY KEY CHECK(kind IN ('context','options','finish')),
 minute timestamptz NOT NULL, count integer NOT NULL
);
CREATE TABLE dashboard_auth.audit (
 ts timestamptz NOT NULL DEFAULT clock_timestamp(),
 action text NOT NULL CHECK(action IN ('registration','login','session','logout','revoke',
 'authorize_registration','authorize_recovery','reconcile_mode','rebind_origin','cleanup')),
 outcome text NOT NULL CHECK(outcome IN ('success','denied','counter_anomaly')),
 actor text NOT NULL CHECK(actor IN ('owner','host_operator','unauthenticated'))
);
REVOKE ALL ON ALL TABLES IN SCHEMA dashboard_auth FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA dashboard_auth REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA dashboard_auth REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- This is the API verifier's capability surface, not a public HTTP RPC. The API
-- must verify FIDO cryptography before finish; SQL rechecks all durable authority.
CREATE FUNCTION dashboard_auth.api(action text, p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dashboard_auth AS $$
DECLARE
 s dashboard_auth.instance%ROWTYPE; c dashboard_auth.contexts%ROWTYPE;
 i dashboard_auth.intents%ROWTYPE; q dashboard_auth.ceremonies%ROWTYPE;
 k dashboard_auth.credentials%ROWTYPE; se dashboard_auth.sessions%ROWTYPE;
 t timestamptz := clock_timestamp(); deadline timestamptz; bucket text;
 cap integer; n integer; result jsonb; mode_ok boolean; browser_ok boolean;
BEGIN
 SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
 IF NOT FOUND THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 mode_ok := s.key_generation IS NOT DISTINCT FROM p->>'key_generation';
 browser_ok := COALESCE(mode_ok AND s.origin IS NOT NULL AND s.origin=p->>'origin'
                    AND s.rp_id=p->>'rp_id', false);
 IF s.state='keyless_enrolled' AND NOT EXISTS (
  SELECT FROM dashboard_auth.credentials WHERE active AND credential_epoch=s.credential_epoch
   AND credential_id IS NOT NULL AND credential_data IS NOT NULL AND user_handle IS NOT NULL
 ) THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 IF NOT mode_ok THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 IF action='status' THEN
  IF NOT browser_ok THEN RETURN '{"state":"unavailable","authenticated":false,"session_expires_at":null}'; END IF;
  SELECT * INTO se FROM dashboard_auth.sessions WHERE digest=p->>'session_digest'
   AND NOT revoked AND expires_at>t AND credential_epoch=s.credential_epoch
   AND session_epoch=s.session_epoch;
  RETURN jsonb_build_object('state',s.state,'authenticated',FOUND,
   'session_expires_at',se.expires_at);
 END IF;
 IF action='header' THEN
  IF s.state<>'configured_key' OR s.key_generation IS NULL THEN
   RETURN '{"error":"UNAUTHORIZED"}'; END IF;
  RETURN '{"method":"header","expires_at":null}';
 END IF;
 IF action IN ('authorize','csrf','revoke','revoke_all') THEN
  IF p->>'method'='header' THEN
   IF s.state<>'configured_key' OR s.key_generation IS NULL THEN
    RETURN '{"error":"UNAUTHORIZED"}'; END IF;
  ELSE
   IF NOT browser_ok THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
   SELECT * INTO se FROM dashboard_auth.sessions WHERE digest=p->>'session_digest'
    AND NOT revoked AND expires_at>t AND credential_epoch=s.credential_epoch
    AND session_epoch=s.session_epoch;
   IF NOT FOUND THEN RETURN '{"error":"UNAUTHORIZED"}'; END IF;
   IF COALESCE((p->>'unsafe')::boolean,false) AND NOT EXISTS (
    SELECT FROM dashboard_auth.csrf WHERE session_digest=se.digest
     AND digest=p->>'csrf_digest' AND expires_at>t
   ) THEN RETURN '{"error":"FORBIDDEN"}'; END IF;
  END IF;
  IF action='authorize' THEN
   RETURN jsonb_build_object('method',COALESCE(p->>'method','cookie'),'expires_at',se.expires_at);
  ELSIF action='csrf' THEN
   deadline := LEAST(se.expires_at,t+interval '30 minutes');
   DELETE FROM dashboard_auth.csrf WHERE session_digest=se.digest AND digest IN (
    SELECT digest FROM dashboard_auth.csrf WHERE session_digest=se.digest
     ORDER BY created_at DESC,digest OFFSET 3);
   INSERT INTO dashboard_auth.csrf(session_digest,digest,expires_at)
    VALUES(se.digest,p->>'new_csrf_digest',deadline);
   RETURN jsonb_build_object('csrf_expires_at',deadline);
  ELSIF action='revoke_all' THEN
   UPDATE dashboard_auth.instance SET session_epoch=session_epoch+1 WHERE singleton;
   UPDATE dashboard_auth.sessions SET revoked=true WHERE NOT revoked;
  ELSE
   UPDATE dashboard_auth.sessions SET revoked=true WHERE digest=p->>'session_digest';
  END IF;
  INSERT INTO dashboard_auth.audit(action,outcome,actor) VALUES
   (CASE WHEN action='revoke_all' THEN 'revoke' ELSE 'logout' END,'success','owner');
  RETURN '{}';
 END IF;
 IF NOT browser_ok THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 IF action='key_session' AND s.state<>'configured_key' THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 IF action NOT IN ('key_session','context') AND s.state='configured_key' THEN
  RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 -- Fixed, global buckets are independent of visitor-supplied labels.
 bucket := CASE WHEN action='context' THEN 'context'
  WHEN action IN ('registration_options','login_options') THEN 'options'
  WHEN action IN ('snapshot_registration','snapshot_login','key_session') THEN 'finish' END;
 IF bucket IS NOT NULL THEN
  cap := CASE bucket WHEN 'context' THEN 30 WHEN 'options' THEN 60 ELSE 120 END;
  INSERT INTO dashboard_auth.rate_buckets(kind,minute,count)
   VALUES(bucket,date_trunc('minute',t),1)
   ON CONFLICT(kind) DO UPDATE SET minute=EXCLUDED.minute,
    count=CASE WHEN rate_buckets.minute=EXCLUDED.minute THEN rate_buckets.count+1 ELSE 1 END
   RETURNING count INTO n;
  IF n>cap THEN RETURN '{"error":"RATE_LIMITED"}'; END IF;
 END IF;
 IF action='context' THEN
  SELECT count(*) INTO n FROM dashboard_auth.contexts WHERE NOT revoked AND expires_at>t;
  IF n>=64 THEN RETURN '{"error":"RATE_LIMITED"}'; END IF;
  deadline := t+interval '5 minutes';
  INSERT INTO dashboard_auth.contexts(digest,csrf_digest,expires_at,credential_epoch)
   VALUES(p->>'context_digest',p->>'csrf_digest',deadline,s.credential_epoch);
  RETURN jsonb_build_object('csrf_expires_at',deadline);
 END IF;
 IF action<>'key_session' THEN
  SELECT * INTO c FROM dashboard_auth.contexts WHERE digest=p->>'context_digest' FOR UPDATE;
  IF NOT FOUND OR c.revoked OR c.expires_at<=t THEN RETURN '{"error":"UNAUTHORIZED"}'; END IF;
  IF c.csrf_digest IS DISTINCT FROM p->>'csrf_digest' THEN RETURN '{"error":"FORBIDDEN"}'; END IF;
  IF c.credential_epoch<>s.credential_epoch THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  IF action IN ('snapshot_registration','snapshot_login') THEN
   UPDATE dashboard_auth.contexts SET finish_minute=date_trunc('minute',t),
    finish_count=CASE WHEN finish_minute=date_trunc('minute',t) THEN finish_count+1 ELSE 1 END
    WHERE digest=c.digest RETURNING finish_count INTO n;
   IF n>10 THEN RETURN '{"error":"RATE_LIMITED"}'; END IF;
  END IF;
 END IF;
 IF action='intent' THEN
  IF p->>'operation'='enroll' AND s.state<>'keyless_unenrolled' OR
     p->>'operation'='recover' AND s.state NOT IN ('keyless_enrolled','recovery_pending') OR
     p->>'operation' NOT IN ('enroll','recover') THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  SELECT * INTO i FROM dashboard_auth.intents WHERE context_digest=c.digest
   AND NOT consumed AND expires_at>t;
  IF FOUND THEN
   IF i.operation<>p->>'operation' THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
   RETURN jsonb_build_object('request_id',i.id,'expires_at',i.expires_at,
    'operation',i.operation,'canonical_origin',s.origin);
  END IF;
  SELECT count(*) INTO n FROM dashboard_auth.intents WHERE NOT consumed AND expires_at>t;
  IF n>=16 THEN RETURN '{"error":"RATE_LIMITED"}'; END IF;
  INSERT INTO dashboard_auth.intents(id,context_digest,operation,credential_epoch,expires_at)
   VALUES(p->>'request_id',c.digest,p->>'operation',s.credential_epoch,c.expires_at);
  RETURN jsonb_build_object('request_id',p->>'request_id','expires_at',c.expires_at,
   'operation',p->>'operation','canonical_origin',s.origin);
 END IF;
 IF action='cancel' THEN
  UPDATE dashboard_auth.intents SET consumed=true,authorized=false WHERE context_digest=c.digest
   AND NOT consumed AND (id=p->>'request_id' OR id=(
    SELECT intent_id FROM dashboard_auth.ceremonies
     WHERE id=p->>'ceremony_id' AND context_digest=c.digest));
  UPDATE dashboard_auth.ceremonies SET consumed=true WHERE context_digest=c.digest AND NOT consumed
   AND (id=p->>'ceremony_id' OR intent_id=p->>'request_id');
  RETURN '{}';
 END IF;
 IF action IN ('registration_options','login_options') THEN
  IF action='registration_options' THEN
   SELECT * INTO i FROM dashboard_auth.intents WHERE id=p->>'request_id' FOR UPDATE;
   IF NOT FOUND OR i.context_digest<>c.digest OR i.consumed OR i.expires_at<=t
    OR i.credential_epoch<>s.credential_epoch THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
   IF NOT i.authorized THEN RETURN jsonb_build_object('state','pending','expires_at',i.expires_at); END IF;
  ELSIF s.state<>'keyless_enrolled' THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  SELECT * INTO q FROM dashboard_auth.ceremonies WHERE context_digest=c.digest
   AND NOT consumed AND expires_at>t FOR UPDATE;
  IF FOUND THEN
   IF (action='login_options')<>(q.operation='login') THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
   RETURN to_jsonb(q);
  END IF;
  INSERT INTO dashboard_auth.ceremonies(id,context_digest,intent_id,operation,
   credential_epoch,session_epoch,challenge,user_handle,expires_at)
   VALUES(p->>'ceremony_id',c.digest,i.id,COALESCE(i.operation,'login'),s.credential_epoch,
    s.session_epoch,p->>'challenge',CASE WHEN i.id IS NOT NULL THEN p->>'user_handle' END,
    LEAST(c.expires_at,COALESCE(i.expires_at,c.expires_at))) RETURNING * INTO q;
  RETURN to_jsonb(q);
 END IF;
 IF action IN ('snapshot_registration','snapshot_login','finish_registration','finish_login') THEN
  -- Singleton -> context -> intent -> ceremony is the global lock order.
  SELECT * INTO i FROM dashboard_auth.intents WHERE id=(
   SELECT intent_id FROM dashboard_auth.ceremonies WHERE id=p->>'ceremony_id') FOR UPDATE;
  SELECT * INTO q FROM dashboard_auth.ceremonies WHERE id=p->>'ceremony_id' FOR UPDATE;
  IF NOT FOUND OR q.context_digest<>c.digest OR q.consumed OR q.expires_at<=t
   OR q.credential_epoch<>s.credential_epoch OR q.session_epoch<>s.session_epoch THEN
   RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  IF action IN ('snapshot_registration','finish_registration') THEN
   IF q.operation NOT IN ('enroll','recover') OR i.id IS NULL OR NOT i.authorized
    OR i.consumed OR i.expires_at<=t OR i.credential_epoch<>s.credential_epoch
    OR i.context_digest<>c.digest OR
    (q.operation='enroll' AND s.state<>'keyless_unenrolled') OR
    (q.operation='recover' AND s.state<>'recovery_pending') THEN
     RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  ELSE
   IF q.operation<>'login' OR s.state<>'keyless_enrolled' THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
   SELECT * INTO k FROM dashboard_auth.credentials WHERE active AND credential_epoch=s.credential_epoch FOR UPDATE;
   IF NOT FOUND THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
  END IF;
  IF action IN ('snapshot_registration','snapshot_login') THEN
   RETURN jsonb_build_object('ceremony',to_jsonb(q),'credential',to_jsonb(k));
  END IF;
  -- A verifier result is accepted only for the exact snapshot still current.
  IF p->>'challenge' IS DISTINCT FROM q.challenge OR
   (p->>'credential_epoch')::bigint<>s.credential_epoch THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  IF action='finish_registration' THEN
   IF EXISTS(SELECT FROM dashboard_auth.credentials WHERE active) THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
   INSERT INTO dashboard_auth.credentials(credential_epoch,credential_id,credential_data,
    user_handle,backup_eligible,backup_state,counter)
    VALUES(s.credential_epoch,p->>'credential_id',p->>'credential_data',q.user_handle,
     (p->>'backup_eligible')::boolean,(p->>'backup_state')::boolean,(p->>'counter')::bigint);
   UPDATE dashboard_auth.instance SET state='keyless_enrolled' WHERE singleton;
   UPDATE dashboard_auth.intents SET consumed=true,authorized=false WHERE id=i.id;
  ELSE
   IF p->>'credential_id' IS DISTINCT FROM k.credential_id OR
    (p->>'backup_eligible')::boolean IS DISTINCT FROM k.backup_eligible OR
    ((p->>'backup_state')::boolean AND NOT k.backup_eligible) OR
    (NOT k.backup_eligible AND (k.counter>0 OR (p->>'counter')::bigint>0)
     AND (p->>'counter')::bigint<=k.counter) THEN RETURN '{"error":"UNAUTHORIZED"}'; END IF;
   IF k.backup_eligible AND (p->>'counter')::bigint<=k.counter THEN
    INSERT INTO dashboard_auth.audit(action,outcome,actor) VALUES('login','counter_anomaly','owner');
   END IF;
   UPDATE dashboard_auth.credentials SET counter=GREATEST(counter,(p->>'counter')::bigint),
    backup_state=(p->>'backup_state')::boolean WHERE credential_epoch=s.credential_epoch;
  END IF;
  UPDATE dashboard_auth.ceremonies SET consumed=true WHERE id=q.id;
  UPDATE dashboard_auth.contexts SET revoked=true WHERE digest=c.digest;
 ELSIF action<>'key_session' THEN
  RETURN '{"error":"AUTH_UNAVAILABLE"}';
 END IF;
 deadline := t+interval '12 hours';
 INSERT INTO dashboard_auth.sessions(digest,credential_epoch,session_epoch,expires_at)
  VALUES(p->>'new_session_digest',s.credential_epoch,s.session_epoch,deadline);
 INSERT INTO dashboard_auth.csrf(session_digest,digest,expires_at)
  VALUES(p->>'new_session_digest',p->>'new_csrf_digest',t+interval '30 minutes');
 INSERT INTO dashboard_auth.audit(action,outcome,actor) VALUES
  (CASE action WHEN 'finish_registration' THEN 'registration' WHEN 'finish_login' THEN 'login'
   ELSE 'session' END,'success','owner');
 RETURN jsonb_build_object('csrf_expires_at',t+interval '30 minutes','session_expires_at',deadline);
END $$;
REVOKE ALL ON FUNCTION dashboard_auth.api(text,jsonb) FROM PUBLIC;
GRANT USAGE ON SCHEMA dashboard_auth TO dashboard_auth_api;
GRANT EXECUTE ON FUNCTION dashboard_auth.api(text,jsonb) TO dashboard_auth_api;

-- Only the schema owner / administrative host connection may execute this.
CREATE FUNCTION dashboard_auth.host(action text,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dashboard_auth AS $$
DECLARE s dashboard_auth.instance%ROWTYPE; i dashboard_auth.intents%ROWTYPE;
 t timestamptz := clock_timestamp(); next_state text; event text;
BEGIN
 SELECT * INTO s FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
 IF NOT FOUND THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
 IF action IN ('reconcile_mode','rebind_origin') THEN
  IF NOT COALESCE((p->>'confirm_revoke')::boolean,false) THEN RETURN '{"error":"FORBIDDEN"}'; END IF;
  IF action='reconcile_mode' THEN
   IF s.key_generation IS NOT DISTINCT FROM p->>'key_generation'
    AND s.origin IS NOT DISTINCT FROM p->>'origin' AND s.rp_id IS NOT DISTINCT FROM p->>'rp_id'
    THEN RETURN jsonb_build_object('operation',action,'canonical_origin',s.origin,'changed',false); END IF;
   IF s.origin IS NOT NULL AND (s.origin IS DISTINCT FROM p->>'origin' OR s.rp_id IS DISTINCT FROM p->>'rp_id')
    THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
   next_state := CASE WHEN p->>'key_generation' IS NULL THEN 'keyless_unenrolled' ELSE 'configured_key' END;
  ELSE
   IF p->>'origin' IS NULL OR p->>'rp_id' IS NULL THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
   IF s.key_generation IS DISTINCT FROM p->>'key_generation' THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
   next_state := CASE WHEN s.key_generation IS NULL THEN 'recovery_pending' ELSE 'configured_key' END;
  END IF;
  UPDATE dashboard_auth.instance SET credential_epoch=credential_epoch+1,session_epoch=session_epoch+1,
   state=next_state,origin=p->>'origin',rp_id=p->>'rp_id',key_generation=p->>'key_generation' WHERE singleton;
 ELSE
  IF s.origin IS DISTINCT FROM p->>'origin' OR s.rp_id IS DISTINCT FROM p->>'rp_id'
   OR s.origin IS NULL OR s.key_generation IS DISTINCT FROM p->>'key_generation'
   OR s.state='configured_key' THEN RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
  SELECT * INTO i FROM dashboard_auth.intents WHERE id=p->>'request_id' FOR UPDATE;
  IF NOT FOUND OR i.consumed OR i.expires_at<=t OR i.credential_epoch<>s.credential_epoch
   OR NOT EXISTS(SELECT FROM dashboard_auth.contexts WHERE digest=i.context_digest
    AND NOT revoked AND expires_at>t) THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  IF action='authorize_registration' THEN
   IF s.state<>'keyless_unenrolled' OR i.operation<>'enroll' THEN RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  ELSIF action='authorize_recovery' THEN
   IF NOT COALESCE((p->>'confirm_revoke')::boolean,false) THEN RETURN '{"error":"FORBIDDEN"}'; END IF;
   IF s.state NOT IN ('keyless_enrolled','recovery_pending') OR i.operation<>'recover' THEN
    RETURN '{"error":"AUTH_RESTART_REQUIRED"}'; END IF;
  ELSE RETURN '{"error":"AUTH_UNAVAILABLE"}'; END IF;
  IF i.authorized THEN RETURN jsonb_build_object('operation',action,'canonical_origin',s.origin,'changed',false); END IF;
  IF action='authorize_registration' THEN
   UPDATE dashboard_auth.intents SET consumed=true,authorized=false WHERE authorized AND id<>i.id;
   UPDATE dashboard_auth.intents SET authorized=true,expires_at=LEAST(expires_at,t+interval '5 minutes') WHERE id=i.id;
   INSERT INTO dashboard_auth.audit(action,outcome,actor) VALUES(action,'success','host_operator');
   RETURN jsonb_build_object('operation',action,'canonical_origin',s.origin,'changed',true);
  END IF;
  UPDATE dashboard_auth.instance SET credential_epoch=credential_epoch+1,session_epoch=session_epoch+1,
   state='recovery_pending' WHERE singleton;
 END IF;
 UPDATE dashboard_auth.credentials SET active=false,retired_at=t WHERE active;
 UPDATE dashboard_auth.sessions SET revoked=true WHERE NOT revoked;
 UPDATE dashboard_auth.ceremonies SET consumed=true WHERE NOT consumed;
 UPDATE dashboard_auth.intents SET consumed=true,authorized=false WHERE NOT consumed AND (i.id IS NULL OR id<>i.id);
 UPDATE dashboard_auth.contexts SET revoked=true WHERE NOT revoked AND (i.id IS NULL OR digest<>i.context_digest);
 IF i.id IS NOT NULL THEN
  UPDATE dashboard_auth.intents SET authorized=true,credential_epoch=s.credential_epoch+1,
   expires_at=LEAST(expires_at,t+interval '5 minutes') WHERE id=i.id;
  UPDATE dashboard_auth.contexts SET credential_epoch=s.credential_epoch+1 WHERE digest=i.context_digest;
 END IF;
 INSERT INTO dashboard_auth.audit(action,outcome,actor) VALUES(action,'success','host_operator');
 RETURN jsonb_build_object('operation',action,'canonical_origin',COALESCE(p->>'origin',s.origin),'changed',true);
END $$;
REVOKE ALL ON FUNCTION dashboard_auth.host(text,jsonb) FROM PUBLIC,dashboard_auth_api;

CREATE FUNCTION dashboard_auth.cleanup() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,dashboard_auth AS $$
DECLARE t timestamptz := clock_timestamp();
BEGIN
 -- A bounded batch per table. Repeated independent sweeps drain historical state.
 DELETE FROM dashboard_auth.intents WHERE id IN (SELECT id FROM dashboard_auth.intents WHERE expires_at<t-interval '23 hours' LIMIT 1000);
 DELETE FROM dashboard_auth.ceremonies WHERE id IN (SELECT id FROM dashboard_auth.ceremonies WHERE expires_at<t-interval '23 hours' LIMIT 1000);
 DELETE FROM dashboard_auth.contexts WHERE digest IN (SELECT digest FROM dashboard_auth.contexts WHERE expires_at<t-interval '23 hours' LIMIT 1000);
 DELETE FROM dashboard_auth.sessions WHERE digest IN (SELECT digest FROM dashboard_auth.sessions WHERE expires_at<t-interval '23 hours' LIMIT 1000);
 DELETE FROM dashboard_auth.csrf WHERE (session_digest,digest) IN (SELECT session_digest,digest FROM dashboard_auth.csrf WHERE expires_at<t-interval '23 hours' LIMIT 1000);
 UPDATE dashboard_auth.credentials SET credential_id=NULL,credential_data=NULL,user_handle=NULL
  WHERE NOT active AND retired_at<t-interval '23 hours' AND credential_id IS NOT NULL;
 DELETE FROM dashboard_auth.audit WHERE ctid IN (SELECT ctid FROM dashboard_auth.audit WHERE ts<t-interval '30 days' LIMIT 1000);
END $$;
REVOKE ALL ON FUNCTION dashboard_auth.cleanup() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION dashboard_auth.cleanup() TO dashboard_auth_api;
