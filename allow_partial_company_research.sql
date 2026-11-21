begin;

alter table public.companies drop constraint companies_qualification_status_check;
alter table public.companies add constraint companies_qualification_status_check
    check (qualification_status in ('needs_more_research', 'poor_fit', 'potential_fit',
        'fits_profile_no_recent_signal_found', 'qualified_for_review'));

create or replace function public.save_researched_company(p_company jsonb, p_reasons jsonb)
returns text
language plpgsql
set search_path = ''
as $$
declare
    new_company_id bigint;
begin
    if p_company->'research_summary'->'score_breakdown'->'ecommerce'->>'evidence'
        is distinct from 'verified' or not exists (
            select 1 from jsonb_array_elements(p_reasons) as reason
            where reason->>'criterion' = 'ecommerce' and (reason->>'points')::integer = 25
              and reason->>'evidence_status' = 'verified'
              and btrim(coalesce(reason->>'source_url', '')) <> ''
              and btrim(coalesce(reason->>'quote', '')) <> ''
        ) then
        raise exception 'Research must include evidence of online product sales';
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
