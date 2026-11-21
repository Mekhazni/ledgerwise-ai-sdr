begin;

create or replace function public.reserve_calcom_booking(p_incoming bigint,p_token uuid,p_offer bigint,p_slot jsonb,p_phone text,
    p_email text,p_name text)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    conversation bigint;
    company bigint;
begin
    select t.id,t.company_id into conversation,company from public.messages m join public.threads t on t.id=m.thread_id
    where m.id=p_incoming;
    perform 1 from public.companies where id=company for update;
    if not public.reply_context_allowed(p_incoming)
       or (p_phone is not null and p_phone !~ '^[+][1-9][0-9]{7,14}$')
       or nullif(btrim(p_name),'') is null or nullif(btrim(p_email),'') is null
       or coalesce((p_slot->>'start')::timestamptz,now())<=now()
       or not exists(select 1 from public.reply_decisions where incoming_id=p_incoming and token=p_token and state='generating')
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
      'at',now(),'offer_id',p_offer,'slot',p_slot,'phone',p_phone,'email',p_email,'contact_name',p_name,
      'contact_id',(select primary_contact_id from public.companies where id=company)) where id=p_incoming;
    return true;
end;
$$;

commit;
