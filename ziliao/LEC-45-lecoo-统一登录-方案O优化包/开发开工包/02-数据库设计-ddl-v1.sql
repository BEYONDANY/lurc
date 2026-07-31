-- =====================================================================
-- lecoo 统一登录 · 方案 O · PostgreSQL DDL V1.0
-- schema: gov（治理）/ audit（审计，只增不改）
-- 硬边界：不存储可验证密码哈希 / Passkey；凭证在 Logto
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS gov;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE gov.users (
  id              text PRIMARY KEY,               -- 全局身份 ID（可与 Logto sub 对齐或映射）
  email           citext UNIQUE,
  name            text,
  avatar_url      text,
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','disabled','deleted')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE gov.identity_links (
  id              bigserial PRIMARY KEY,
  user_id         text NOT NULL REFERENCES gov.users(id),
  provider        text NOT NULL,                  -- logto / google / feishu ...
  subject         text NOT NULL,
  raw_claims      jsonb,
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, subject)
);

CREATE TABLE gov.tenants (
  id              text PRIMARY KEY,
  name            text NOT NULL,
  slug            citext UNIQUE NOT NULL,
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','suspended','deleted')),
  created_by      text REFERENCES gov.users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE gov.memberships (
  id              bigserial PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES gov.tenants(id),
  user_id         text NOT NULL REFERENCES gov.users(id),
  role            text NOT NULL CHECK (role IN ('owner','admin','member')),
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('invited','active','disabled')),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, user_id)
);
CREATE INDEX idx_memberships_user ON gov.memberships(user_id);

CREATE TABLE gov.invites (
  id              bigserial PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES gov.tenants(id),
  email           citext NOT NULL,
  role            text NOT NULL CHECK (role IN ('owner','admin','member')),
  token_hash      text NOT NULL UNIQUE,
  invited_by      text REFERENCES gov.users(id),
  expires_at      timestamptz NOT NULL,
  accepted_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE gov.apps (
  id              text PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES gov.tenants(id),
  name            text NOT NULL,
  client_id       text UNIQUE NOT NULL,
  client_secret_hash text,                        -- 仅哈希；明文一次性返回
  app_type        text NOT NULL CHECK (app_type IN ('web','spa','native')),
  redirect_uris   text[] NOT NULL,
  logout_uris     text[] NOT NULL DEFAULT '{}',
  status          text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('draft','pending','active','rejected','disabled')),
  created_by      text REFERENCES gov.users(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_apps_tenant ON gov.apps(tenant_id);

CREATE TABLE gov.permission_catalog (
  id              bigserial PRIMARY KEY,
  tenant_id       text NOT NULL REFERENCES gov.tenants(id),
  app_id          text NOT NULL REFERENCES gov.apps(id),
  perm_key        text NOT NULL,
  perm_name       text NOT NULL,
  description     text,
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, app_id, perm_key)
);

-- 业务侧会话索引（辅助踢人；权威会话在 Logto）
CREATE TABLE gov.business_sessions (
  id              text PRIMARY KEY,
  user_id         text NOT NULL REFERENCES gov.users(id),
  tenant_id       text REFERENCES gov.tenants(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  revoked_at      timestamptz
);
CREATE INDEX idx_biz_sess_user ON gov.business_sessions(user_id) WHERE revoked_at IS NULL;

CREATE TABLE audit.events (
  id              bigserial PRIMARY KEY,
  tenant_id       text,
  actor_user_id   text,
  action          text NOT NULL,                  -- login.success / member.invite / secret.rotate / kick ...
  object_type     text,
  object_id       text,
  detail          jsonb NOT NULL DEFAULT '{}',
  ip              inet,
  user_agent      text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_tenant_time ON audit.events(tenant_id, created_at DESC);

-- Redis 键规范（不入库）：
-- ratelimit:login:{ip} TTL
-- invite:lock:{token_hash} 一次性
-- kick:user:{user_id} 指令扇出
