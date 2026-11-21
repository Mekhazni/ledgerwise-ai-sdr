begin;

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

commit;
