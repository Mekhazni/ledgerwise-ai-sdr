begin;

create or replace function public.import_gmail_reply(p_thread bigint, p_gmail_thread text, p_reply jsonb)
returns text language plpgsql security definer set search_path = ''
as $$
declare
    target_company bigint;
    new_reply_id bigint;
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
    on conflict (gmail_message_id) do nothing returning id into new_reply_id;
    if not found then return 'duplicate'; end if;

    update public.messages d set status='rejected',
        review_events=d.review_events || jsonb_build_object('action','stale_after_incoming','at',now())
    from public.messages previous, public.messages latest
    where d.reply_to_id=previous.id and latest.id=new_reply_id and d.thread_id=p_thread and d.status='draft'
      and ((coalesce(previous.received_at,previous.created_at),previous.id) <
           (coalesce(latest.received_at,latest.created_at),latest.id) or p_reply->'opt_out'='true'::jsonb);

    if p_reply->'opt_out' = 'true'::jsonb then
        insert into public.suppressions(email, reason, trigger_message_id)
        select distinct lower(btrim(address)), 'Clear opt-out in a Gmail reply', new_reply_id
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
