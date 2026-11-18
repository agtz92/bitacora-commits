-- Esquema de la bitácora en Postgres (Supabase). Todo vive en el schema "bitacora".
-- La app se conecta con un rol propio (bitacora_app) que solo tiene acceso a este schema;
-- ver README, sección "Base de datos".

create schema if not exists bitacora;

create table if not exists bitacora.repos (
  id bigint primary key,                    -- id de GitHub (sobrevive renombres)
  full_name text not null,
  owner text not null,
  name text not null,
  private boolean not null default false,
  fork boolean not null default false,
  archived boolean not null default false,
  html_url text,
  created_at timestamptz,
  pushed_at timestamptz,
  first_seen_at timestamptz not null default now(),
  fetched_pushed_at timestamptz,            -- pushed_at de la última descarga de commits
  needs_fetch boolean not null default false,
  last_fetch_at timestamptz,
  last_error text,
  gone boolean not null default false       -- ya no aparece en la cuenta
);
create table if not exists bitacora.repo_branches (
  repo_id bigint not null references bitacora.repos(id) on delete cascade,
  name text not null,
  head_sha text not null,
  primary key (repo_id, name)
);
create table if not exists bitacora.commits (
  repo_id bigint not null references bitacora.repos(id) on delete cascade,
  sha text not null,
  committed_at timestamptz not null,        -- fecha de autor
  local_date date not null,                 -- en TIMEZONE
  local_time text not null,                 -- HH:MM en TIMEZONE
  month text not null,                      -- YYYY-MM en TIMEZONE
  author_name text not null,
  author_email text,
  is_bot boolean not null default false,
  message text not null,                    -- primera línea
  primary key (repo_id, sha)
);
create index if not exists commits_month on bitacora.commits(month);
create table if not exists bitacora.repo_clients (
  repo_id bigint not null references bitacora.repos(id) on delete cascade,
  client text not null,
  pos int not null default 0,
  primary key (repo_id, client)
);
create table if not exists bitacora.repo_tags (
  repo_id bigint not null references bitacora.repos(id) on delete cascade,
  tag text not null,
  pos int not null default 0,
  primary key (repo_id, tag)
);
-- Clientes/etiquetas importados para repos que aún no se han descubierto.
create table if not exists bitacora.pending_meta (
  name text primary key,
  payload jsonb not null
);
create table if not exists bitacora.sync_runs (
  id bigserial primary key,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  forced boolean not null default false,
  status text not null default 'running',  -- running | done
  repos_total int,
  new_repos jsonb not null default '[]',
  repos_fetched int not null default 0,
  new_commits int not null default 0,
  errors jsonb not null default '[]'
);
