begin;

create or replace function public.reserve_calcom_booking(p_incoming bigint,p_token uuid,p_offer bigint,p_slot jsonb,p_phone text,
    p_email text,p_name text,p_actor uuid)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    conversation bigint;
    company bigint;
begin
    select t.id,t.company_id into conversation,company from public.messages m join public.threads t on t.id=m.thread_id
    where m.id=p_incoming;
    perform 1 from public.companies where id=company for update;
    if (p_actor is null and (not public.reply_context_allowed(p_incoming)
          or not exists(select 1 from public.reply_decisions where incoming_id=p_incoming and token=p_token and state='generating')))
       or (p_actor is not null and (not public.manual_reply_context_allowed(p_incoming,p_actor)
          or not exists(select 1 from public.reviewers where user_id=p_actor and active and role in ('admin','reviewer'))
          or exists(select 1 from public.messages where reply_to_id=p_incoming and status in ('draft','approved','sent'))))
       or (p_phone is not null and p_phone !~ '^[+][1-9][0-9]{7,14}$')
       or nullif(btrim(p_name),'') is null or nullif(btrim(p_email),'') is null
       or coalesce((p_slot->>'start')::timestamptz,now())<=now()
       or not exists(select 1 from public.messages m join public.threads t on t.id=m.thread_id
            where m.id=p_incoming and lower(m.inbound_email->>'sender')=p_email and lower(t.test_recipient)=p_email)
       or not exists(select 1 from public.messages where id=p_offer and thread_id=conversation and direction='out'
              and status='sent' and lower(actual_recipient)=p_email and offered_slots @> jsonb_build_array(p_slot))
       or exists(select 1 from public.messages newer
           join public.messages offered on offered.id=p_offer
           join public.messages incoming on incoming.id=p_incoming
           where newer.thread_id=conversation and newer.direction='out' and newer.status='sent'
             and jsonb_array_length(newer.offered_slots)>0
             and (newer.sent_at,newer.id)>(offered.sent_at,offered.id)
             and newer.sent_at<coalesce(incoming.received_at,incoming.created_at))
       or exists(select 1 from public.bookings where thread_id=conversation and status='booked')
       or exists(select 1 from public.messages where thread_id=conversation and review_events @> '[{"action":"booking_requested"}]') then
        return false;
    end if;
    update public.messages set review_events=review_events || jsonb_build_object('action','booking_requested',
      'at',now(),'actor',p_actor,'offer_id',p_offer,'slot',p_slot,'phone',p_phone,'email',p_email,'contact_name',p_name,
      'contact_id',(select primary_contact_id from public.companies where id=company)) where id=p_incoming;
    return true;
end;
$$;

create or replace function public.change_conversation_owner(p_thread bigint,p_actor uuid,p_action text)
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
    if exists(select 1 from public.messages m
              cross join lateral jsonb_array_elements(m.review_events) as event
              left join public.reply_decisions j on j.incoming_id=m.id
              where m.thread_id=p_thread and event->>'action'='booking_requested'
                and (event->>'at')::timestamptz > now()-interval '5 minutes'
                and (event->>'actor' is not null or j.state='generating')
                and not m.review_events @> '[{"action":"booking_confirmed"}]') then
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

create function public.compose_reviewer_scheduling(p_incoming bigint,p_actor uuid,p_result jsonb)
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
    insert into public.messages(thread_id,reply_to_id,direction,subject,body,status,version,facts_used,offered_slots,review_events)
    values(incoming.thread_id,p_incoming,'out',incoming.subject,p_result->>'body','draft',next_version,'[]',p_result->'offered_slots',
           jsonb_build_array(jsonb_build_object('action','composed','actor',p_actor,'name',actor_name,'at',now(),'decision',p_result-'body'-'facts'-'offered_slots')));
    return true;
end;
$$;


revoke all on function public.reserve_calcom_booking(bigint,uuid,bigint,jsonb,text,text,text,uuid),
    public.compose_reviewer_scheduling(bigint,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.reserve_calcom_booking(bigint,uuid,bigint,jsonb,text,text,text,uuid),
    public.compose_reviewer_scheduling(bigint,uuid,jsonb) to service_role;
commit;
