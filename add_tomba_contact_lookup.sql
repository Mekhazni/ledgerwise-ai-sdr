begin;

alter table public.company_contacts
    drop constraint company_contacts_publication_status_check,
    drop constraint company_contacts_sources_check,
    add column provider_status text,
    add column provider_confidence integer check (provider_confidence between 0 and 100),
    add constraint company_contacts_publication_status_check
        check (publication_status in ('published', 'provider_reported')),
    add constraint company_contacts_sources_check
        check (jsonb_typeof(sources) = 'array'
            and (publication_status = 'provider_reported' or jsonb_array_length(sources) > 0));

create table public.tomba_lookups (
    id bigint generated always as identity primary key,
    company_id bigint not null references public.companies (id),
    domain text not null unique,
    requested_by uuid references auth.users (id) on delete set null,
    requested_at timestamptz not null default now(),
    status text not null default 'reserved'
        check (status in ('reserved', 'completed', 'no_results', 'failed', 'uncertain')),
    preferred_contact_id bigint references public.company_contacts (id) on delete set null
);

alter table public.tomba_lookups enable row level security;
revoke all on public.tomba_lookups from public, anon, authenticated, service_role;
grant select on public.tomba_lookups to service_role;

create function public.reserve_tomba_lookup(p_company bigint, p_actor uuid)
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
    if (select decision from public.company_reviews where company_id = p_company
        order by id desc limit 1) is distinct from 'confirmed_fit' then
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

create function public.finish_tomba_lookup(p_lookup bigint, p_status text, p_contacts jsonb, p_preferred text)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    target_company bigint;
    preferred_id bigint;
begin
    select company_id into target_company from public.tomba_lookups
    where id = p_lookup and status = 'reserved' for update;
    if not found then
        raise exception 'Lookup is no longer reserved';
    end if;
    if p_status not in ('completed', 'no_results', 'failed', 'uncertain') then
        raise exception 'Invalid lookup status';
    end if;
    if p_status = 'completed' then
        insert into public.company_contacts (
            company_id, email, name, role, contact_type, sources, publication_status,
            delivery_status, provider_status, provider_confidence
        )
        select target_company, contact.email, contact.name, contact.role, contact.contact_type,
            contact.sources, 'provider_reported', contact.delivery_status,
            contact.provider_status, contact.provider_confidence
        from jsonb_to_recordset(p_contacts) as contact(
            email text, name text, role text, contact_type text, sources jsonb,
            delivery_status text, provider_status text, provider_confidence integer
        )
        on conflict (company_id, (lower(email))) do nothing;

        select id into preferred_id from public.company_contacts where company_id = target_company
            and lower(email) = lower(p_preferred) and publication_status = 'provider_reported'
            and provider_status = 'valid' and provider_confidence >= 90
            and jsonb_array_length(sources) > 0
            and contact_type in ('finance', 'founder', 'coo', 'general_inbox');
    end if;
    update public.tomba_lookups set status = p_status, preferred_contact_id = preferred_id
    where id = p_lookup;
end;
$$;

revoke all on function public.reserve_tomba_lookup(bigint, uuid)
from public, anon, authenticated, service_role;
grant execute on function public.reserve_tomba_lookup(bigint, uuid) to service_role;
revoke all on function public.finish_tomba_lookup(bigint, text, jsonb, text)
from public, anon, authenticated, service_role;
grant execute on function public.finish_tomba_lookup(bigint, text, jsonb, text) to service_role;

create or replace function public.save_researched_company(p_company jsonb, p_reasons jsonb)
returns text
language plpgsql
set search_path = ''
as $$
declare
    new_company_id bigint;
begin
    if exists (
        select 1 from unnest(array['location', 'ecommerce', 'revenue', 'headcount']) as required(criterion)
        where coalesce(p_company->'research_summary'->'score_breakdown'->required.criterion->>'evidence', '')
            not in ('verified', 'estimate', 'mismatch', 'estimate_outside_target')
    ) then
        raise exception 'Research must cover all four mandatory criteria';
    end if;

    insert into public.companies (
        name, website, score, qualification_status, contact_name, contact_role, contact_email,
        missing_information, research_errors, research_summary
    )
    values (
        p_company->>'name', p_company->>'website', (p_company->>'score')::integer,
        p_company->>'qualification_status', p_company->>'contact_name', p_company->>'contact_role', p_company->>'contact_email',
        array(select jsonb_array_elements_text(p_company->'missing_information')),
        p_company->'research_errors', nullif(p_company->'research_summary', 'null'::jsonb)
    )
    on conflict (website) do nothing
    returning id into new_company_id;

    if new_company_id is null then
        return 'existing';
    end if;

    insert into public.reasons (company_id, criterion, finding, points, source_url, quote, evidence_status)
    select new_company_id, reason.criterion, reason.finding, reason.points,
        reason.source_url, reason.quote, reason.evidence_status
    from jsonb_to_recordset(p_reasons) as reason(
        criterion text, finding text, points integer, source_url text, quote text, evidence_status text
    );

    insert into public.company_contacts (company_id, email, name, role, contact_type, sources)
    select new_company_id, contact.email, contact.name, contact.role, contact.contact_type, contact.sources
    from jsonb_to_recordset(coalesce(p_company->'contacts', '[]'::jsonb)) as contact(
        email text, name text, role text, contact_type text, sources jsonb
    );

    return 'saved';
end;
$$;

revoke all on function public.save_researched_company(jsonb, jsonb)
from public, anon, authenticated, service_role;
grant execute on function public.save_researched_company(jsonb, jsonb) to service_role;

commit;
