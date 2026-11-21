begin;

alter table public.companies
    add column primary_contact_id bigint references public.company_contacts (id) deferrable initially deferred,
    add column backup_contact_id bigint references public.company_contacts (id) deferrable initially deferred,
    add constraint distinct_outreach_contacts check (
        backup_contact_id is null or (primary_contact_id is not null and primary_contact_id <> backup_contact_id)
    );

create function public.save_outreach_contacts(p_company bigint, p_actor uuid, p_primary bigint, p_backup bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not exists (select 1 from public.reviewers where user_id = p_actor
                   and active and role in ('admin', 'reviewer')) then
        return 'forbidden';
    end if;

    perform 1 from public.companies where id = p_company for update;
    if not found then
        return 'not_found';
    end if;
    if coalesce((select decision from public.company_reviews where company_id = p_company
        order by id desc limit 1), '') not in ('confirmed_fit', 'demo_fit') then
        return 'fit_required';
    end if;

    if p_backup is not null and (p_primary is null or p_backup = p_primary) then
        return 'invalid_contact';
    end if;
    if exists (
        select 1 from unnest(array[p_primary, p_backup]) as selected(contact_id)
        where selected.contact_id is not null and not exists (
            select 1 from public.company_contacts c
            where c.id = selected.contact_id and c.company_id = p_company
              and c.delivery_status is distinct from 'undeliverable'
              and c.provider_status is distinct from 'invalid'
        )
    ) then
        return 'invalid_contact';
    end if;

    update public.companies set primary_contact_id = p_primary, backup_contact_id = p_backup
    where id = p_company;
    return 'saved';
end;
$$;

revoke all on function public.save_outreach_contacts(bigint, uuid, bigint, bigint)
from public, anon, authenticated, service_role;
grant execute on function public.save_outreach_contacts(bigint, uuid, bigint, bigint) to service_role;

commit;
