begin;

create table public.reviewers (
    user_id uuid primary key references auth.users (id) on delete cascade,
    full_name text not null check (btrim(full_name) <> ''),
    phone text,
    role text not null check (role in ('admin', 'reviewer')),
    active boolean not null default false
);

alter table public.reviewers enable row level security;

revoke all on table public.reviewers from public, anon, authenticated;
grant select on table public.reviewers to authenticated;

create policy "Read own reviewer access"
    on public.reviewers
    for select
    to authenticated
    using ((select auth.uid()) = user_id);

commit;
