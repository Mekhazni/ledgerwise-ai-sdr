-- Run after schema.sql to add the sample only if its domain is absent.
-- Score reflects the evidence stored under the existing criterion list.
with sample as (
    insert into public.companies (name, website, score, status)
    values ('All In Food', 'allinfood.com', 25, 'new')
    on conflict (website) do nothing
    returning id
)
insert into public.reasons (company_id, criterion, finding, points, source_url, quote, evidence_status)
select id, 'location', 'All In Food has a corporate office in Los Angeles, California.', 25,
    'https://pitchbook.com/profiles/company/170773-84',
    'Corporate Office 1370 North Street Andrews Place Los Angeles, CA 90028 United States',
    'verified'
from sample
union all
select id, 'size', 'Annual revenue still needs a source.', 0, null, null, 'unknown'
from sample
union all
select id, 'size', 'Current headcount still needs confirmation.', 0, null, null, 'unknown'
from sample;
