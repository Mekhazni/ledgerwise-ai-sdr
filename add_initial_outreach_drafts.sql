begin;

alter table public.messages add column initial_outreach jsonb
    check (initial_outreach is null or (direction = 'out' and jsonb_typeof(initial_outreach) = 'object'));

create table public.initial_outreach_jobs (
    company_id bigint primary key references public.companies (id) on delete cascade,
    contact_id bigint not null references public.company_contacts (id) on delete cascade,
    token uuid not null default gen_random_uuid(),
    state text not null check (state in ('generating', 'ready', 'failed')),
    started_at timestamptz not null default now()
);
alter table public.initial_outreach_jobs enable row level security;
revoke all on public.initial_outreach_jobs from public, anon, authenticated, service_role;
grant select on public.initial_outreach_jobs to service_role;

create function public.start_initial_outreach(
    p_company bigint, p_actor uuid, p_primary bigint, p_backup bigint, p_retry boolean
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    result text;
    previous_primary bigint;
    job public.initial_outreach_jobs%rowtype;
begin
    if not exists (select 1 from public.reviewers where user_id = p_actor
                   and active and role in ('admin', 'reviewer')) then
        return jsonb_build_object('status', 'forbidden');
    end if;
    select primary_contact_id into previous_primary from public.companies
    where id = p_company for update;
    if not found then
        return jsonb_build_object('status', 'not_found');
    end if;
    if p_retry then
        select primary_contact_id, backup_contact_id into p_primary, p_backup
        from public.companies where id = p_company;
    end if;
    result := public.save_outreach_contacts(p_company, p_actor, p_primary, p_backup);
    if result <> 'saved' then
        return jsonb_build_object('status', result);
    end if;

    if previous_primary is distinct from p_primary then
        delete from public.initial_outreach_jobs where company_id = p_company;
        update public.messages m set status = 'rejected', approved_version = null
        from public.threads t where m.thread_id = t.id and t.company_id = p_company
            and m.initial_outreach is not null and m.status in ('draft', 'approved');
    end if;
    if p_primary is null or exists (
        select 1 from public.messages m join public.threads t on t.id = m.thread_id
        where t.company_id = p_company and m.initial_outreach is not null and m.status = 'sent'
    ) then
        return jsonb_build_object('status', 'saved');
    end if;

    select * into job from public.initial_outreach_jobs where company_id = p_company;
    if found and (not p_retry or job.state = 'ready'
        or (job.state = 'generating' and job.started_at > now() - interval '3 minutes')) then
        return jsonb_build_object('status', 'saved');
    end if;

    insert into public.initial_outreach_jobs (company_id, contact_id, state)
    values (p_company, p_primary, 'generating')
    on conflict (company_id) do update
    set contact_id = excluded.contact_id, token = excluded.token, state = excluded.state,
        started_at = excluded.started_at
    returning * into job;
    return jsonb_build_object('status', 'generate', 'token', job.token, 'contact_id', p_primary);
end;
$$;

create function public.finish_initial_outreach(
    p_company bigint, p_token uuid, p_draft jsonb, p_test_recipient text
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
    current_contact bigint;
    job public.initial_outreach_jobs%rowtype;
    new_thread bigint;
begin
    select primary_contact_id into current_contact from public.companies where id = p_company for update;
    select * into job from public.initial_outreach_jobs
    where company_id = p_company and token = p_token and state = 'generating';
    if not found or current_contact is distinct from job.contact_id then
        return false;
    end if;
    if p_draft is null or coalesce((select decision from public.company_reviews where company_id = p_company
        order by id desc limit 1), '') not in ('confirmed_fit', 'demo_fit') then
        update public.initial_outreach_jobs set state = 'failed' where company_id = p_company;
        return false;
    end if;

    insert into public.threads (company_id, test_recipient) values (p_company, p_test_recipient)
    returning id into new_thread;
    insert into public.messages (thread_id, direction, subject, body, status, facts_used, initial_outreach)
    values (new_thread, 'out', p_draft->>'subject', p_draft->>'body', 'draft',
            p_draft->'facts_used', p_draft->'metadata');
    update public.initial_outreach_jobs set state = 'ready' where company_id = p_company;
    return true;
end;
$$;

revoke all on function public.start_initial_outreach(bigint, uuid, bigint, bigint, boolean)
from public, anon, authenticated, service_role;
revoke all on function public.finish_initial_outreach(bigint, uuid, jsonb, text)
from public, anon, authenticated, service_role;
grant execute on function public.start_initial_outreach(bigint, uuid, bigint, bigint, boolean) to service_role;
grant execute on function public.finish_initial_outreach(bigint, uuid, jsonb, text) to service_role;

commit;
