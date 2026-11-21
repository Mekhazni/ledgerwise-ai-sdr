begin;

alter table public.company_reviews
    drop constraint company_reviews_notes_check,
    alter column criteria drop not null,
    add constraint company_reviews_short_note_check check (char_length(notes) <= 500) not valid;

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
        missing_information, research_errors
    )
    values (
        p_company->>'name', p_company->>'website', (p_company->>'score')::integer,
        p_company->>'qualification_status', p_company->>'contact_name', p_company->>'contact_role', p_company->>'contact_email',
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
