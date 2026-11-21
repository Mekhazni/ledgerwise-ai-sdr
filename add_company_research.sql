begin;

alter table public.companies
    add column qualification_status text check (
        qualification_status in ('needs_more_research', 'poor_fit',
            'fits_profile_no_recent_signal_found', 'qualified_for_review')
    ),
    add column missing_information text[] not null default '{}',
    add column research_errors jsonb not null default '[]'::jsonb
        check (jsonb_typeof(research_errors) = 'array');

alter table public.reasons drop constraint reasons_criterion_check;
alter table public.reasons add constraint reasons_criterion_check
    check (criterion in ('hiring', 'funding', 'new_channel', 'location', 'size', 'ecommerce', 'contact'));

create function public.save_researched_company(p_company jsonb, p_reasons jsonb)
returns text
language plpgsql
set search_path = ''
as $$
declare
    new_company_id bigint;
begin
    insert into public.companies (
        name, website, score, qualification_status, contact_name, contact_role,
        missing_information, research_errors
    )
    values (
        p_company->>'name', p_company->>'website', (p_company->>'score')::integer,
        p_company->>'qualification_status', p_company->>'contact_name', p_company->>'contact_role',
        array(select jsonb_array_elements_text(p_company->'missing_information')),
        p_company->'research_errors'
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

    return 'saved';
end;
$$;

revoke all on function public.save_researched_company(jsonb, jsonb)
from public, anon, authenticated, service_role;
grant execute on function public.save_researched_company(jsonb, jsonb) to service_role;

commit;
