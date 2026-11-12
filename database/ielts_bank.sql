-- IELTS bank: ready-made full mock papers and practice sets.
-- Used (1) as a backup when OpenAI is unavailable and (2) as a source of admin-verified exams.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.

create table if not exists public.ielts_bank (
    id uuid primary key default gen_random_uuid(),
    kind text not null check (kind in ('mock','practice')),
    -- mock: 'mock:Academic' / 'mock:General Training'
    -- practice: 'reading:Academic', 'reading:General Training', 'listening', 'writing:Academic:1',
    --           'writing:General Training:1', 'writing:2', 'speaking:1', 'speaking:2', 'speaking:3'
    bank_key text not null,
    title text,
    content jsonb not null,                       -- full content incl. answer keys (server only)
    audio jsonb not null default '{}'::jsonb,     -- {"1": "storage/path.mp3", ...}
    verified boolean not null default false,      -- approved by an admin
    times_used integer not null default 0,
    stats jsonb not null default '{}'::jsonb,     -- per-question answer statistics (detects wrong keys)
    source_id text,                               -- mock/test the item was copied from
    created_at timestamptz not null default now()
);

create index if not exists idx_ielts_bank_key on public.ielts_bank(bank_key);

create table if not exists public.ielts_bank_usage (
    user_id uuid not null references auth.users(id) on delete cascade,
    bank_id uuid not null references public.ielts_bank(id) on delete cascade,
    used_at timestamptz not null default now(),
    primary key (user_id, bank_id)
);

-- Server-only tables (answer keys must not reach browsers).
alter table public.ielts_bank enable row level security;
alter table public.ielts_bank_usage enable row level security;

-- Audio is stored in the private bucket created by listening_bank.sql:
insert into storage.buckets (id, name, public)
values ('listening-audio', 'listening-audio', false)
on conflict (id) do nothing;
