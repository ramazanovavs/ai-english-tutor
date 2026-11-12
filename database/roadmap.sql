-- Level roadmap ("путеводитель") and placement test: one row per learner.
-- Run once in the Supabase SQL Editor.
create table if not exists public.learner_roadmap (
    user_id uuid primary key references auth.users(id) on delete cascade,
    levels jsonb not null default '{}'::jsonb,     -- {"overall": "B1.2", "grammar": "B1.2", ...}
    items jsonb not null default '{}'::jsonb,      -- can-do statements: {"B1.2-g1": {"status": "confirmed", ...}}
    placement jsonb not null default '{}'::jsonb,  -- current / last placement test (contains answer keys)
    placed_at timestamptz,
    updated_at timestamptz not null default now()
);

-- Only the server (service role) reads and writes this table: the placement state contains answer keys.
alter table public.learner_roadmap enable row level security;
