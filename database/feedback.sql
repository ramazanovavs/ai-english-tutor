-- Feedback from testers (students, master's students, English teachers). Safe to run again.
create table if not exists public.feedback (
    id uuid primary key default gen_random_uuid(),
    user_id uuid references auth.users(id) on delete set null,
    email text,
    role text,                 -- student / masters / teacher / other
    section text not null,     -- menu item the tester worked with
    kind text,                 -- bug / inconvenience / wish / impression
    rating integer check (rating is null or rating between 1 and 5),
    comment text not null,
    context jsonb not null default '{}'::jsonb,   -- interface language, level, browser
    status text not null default 'new' check (status in ('new', 'seen', 'done')),
    admin_note text,
    created_at timestamptz not null default now()
);
create index if not exists idx_feedback_created on public.feedback(created_at desc);
-- Only the server (service role) reads and writes feedback.
alter table public.feedback enable row level security;
