begin;

create table public.company_contacts (
    id bigint generated always as identity primary key,
    company_id bigint not null references public.companies (id),
    email text not null check (btrim(email) <> ''),
    name text,
    role text,
    contact_type text not null check (contact_type in ('finance', 'founder', 'coo', 'general_inbox', 'needs_review')),
    sources jsonb not null check (jsonb_typeof(sources) = 'array' and jsonb_array_length(sources) > 0),
    publication_status text not null default 'published' check (publication_status = 'published'),
    delivery_status text not null default 'unverified'
        check (delivery_status in ('unverified', 'verified', 'undeliverable')),
    check (contact_type not in ('finance', 'founder', 'coo') or (name is not null and role is not null))
);

create unique index company_contacts_company_email_idx
    on public.company_contacts (company_id, lower(email));

alter table public.company_contacts enable row level security;
revoke all on public.company_contacts from public, anon, authenticated, service_role;
grant select, insert on public.company_contacts to service_role;

create or replace function public.save_researched_company(p_company jsonb, p_reasons jsonb)
returns text
language plpgsql
set search_path = ''
as $$
declare
    new_company_id bigint;
begin
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
