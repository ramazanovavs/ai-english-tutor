-- Backup bank for General English Listening tests.
-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.

create table if not exists public.listening_bank (
    id uuid primary key default gen_random_uuid(),
    level text not null check (level in ('A1','A2','B1','B2','C1','C2')),
    format text,
    topic text,
    title text,
    content jsonb not null,          -- script, speakers, 10 questions with answers
    audio_path text not null,        -- file path in the "listening-audio" storage bucket
    size_bytes integer,
    times_used integer not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_listening_bank_level on public.listening_bank(level);

-- Only the server (service-role key) reads this table: answers must not reach browsers.
alter table public.listening_bank enable row level security;

-- Private bucket for the MP3 files (no public URLs; the server streams them).
insert into storage.buckets (id, name, public)
values ('listening-audio', 'listening-audio', false)
on conflict (id) do nothing;
