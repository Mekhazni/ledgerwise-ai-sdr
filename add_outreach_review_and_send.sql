begin;

alter table public.messages
    add column review_events jsonb not null default '[]'::jsonb check (jsonb_typeof(review_events) = 'array'),
    add column send_state text check (send_state in ('sending', 'sent', 'failed', 'unknown')),
    add column actual_recipient text,
    add column send_error text,
    add column sent_at timestamptz;

-- Distinct initial subjects reduce Gmail grouping when every company shares one inbox.
create unique index outreach_initial_send_subject_idx on public.messages (lower(btrim(subject)))
where initial_outreach is not null and send_state in ('sending', 'sent', 'unknown');

create function public.review_initial_message(
    p_message bigint, p_version integer, p_actor uuid, p_action text,
    p_subject text default null, p_body text default null, p_recipient text default null
)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
    msg public.messages%rowtype;
    conversation public.threads%rowtype;
    company public.companies%rowtype;
    actor_name text;
    event jsonb;
begin
    select full_name into actor_name from public.reviewers
    where user_id = p_actor and active and role in ('admin', 'reviewer');
    if not found then return jsonb_build_object('status', 'forbidden'); end if;

    select t.* into conversation from public.threads t join public.messages m on m.thread_id = t.id
    where m.id = p_message;
    if not found then return jsonb_build_object('status', 'stale'); end if;
    select * into company from public.companies where id = conversation.company_id for update;
    select * into msg from public.messages where id = p_message for update;
    if not found or msg.version <> p_version or msg.direction <> 'out'
       or msg.initial_outreach is null or msg.status <> 'draft'
       or exists (select 1 from public.messages where thread_id = msg.thread_id and initial_outreach is not null
                  and (version, id) > (msg.version, msg.id))
       or exists (select 1 from public.messages m join public.threads t on t.id = m.thread_id
                  where t.company_id = company.id and m.initial_outreach is not null and m.status in ('approved', 'sent'))
       or exists (select 1 from public.initial_outreach_jobs where company_id = company.id
                  and state = 'generating' and started_at > now() - interval '3 minutes') then
        return jsonb_build_object('status', 'stale');
    end if;
    if msg.initial_outreach->>'contact_id' is distinct from company.primary_contact_id::text
       or coalesce((select decision from public.company_reviews where company_id = company.id
           order by id desc limit 1), '') not in ('confirmed_fit', 'demo_fit') then
        return jsonb_build_object('status', 'ineligible');
    end if;
    event := jsonb_build_object('action', p_action, 'actor', p_actor, 'name', actor_name,
                               'at', now(), 'version', msg.version);
    if p_action = 'reject' then
        update public.messages set status = 'rejected', review_events = review_events || event where id = msg.id;
        return jsonb_build_object('status', 'rejected');
    elsif p_action = 'edit' then
        if p_subject is null or p_body is null then return jsonb_build_object('status', 'invalid'); end if;
        update public.messages set status = 'rejected', review_events = review_events ||
            jsonb_build_object('action', 'superseded', 'actor', p_actor, 'name', actor_name, 'at', now())
        where id = msg.id;
        insert into public.messages(thread_id,direction,subject,body,status,version,facts_used,initial_outreach,review_events)
        values (msg.thread_id,'out',p_subject,p_body,'draft',msg.version+1,msg.facts_used,
                msg.initial_outreach || jsonb_build_object('edited_by',p_actor,'edited_by_name',actor_name,'edited_at',now()),
                jsonb_build_array(event || jsonb_build_object('version',msg.version+1,'previous_message_id',msg.id)));
        return jsonb_build_object('status', 'edited');
    elsif p_action = 'approve' then
        if p_recipient is null or p_recipient = '' or exists (
            select 1 from public.suppressions
            where email in (lower(p_recipient), lower(msg.initial_outreach->>'business_email'))
        ) then return jsonb_build_object('status', 'suppressed'); end if;
        begin
            update public.messages set status = 'approved', approved_version = version, send_state = 'sending',
                actual_recipient = p_recipient, send_error = null, review_events = review_events || event
            where id = msg.id returning * into msg;
        exception when unique_violation then
            return jsonb_build_object('status', 'subject_in_use');
        end;
        return jsonb_build_object('status','claimed','message',to_jsonb(msg),'thread',to_jsonb(conversation));
    end if;
    return jsonb_build_object('status', 'invalid');
end;
$$;

create function public.record_initial_send(
    p_message bigint, p_version integer, p_outcome text, p_error text,
    p_gmail_id text default null, p_gmail_thread text default null
)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    target_thread bigint;
begin
    if p_outcome not in ('sent','failed','unknown') then return false; end if;
    if p_outcome = 'sent' and (nullif(p_gmail_id, '') is null or nullif(p_gmail_thread, '') is null) then return false; end if;
    update public.messages set
        status = case p_outcome when 'sent' then 'sent' when 'failed' then 'draft' else 'approved' end,
        approved_version = case when p_outcome = 'failed' then null else approved_version end,
        send_state = p_outcome, send_error = p_error,
        gmail_message_id = case when p_outcome = 'sent' then p_gmail_id else null end,
        sent_at = case when p_outcome = 'sent' then now() else null end,
        review_events = review_events || jsonb_build_object('action',p_outcome,'version',version,'at',now(),
                                                          'recipient',actual_recipient,'error',p_error)
    where id = p_message and version = p_version and approved_version = p_version
      and status = 'approved' and send_state = 'sending'
    returning thread_id into target_thread;
    if not found then return false; end if;
    if p_outcome = 'sent' then
        update public.threads set gmail_thread_id = p_gmail_thread where id = target_thread;
    end if;
    return true;
end;
$$;

revoke all on function public.review_initial_message(bigint,integer,uuid,text,text,text,text),
    public.record_initial_send(bigint,integer,text,text,text,text) from public, anon, authenticated, service_role;
grant execute on function public.review_initial_message(bigint,integer,uuid,text,text,text,text),
    public.record_initial_send(bigint,integer,text,text,text,text) to service_role;

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
        where m.id = p_message and t.company_id = p_company and m.status in ('draft', 'rejected')
          and not exists (select 1 from public.messages newer where newer.thread_id = m.thread_id
               and newer.initial_outreach is not null and (newer.version, newer.id) > (m.version, m.id))
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
      and m.status in ('draft', 'rejected')
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

create or replace function public.start_initial_outreach(
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
    if exists (
        select 1 from public.messages m join public.threads t on t.id = m.thread_id
        where t.company_id = p_company and m.initial_outreach is not null and m.status = 'approved'
    ) then return jsonb_build_object('status', 'stale'); end if;
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

commit;
