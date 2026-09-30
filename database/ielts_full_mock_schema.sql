-- ============================================================
-- FULL IELTS MOCK EXAM EXTENSION
-- Additive migration. Run after the existing IELTS schema.
-- ============================================================

create table if not exists public.ielts_mock_sections (
    id uuid primary key default gen_random_uuid(),
    mock_id uuid not null references public.ielts_mock_tests(id) on delete cascade,
    user_id uuid not null references auth.users(id) on delete cascade,
    section text not null check (section in ('listening','reading','writing','speaking')),
    part integer not null check (part between 1 and 4),
    status text not null default 'ready'
        check (status in ('ready','in_progress','completed')),
    content jsonb not null default '{}'::jsonb,
    answers jsonb not null default '{}'::jsonb,
    result jsonb not null default '{}'::jsonb,
    raw_score integer,
    total_questions integer,
    estimated_band numeric(2,1)
        check (estimated_band is null or estimated_band between 0 and 9),
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz not null default now(),
    unique(mock_id, section, part)
);

create index if not exists idx_ielts_mock_sections_mock
on public.ielts_mock_sections(mock_id, section, part);

create index if not exists idx_ielts_mock_sections_user
on public.ielts_mock_sections(user_id, created_at desc);

alter table public.ielts_mock_sections enable row level security;

drop policy if exists "ielts mock sections own select" on public.ielts_mock_sections;
create policy "ielts mock sections own select"
on public.ielts_mock_sections for select to authenticated
using (auth.uid() = user_id);

drop policy if exists "ielts mock sections own insert" on public.ielts_mock_sections;
create policy "ielts mock sections own insert"
on public.ielts_mock_sections for insert to authenticated
with check (auth.uid() = user_id);

drop policy if exists "ielts mock sections own update" on public.ielts_mock_sections;
create policy "ielts mock sections own update"
on public.ielts_mock_sections for update to authenticated
using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "ielts mock sections own delete" on public.ielts_mock_sections;
create policy "ielts mock sections own delete"
on public.ielts_mock_sections for delete to authenticated
using (auth.uid() = user_id);
