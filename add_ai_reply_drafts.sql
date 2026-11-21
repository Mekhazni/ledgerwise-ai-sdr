begin;

alter table public.messages add column reply_to_id bigint references public.messages(id);
create unique index reply_message_version_uidx on public.messages(reply_to_id,version) where reply_to_id is not null;
create table public.reply_decisions (
    incoming_id bigint primary key references public.messages(id) on delete cascade,
    state text not null check (state in ('generating','failed','ready')),
    token uuid not null default gen_random_uuid(),
    started_at timestamptz not null default now(),
    decision jsonb,
    error text
);
alter table public.reply_decisions enable row level security;
revoke all on public.reply_decisions from public, anon, authenticated, service_role;
grant select on public.reply_decisions to service_role;

create function public.reply_context_allowed(p_incoming bigint)
returns boolean language sql set search_path = ''
as $$
select exists (
    select 1 from public.messages m join public.threads t on t.id=m.thread_id
    join public.companies c on c.id=t.company_id
    where m.id=p_incoming and m.direction='in' and m.status='received'
      and t.status='ai_handles' and t.gmail_thread_id is not null and c.status<>'opted_out'
      and coalesce((select decision from public.company_reviews where company_id=c.id order by id desc limit 1),'')
          in ('confirmed_fit','demo_fit')
      and not exists (
          select 1 from public.messages newer where newer.thread_id=t.id and newer.direction='in'
            and (coalesce(newer.received_at,newer.created_at),newer.id) >
                (coalesce(m.received_at,m.created_at),m.id)
      )
      and not exists (
          select 1 from public.suppressions s where s.email=lower(m.inbound_email->>'sender')
          or s.email in (select lower(original.initial_outreach->>'business_email') from public.messages original
                         where original.thread_id=t.id and original.initial_outreach is not null and original.status='sent')
      )
)
$$;

create function public.claim_ai_reply(p_incoming bigint, p_mode text, p_version_id bigint)
returns jsonb language plpgsql security definer set search_path = ''
as $$
declare
    company bigint;
    job public.reply_decisions%rowtype;
begin
    select t.company_id into company from public.messages m join public.threads t on t.id=m.thread_id
    where m.id=p_incoming;
    perform 1 from public.companies where id=company for update;
    if not public.reply_context_allowed(p_incoming) then return jsonb_build_object('status','blocked'); end if;
    if exists (select 1 from public.messages where reply_to_id=p_incoming and status in ('approved','sent')) then
        return jsonb_build_object('status','blocked');
    end if;
    if p_mode='regenerate' and not exists (
        select 1 from public.messages m where m.id=p_version_id and m.reply_to_id=p_incoming
        and m.status in ('draft','rejected') and not exists (
            select 1 from public.messages newer where newer.reply_to_id=p_incoming and newer.version>m.version
        )
    ) then return jsonb_build_object('status','stale'); end if;
    if p_mode not in ('auto','retry','regenerate') then return jsonb_build_object('status','blocked'); end if;
    select * into job from public.reply_decisions where incoming_id=p_incoming;
    if found and (p_mode='auto' or (job.state='ready' and p_mode='retry')
        or (job.state='generating' and job.started_at>now()-interval '5 minutes')) then
        return jsonb_build_object('status','existing');
    end if;
    insert into public.reply_decisions(incoming_id,state) values(p_incoming,'generating')
    on conflict(incoming_id) do update set state='generating',token=gen_random_uuid(),started_at=now(),error=null
    returning * into job;
    return jsonb_build_object('status','generate','token',job.token,'company_id',company);
end;
$$;

create function public.finish_ai_reply(p_incoming bigint,p_token uuid,p_result jsonb,p_error text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    company bigint;
    incoming public.messages%rowtype;
    next_version integer;
begin
    select t.company_id into company from public.messages m join public.threads t on t.id=m.thread_id where m.id=p_incoming;
    perform 1 from public.companies where id=company for update;
    if not exists (select 1 from public.reply_decisions where incoming_id=p_incoming and token=p_token and state='generating')
    then return false; end if;
    if p_result is null or not public.reply_context_allowed(p_incoming) then
        update public.reply_decisions set state='failed',error=coalesce(p_error,'Conversation changed before drafting finished.')
        where incoming_id=p_incoming;
        return false;
    end if;
    select * into incoming from public.messages where id=p_incoming;
    if p_result->>'action'='suppress' then
        insert into public.suppressions(email,reason,trigger_message_id)
        select distinct lower(btrim(address)), 'Opt-out in an incoming reply', p_incoming
        from (
            select incoming.inbound_email->>'sender' as address
            union all select initial_outreach->>'business_email' from public.messages
              where thread_id=incoming.thread_id and status='sent' and initial_outreach is not null
        ) targets where nullif(btrim(address),'') is not null on conflict(email) do nothing;
        update public.companies set status='opted_out' where id=company;
    end if;
    if (p_result->>'needs_human')::boolean then
        update public.threads set status='needs_human',handoff_reason=p_result->>'reason' where id=incoming.thread_id;
    end if;
    select coalesce(max(version),0)+1 into next_version from public.messages where reply_to_id=p_incoming;
    update public.messages set status='rejected' where reply_to_id=p_incoming and status='draft';
    if nullif(p_result->>'body','') is not null then
        insert into public.messages(thread_id,reply_to_id,direction,subject,body,status,version,facts_used,review_events)
        values(incoming.thread_id,p_incoming,'out',incoming.subject,p_result->>'body','draft',next_version,
               coalesce(p_result->'facts','[]'::jsonb),jsonb_build_array(jsonb_build_object('action','generated','at',now(),'decision',p_result-'body'-'facts')));
    end if;
    update public.reply_decisions set state='ready',decision=p_result-'body'-'facts',error=null where incoming_id=p_incoming;
    return true;
end;
$$;

create function public.review_reply_message(
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
    incoming public.messages%rowtype;
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
       or msg.reply_to_id is null or msg.status <> 'draft'
       or exists (select 1 from public.messages where reply_to_id = msg.reply_to_id
                  and (version,id) > (msg.version,msg.id))
       or exists (select 1 from public.messages where reply_to_id = msg.reply_to_id and status in ('approved','sent'))
       or exists (select 1 from public.reply_decisions where incoming_id=msg.reply_to_id and state='generating') then
        return jsonb_build_object('status','stale');
    end if;
    select * into incoming from public.messages where id=msg.reply_to_id;
    if not public.reply_context_allowed(incoming.id) then
        return jsonb_build_object('status','ineligible');
    end if;
    if p_action='approve' and (lower(p_recipient) is distinct from lower(incoming.inbound_email->>'sender')
          or nullif(incoming.inbound_email->>'message_id','') is null or conversation.gmail_thread_id is null) then
        return jsonb_build_object('status','ineligible');
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
        insert into public.messages(thread_id,direction,subject,body,status,version,facts_used,reply_to_id,review_events)
        values (msg.thread_id,'out',msg.subject,p_body,'draft',msg.version+1,msg.facts_used,
                msg.reply_to_id,
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
        return jsonb_build_object('status','claimed','message',to_jsonb(msg),'thread',to_jsonb(conversation),'incoming',to_jsonb(incoming));
    end if;
    return jsonb_build_object('status', 'invalid');
end;
$$;


revoke all on function public.reply_context_allowed(bigint),public.claim_ai_reply(bigint,text,bigint),
    public.finish_ai_reply(bigint,uuid,jsonb,text),public.review_reply_message(bigint,integer,uuid,text,text,text,text)
    from public,anon,authenticated,service_role;
grant execute on function public.claim_ai_reply(bigint,text,bigint),public.finish_ai_reply(bigint,uuid,jsonb,text),
    public.review_reply_message(bigint,integer,uuid,text,text,text,text) to service_role;
create or replace function public.import_gmail_reply(p_thread bigint, p_gmail_thread text, p_reply jsonb)
returns text language plpgsql security definer set search_path = ''
as $$
declare
    target_company bigint;
    message_id bigint;
    original_contact text;
    test_address text;
begin
    select company_id into target_company from public.threads
    where id = p_thread and gmail_thread_id = p_gmail_thread;
    if not found then return 'unmatched'; end if;
    perform 1 from public.companies where id = target_company for update;

    select initial_outreach->>'business_email', actual_recipient
    into original_contact, test_address
    from public.messages where thread_id = p_thread and initial_outreach is not null and status = 'sent'
    order by sent_at, id limit 1;
    if not found or lower(test_address) is distinct from lower(p_reply->>'sender') then
        return 'unmatched';
    end if;

    insert into public.messages(thread_id, direction, subject, body, status, gmail_message_id, received_at, inbound_email)
    values (p_thread, 'in', coalesce(p_reply->>'subject', ''), p_reply->>'body', 'received',
            p_reply->>'gmail_message_id', (p_reply->>'received_at')::timestamptz,
            jsonb_build_object('sender', p_reply->>'sender', 'recipients', p_reply->'recipients',
                'message_id', p_reply->>'message_id', 'in_reply_to', p_reply->>'in_reply_to',
                'references', p_reply->>'references'))
    on conflict (gmail_message_id) do nothing returning id into message_id;
    if not found then return 'duplicate'; end if;

    update public.messages d set status='rejected',
        review_events=review_events || jsonb_build_object('action','stale_after_incoming','at',now())
    from public.messages previous, public.messages latest
    where d.reply_to_id=previous.id and latest.id=message_id and d.thread_id=p_thread and d.status='draft'
      and ((coalesce(previous.received_at,previous.created_at),previous.id) <
           (coalesce(latest.received_at,latest.created_at),latest.id) or p_reply->'opt_out'='true'::jsonb);

    if p_reply->'opt_out' = 'true'::jsonb then
        insert into public.suppressions(email, reason, trigger_message_id)
        select distinct lower(btrim(address)), 'Clear opt-out in a Gmail reply', message_id
        from unnest(array[original_contact, test_address]) as target(address)
        where nullif(btrim(address), '') is not null
        on conflict (email) do nothing;
        update public.companies set status = 'opted_out' where id = target_company;
        return 'suppressed';
    end if;
    return 'imported';
end;
$$;

commit;
