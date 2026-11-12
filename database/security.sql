-- Security hardening (run in the Supabase SQL Editor; safe to run again).
--
-- 1) The browser never talks to the database directly: every request goes through the FastAPI server,
--    which uses the service-role key. So we remove ALL row-level policies in the public schema and keep
--    RLS on: with the public anon key a logged-in student can no longer read answer keys (tests,
--    ielts_mock_sections) or change his own levels and bands (student_skills, profiles, ielts_*).
do $$
declare
    r record;
begin
    for r in select schemaname, tablename, policyname from pg_policies where schemaname = 'public' loop
        execute format('drop policy if exists %I on %I.%I', r.policyname, r.schemaname, r.tablename);
    end loop;
    for r in select tablename from pg_tables where schemaname = 'public' loop
        execute format('alter table public.%I enable row level security', r.tablename);
    end loop;
end $$;

-- Belt and braces: the browser roles get no table privileges at all.
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;
alter default privileges in schema public revoke all on functions from anon, authenticated;

-- 2) Journal of AI calls (no prompts or learner texts are stored).
create table if not exists public.ai_usage (
    id bigserial primary key,
    user_id uuid,
    path text,
    kind text not null,            -- text / stream / tts / stt / audio
    model text,
    input_tokens integer,
    output_tokens integer,
    units integer,                 -- characters (tts) or seconds of audio (stt, audio)
    duration_ms integer,
    ok boolean not null default true,
    error text,
    cost_usd numeric(12,6),
    created_at timestamptz not null default now()
);
create index if not exists idx_ai_usage_created on public.ai_usage(created_at desc);
create index if not exists idx_ai_usage_user on public.ai_usage(user_id, created_at desc);
alter table public.ai_usage enable row level security;

-- 3) Consents: terms of use and personal data, and a separate consent for voice recordings.
create table if not exists public.user_consents (
    user_id uuid primary key references auth.users(id) on delete cascade,
    terms_version text not null,
    accepted_at timestamptz not null default now(),
    voice boolean not null default false,
    voice_at timestamptz
);
alter table public.user_consents enable row level security;

revoke all on public.ai_usage, public.user_consents from anon, authenticated;
