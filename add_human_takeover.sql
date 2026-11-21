begin;

create function public.manual_reply_context_allowed(p_incoming bigint, p_actor uuid)
returns boolean language sql set search_path = ''
as $$
select exists (
    select 1 from public.messages m join public.threads t on t.id=m.thread_id
    join public.companies c on c.id=t.company_id
    where m.id=p_incoming and m.direction='in' and m.status='received'
      and t.status='human_took_over' and t.owner=p_actor and t.gmail_thread_id is not null and c.status<>'opted_out'
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

create or replace function public.review_reply_message(
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
    select * into conversation from public.threads where id=msg.thread_id;
    if not (public.reply_context_allowed(incoming.id)
            or public.manual_reply_context_allowed(incoming.id,p_actor)) then
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



create function public.change_conversation_owner(p_thread bigint,p_actor uuid,p_action text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    conversation public.threads%rowtype;
    company bigint;
    actor_name text;
    anchor bigint;
begin
    select full_name into actor_name from public.reviewers
      where user_id=p_actor and active and role in ('admin','reviewer');
    if not found then return false; end if;
    select company_id into company from public.threads where id=p_thread;
    perform 1 from public.companies where id=company and status<>'opted_out' for update;
    if not found then return false; end if;
    select * into conversation from public.threads where id=p_thread and gmail_thread_id is not null;
    if not found then return false; end if;
    select id into anchor from public.messages where thread_id=p_thread and direction='in'
      order by coalesce(received_at,created_at) desc,id desc limit 1;
    if exists(select 1 from public.messages m join public.reply_decisions j on j.incoming_id=m.id
              where m.thread_id=p_thread and m.review_events @> '[{"action":"booking_requested"}]'
              and j.state='generating' and j.started_at>now()-interval '5 minutes') then
        return false;
    end if;
    if p_action='takeover' and conversation.status in ('ai_handles','needs_human','human_took_over') and conversation.owner is null then
        update public.threads set status='human_took_over',owner=p_actor where id=p_thread;
        if anchor is not null and not public.manual_reply_context_allowed(anchor,p_actor) then
            raise exception 'Conversation is no longer eligible for takeover';
        end if;
    elsif p_action='return_to_ai' and conversation.status='human_took_over' and conversation.owner=p_actor then
        if anchor is not null and (not public.manual_reply_context_allowed(anchor,p_actor)
            or exists (select 1 from public.messages where reply_to_id=anchor and status in ('draft','approved'))
            or (conversation.handoff_reason is not null and not exists
                (select 1 from public.messages where reply_to_id=anchor and status='sent'))) then
            return false;
        end if;
        update public.threads set status='ai_handles',owner=null,handoff_reason=null where id=p_thread;
    else
        return false;
    end if;
    if anchor is null then
        select id into anchor from public.messages where thread_id=p_thread and direction='out' and status='sent'
          order by id desc limit 1;
    end if;
    if anchor is null then raise exception 'Conversation has no audit message'; end if;
    update public.messages set review_events=review_events || jsonb_build_object('action',p_action,
        'actor',p_actor,'name',actor_name,'at',now(),'reason',conversation.handoff_reason) where id=anchor;
    return true;
end;
$$;

create function public.compose_manual_reply(p_incoming bigint,p_actor uuid,p_body text,p_facts jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    incoming public.messages%rowtype;
    company bigint;
    actor_name text;
    next_version integer;
begin
    select full_name into actor_name from public.reviewers
      where user_id=p_actor and active and role in ('admin','reviewer');
    if not found then return false; end if;
    select * into incoming from public.messages where id=p_incoming;
    select company_id into company from public.threads where id=incoming.thread_id;
    perform 1 from public.companies where id=company for update;
    if not public.manual_reply_context_allowed(p_incoming,p_actor)
       or exists(select 1 from public.messages where reply_to_id=p_incoming and status in ('draft','approved','sent')) then
        return false;
    end if;
    select coalesce(max(version),0)+1 into next_version from public.messages where reply_to_id=p_incoming;
    insert into public.messages(thread_id,reply_to_id,direction,subject,body,status,version,facts_used,review_events)
    values(incoming.thread_id,p_incoming,'out',incoming.subject,p_body,'draft',next_version,p_facts,
           jsonb_build_array(jsonb_build_object('action','composed','actor',p_actor,'name',actor_name,'at',now())));
    return true;
end;
$$;

revoke all on function public.manual_reply_context_allowed(bigint,uuid),
    public.change_conversation_owner(bigint,uuid,text),public.compose_manual_reply(bigint,uuid,text,jsonb)
    from public,anon,authenticated;
grant execute on function public.change_conversation_owner(bigint,uuid,text),
    public.compose_manual_reply(bigint,uuid,text,jsonb) to service_role;
commit;
