begin;

create table public.company_reviews (
    id bigint generated always as identity primary key,
    company_id bigint not null references public.companies (id),
    reviewer_user_id uuid references auth.users (id) on delete set null,
    reviewer_name text not null,
    reviewed_at timestamptz not null default now(),
    decision text not null check (decision in ('confirmed_fit', 'rejected', 'needs_more_research')),
    notes text not null check (btrim(notes) <> ''),
    criteria jsonb not null check (
        jsonb_typeof(criteria) = 'object'
        and criteria ?& array['location', 'ecommerce', 'revenue', 'headcount']
    ),
    check (decision <> 'confirmed_fit' or criteria @> '{
        "location": {"status": "validated"}, "ecommerce": {"status": "validated"},
        "revenue": {"status": "validated"}, "headcount": {"status": "validated"}
    }'::jsonb)
);

create index company_reviews_company_history_idx
    on public.company_reviews (company_id, id desc);

alter table public.company_reviews enable row level security;
revoke all on public.company_reviews from public, anon, authenticated, service_role;
grant select on public.company_reviews to service_role;

create function public.record_company_review(
    p_company bigint, p_reviewer uuid, p_decision text, p_notes text, p_criteria jsonb
)
returns bigint
language plpgsql
security definer
set search_path = ''
as $$
declare
    review_id bigint;
begin
    insert into public.company_reviews (company_id, reviewer_user_id, reviewer_name, decision, notes, criteria)
    select p_company, user_id, full_name, p_decision, p_notes, p_criteria
    from public.reviewers
    where user_id = p_reviewer and active and role in ('admin', 'reviewer')
    returning id into review_id;

    return review_id;
end;
$$;

revoke all on function public.record_company_review(bigint, uuid, text, text, jsonb)
from public, anon, authenticated, service_role;
grant execute on function public.record_company_review(bigint, uuid, text, text, jsonb) to service_role;

commit;
