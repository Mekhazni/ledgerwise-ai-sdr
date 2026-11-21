begin;

create function public.refresh_initial_personalization(p_company bigint, p_actor uuid, p_message bigint)
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

    update public.initial_outreach_jobs set token = gen_random_uuid(), state = 'generating', started_at = now()
    where company_id = p_company and contact_id = primary_contact and state = 'ready'
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

    update public.messages m set status = 'rejected', approved_version = null
    from public.threads t where m.thread_id = t.id and t.company_id = p_company
        and m.initial_outreach is not null and m.status = 'draft';

    insert into public.threads (company_id, test_recipient) values (p_company, p_test_recipient)
    returning id into new_thread;
    insert into public.messages (thread_id, direction, subject, body, status, facts_used, initial_outreach)
    values (new_thread, 'out', p_draft->>'subject', p_draft->>'body', 'draft',
            p_draft->'facts_used', p_draft->'metadata');
    update public.initial_outreach_jobs set state = 'ready' where company_id = p_company;
    return true;
end;
$$;

commit;
