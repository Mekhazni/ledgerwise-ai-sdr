begin;

alter table public.company_reviews
    drop constraint company_reviews_decision_check,
    add constraint company_reviews_decision_check
        check (decision in ('confirmed_fit', 'demo_fit', 'rejected', 'needs_more_research'));

alter table public.threads add column demo_only boolean not null default false;

create function public.keep_demo_thread_test_only()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if old.demo_only then
            if new.company_id is distinct from old.company_id then
                raise exception 'A demo thread cannot change companies';
            end if;
            new.demo_only := true;
        end if;
        if new.company_id = old.company_id then
            return new;
        end if;
    end if;

    perform 1 from public.companies where id = new.company_id for update;
    if (select decision from public.company_reviews where company_id = new.company_id
        order by id desc limit 1) = 'demo_fit' then
        new.demo_only := true;
    end if;
    return new;
end;
$$;

create trigger keep_demo_thread_test_only
before insert or update on public.threads
for each row execute function public.keep_demo_thread_test_only();

create function public.mark_company_demo_threads()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    perform 1 from public.companies where id = new.company_id for update;
    update public.threads set demo_only = true where company_id = new.company_id and not demo_only;
    return new;
end;
$$;

create trigger mark_company_demo_threads
after insert on public.company_reviews
for each row when (new.decision = 'demo_fit')
execute function public.mark_company_demo_threads();

revoke all on function public.keep_demo_thread_test_only() from public, anon, authenticated;
revoke all on function public.mark_company_demo_threads() from public, anon, authenticated;

create or replace function public.reserve_tomba_lookup(p_company bigint, p_actor uuid)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    company_domain text;
    lookup_id bigint;
begin
    lock table public.tomba_lookups in share row exclusive mode;
    if not exists (select 1 from public.reviewers where user_id = p_actor
                   and active and role in ('admin', 'reviewer')) then
        return jsonb_build_object('status', 'forbidden');
    end if;
    select website into company_domain from public.companies where id = p_company;
    if not found then
        return jsonb_build_object('status', 'not_found');
    end if;
    if coalesce((select decision from public.company_reviews where company_id = p_company
        order by id desc limit 1), '') not in ('confirmed_fit', 'demo_fit') then
        return jsonb_build_object('status', 'fit_required');
    end if;
    if exists (select 1 from public.tomba_lookups where domain = company_domain) then
        return jsonb_build_object('status', 'existing');
    end if;
    if (select count(*) from public.tomba_lookups) >= 23 then
        return jsonb_build_object('status', 'limit');
    end if;
    insert into public.tomba_lookups (company_id, domain, requested_by)
    values (p_company, company_domain, p_actor) returning id into lookup_id;
    return jsonb_build_object('status', 'reserved', 'id', lookup_id, 'domain', company_domain);
end;
$$;

commit;
