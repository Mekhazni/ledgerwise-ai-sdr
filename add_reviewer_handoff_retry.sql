begin;

create function public.reopen_mistaken_ai_handoff(p_incoming bigint, p_actor uuid, p_expected jsonb)
returns boolean language plpgsql security definer set search_path = ''
as $$
declare
    target_thread bigint;
    target_company bigint;
    actor_name text;
begin
    select full_name into actor_name from public.reviewers
    where user_id = p_actor and active and role in ('admin','reviewer');
    if not found then return false; end if;
    if p_expected->>'action' is distinct from 'human_assistance'
       or coalesce(p_expected->>'intent','') not in ('human_presence_inquiry','specific_time') then
        return false;
    end if;

    select t.id, t.company_id into target_thread, target_company
    from public.messages m join public.threads t on t.id = m.thread_id
    where m.id = p_incoming and m.direction = 'in';
    if not found then return false; end if;
    perform 1 from public.companies where id = target_company for update;

    begin
        update public.threads t set status = 'ai_handles', handoff_reason = null
        where t.id = target_thread and t.status = 'needs_human' and t.owner is null
          and t.handoff_reason = p_expected->>'reason'
          and exists (
              select 1 from public.reply_decisions d
              where d.incoming_id = p_incoming and d.state = 'ready' and d.decision = p_expected
          )
          and not exists (
              select 1 from public.messages m where m.reply_to_id = p_incoming and m.status in ('approved','sent')
          );
        if not found then return false; end if;
        if not public.reply_context_allowed(p_incoming) then
            raise exception 'Conversation no longer eligible for AI';
        end if;
        update public.messages set review_events = review_events || jsonb_build_object(
            'action','handoff_corrected','name',actor_name,'actor',p_actor,'at',now(),
            'reason','Reviewer confirmed a recognized AI classification error','previous_decision',p_expected
        ) where id = p_incoming;
        update public.reply_decisions set state = 'failed', error = 'Reviewer reopened the mistaken handoff. Retry AI reply.'
        where incoming_id = p_incoming;
    exception when raise_exception then
        return false;
    end;
    return true;
end;
$$;

revoke all on function public.reopen_mistaken_ai_handoff(bigint,uuid,jsonb) from public,anon,authenticated,service_role;
grant execute on function public.reopen_mistaken_ai_handoff(bigint,uuid,jsonb) to service_role;

commit;
