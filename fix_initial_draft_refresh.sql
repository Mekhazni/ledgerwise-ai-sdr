begin;

alter table public.initial_outreach_jobs add column if not exists failure_reason text;

create or replace function public.refresh_initial_personalization(p_company bigint, p_actor uuid, p_message bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    primary_contact bigint;
    job public.initial_outreach_jobs%rowtype;
begin
    if not exists (select 1 from public.reviewers where user_id = p_actor
                   and active and role in ('admin', 'reviewer')) then
        return jsonb_build_object('status', 'forbidden');
    end if;
    select primary_contact_id into primary_contact from public.companies
    where id = p_company for update;
    if primary_contact is null or coalesce((
        select decision from public.company_reviews where company_id = p_company order by id desc limit 1
    ), '') not in ('confirmed_fit', 'demo_fit') then
        return jsonb_build_object('status', 'fit_required');
    end if;
    if not exists (
        select 1 from public.messages m join public.threads t on t.id = m.thread_id
        where m.id = p_message and t.company_id = p_company and m.status = 'draft'
          and m.initial_outreach->>'contact_id' = primary_contact::text
          and coalesce(m.initial_outreach->>'personalization', 'limited') = 'limited'
    ) or exists (
        select 1 from public.messages m join public.threads t on t.id = m.thread_id
        where t.company_id = p_company and m.initial_outreach is not null and m.status in ('sent', 'approved')
    ) then
        return jsonb_build_object('status', 'ineligible_draft');
    end if;

    update public.initial_outreach_jobs set token = gen_random_uuid(), state = 'generating', started_at = now(), failure_reason = null
    where company_id = p_company and contact_id = primary_contact
      and (state in ('ready', 'failed') or (state = 'generating' and started_at <= now() - interval '3 minutes'))
    returning * into job;
    if not found then
        return jsonb_build_object('status', 'busy');
    end if;
    return jsonb_build_object('status', 'generate', 'token', job.token, 'contact_id', primary_contact);
end;
$$;

revoke all on function public.refresh_initial_personalization(bigint, uuid, bigint)
from public, anon, authenticated, service_role;
grant execute on function public.refresh_initial_personalization(bigint, uuid, bigint) to service_role;

create or replace function public.finish_initial_outreach(
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
    draft_thread bigint;
    next_version integer;
begin
    select primary_contact_id into current_contact from public.companies where id = p_company for update;
    select * into job from public.initial_outreach_jobs
    where company_id = p_company and token = p_token and state = 'generating';
    if not found or current_contact is distinct from job.contact_id then
        return false;
    end if;
    if p_draft is null or p_draft ? 'error' or coalesce((select decision from public.company_reviews where company_id = p_company
        order by id desc limit 1), '') not in ('confirmed_fit', 'demo_fit') then
        update public.initial_outreach_jobs set state = 'failed',
            failure_reason = coalesce(p_draft->>'error', 'The draft could not be generated or the company is no longer eligible.')
        where company_id = p_company;
        return false;
    end if;

    select m.thread_id into draft_thread
    from public.messages m join public.threads t on t.id = m.thread_id
    where t.company_id = p_company and m.initial_outreach->>'contact_id' = current_contact::text
      and m.status = 'draft'
    order by m.id desc limit 1 for update of m;
    if not found then
        insert into public.threads (company_id, test_recipient) values (p_company, p_test_recipient)
        returning id into draft_thread;
    end if;

    select coalesce(max(version), 0) + 1 into next_version
    from public.messages where thread_id = draft_thread and initial_outreach is not null;

    update public.messages set status = 'rejected', approved_version = null
    where thread_id = draft_thread and initial_outreach is not null and status = 'draft';

    insert into public.messages (thread_id, direction, subject, body, status, version, facts_used, initial_outreach)
    values (draft_thread, 'out', p_draft->>'subject', p_draft->>'body', 'draft', next_version,
            p_draft->'facts_used', p_draft->'metadata');
    update public.initial_outreach_jobs set state = 'ready', failure_reason = null where company_id = p_company;
    return true;
end;
$$;

commit;
