-- ============================================================
-- IELTS EXTENSION FOR AI ENGLISH TUTOR
-- Safe additive migration for an existing full project schema.
-- Run this file if the General English schema already exists.
-- ============================================================

create extension if not exists pgcrypto;

create table if not exists public.ielts_profiles (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null unique references auth.users(id) on delete cascade,
    exam_type text not null default 'Academic'
        check (exam_type in ('Academic','General Training')),
    target_band numeric(2,1) not null default 6.5
        check (target_band between 1 and 9),
    planned_exam_date date,
    current_overall_band numeric(2,1)
        check (current_overall_band is null or current_overall_band between 0 and 9),
    listening_band numeric(2,1)
        check (listening_band is null or listening_band between 0 and 9),
    reading_band numeric(2,1)
        check (reading_band is null or reading_band between 0 and 9),
    writing_band numeric(2,1)
        check (writing_band is null or writing_band between 0 and 9),
    speaking_band numeric(2,1)
        check (speaking_band is null or speaking_band between 0 and 9),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.ielts_writing_attempts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    test_type text not null default 'Academic'
        check (test_type in ('Academic','General Training')),
    task_number integer not null check (task_number in (1,2)),
    prompt text not null,
    response_text text not null,
    word_count integer,
    task_score numeric(2,1) check (task_score is null or task_score between 0 and 9),
    coherence_cohesion numeric(2,1) check (coherence_cohesion is null or coherence_cohesion between 0 and 9),
    lexical_resource numeric(2,1) check (lexical_resource is null or lexical_resource between 0 and 9),
    grammatical_range_accuracy numeric(2,1) check (grammatical_range_accuracy is null or grammatical_range_accuracy between 0 and 9),
    estimated_band numeric(2,1) check (estimated_band is null or estimated_band between 0 and 9),
    feedback jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.ielts_speaking_attempts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    part integer not null check (part between 1 and 3),
    prompt text not null,
    transcript text not null,
    fluency_coherence numeric(2,1) check (fluency_coherence is null or fluency_coherence between 0 and 9),
    lexical_resource numeric(2,1) check (lexical_resource is null or lexical_resource between 0 and 9),
    grammatical_range_accuracy numeric(2,1) check (grammatical_range_accuracy is null or grammatical_range_accuracy between 0 and 9),
    pronunciation numeric(2,1) check (pronunciation is null or pronunciation between 0 and 9),
    provisional_language_band numeric(2,1) check (provisional_language_band is null or provisional_language_band between 0 and 9),
    feedback jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

comment on column public.ielts_speaking_attempts.pronunciation is
'Leave NULL for transcript-only assessment. Populate only when a valid acoustic pronunciation assessment is implemented.';

create table if not exists public.ielts_objective_attempts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    section text not null check (section in ('reading','listening')),
    test_type text not null default 'Academic'
        check (test_type in ('Academic','General Training')),
    title text not null,
    score_percent integer not null check (score_percent between 0 and 100),
    estimated_band numeric(2,1) check (estimated_band is null or estimated_band between 0 and 9),
    answers jsonb not null default '{}'::jsonb,
    details jsonb not null default '[]'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists public.ielts_band_history (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    section text not null check (section in ('listening','reading','writing','speaking','overall')),
    band numeric(2,1) not null check (band between 0 and 9),
    source text not null default 'ai_practice_estimate',
    note text,
    created_at timestamptz not null default now()
);

create table if not exists public.ielts_mock_tests (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    test_type text not null default 'Academic'
        check (test_type in ('Academic','General Training')),
    status text not null default 'in_progress'
        check (status in ('in_progress','completed','abandoned')),
    listening_band numeric(2,1),
    reading_band numeric(2,1),
    writing_band numeric(2,1),
    speaking_band numeric(2,1),
    overall_band numeric(2,1),
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    metadata jsonb not null default '{}'::jsonb
);

create index if not exists idx_ielts_writing_user_created
on public.ielts_writing_attempts(user_id, created_at desc);

create index if not exists idx_ielts_speaking_user_created
on public.ielts_speaking_attempts(user_id, created_at desc);

create index if not exists idx_ielts_objective_user_section
on public.ielts_objective_attempts(user_id, section, created_at desc);

create index if not exists idx_ielts_band_history_user
on public.ielts_band_history(user_id, created_at desc);

create index if not exists idx_ielts_mock_user
on public.ielts_mock_tests(user_id, started_at desc);

alter table public.ielts_profiles enable row level security;
alter table public.ielts_writing_attempts enable row level security;
alter table public.ielts_speaking_attempts enable row level security;
alter table public.ielts_objective_attempts enable row level security;
alter table public.ielts_band_history enable row level security;
alter table public.ielts_mock_tests enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array[
        'ielts_profiles','ielts_writing_attempts','ielts_speaking_attempts',
        'ielts_objective_attempts','ielts_band_history','ielts_mock_tests'
    ]
    loop
        execute format('drop policy if exists "ielts own select" on public.%I', t);
        execute format('create policy "ielts own select" on public.%I for select to authenticated using (auth.uid() = user_id)', t);
        execute format('drop policy if exists "ielts own insert" on public.%I', t);
        execute format('create policy "ielts own insert" on public.%I for insert to authenticated with check (auth.uid() = user_id)', t);
        execute format('drop policy if exists "ielts own update" on public.%I', t);
        execute format('create policy "ielts own update" on public.%I for update to authenticated using (auth.uid() = user_id) with check (auth.uid() = user_id)', t);
        execute format('drop policy if exists "ielts own delete" on public.%I', t);
        execute format('create policy "ielts own delete" on public.%I for delete to authenticated using (auth.uid() = user_id)', t);
    end loop;
end $$;
