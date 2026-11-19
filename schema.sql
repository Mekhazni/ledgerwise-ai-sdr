begin;

create table public.companies (
    id bigint generated always as identity primary key,
    name text not null,
    website text not null unique
        check (website ~ '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$'
            and website not like 'www.%'),
    score integer not null default 0,
    status text not null default 'new'
        check (status in ('new', 'qualified', 'contacted', 'replied', 'booked', 'opted_out')),
    contact_name text,
    contact_role text,
    contact_email text,
    created_at timestamptz not null default now()
);

create table public.reasons (
    id bigint generated always as identity primary key,
    company_id bigint not null references public.companies (id),
    criterion text not null
        check (criterion in ('hiring', 'funding', 'new_channel', 'location', 'size')),
    finding text not null,
    points integer not null default 0,
    source_url text,
    quote text,
    evidence_status text not null
        check (evidence_status in ('verified', 'estimate', 'unknown')),
    created_at timestamptz not null default now(),
    check (evidence_status <> 'unknown' or points = 0),
    check (
        (evidence_status = 'unknown' and source_url is null and quote is null)
        or (
            source_url is not null and btrim(source_url) <> ''
            and quote is not null and btrim(quote) <> ''
        )
    )
);

create table public.threads (
    id bigint generated always as identity primary key,
    company_id bigint not null references public.companies (id),
    test_recipient text not null,
    status text not null default 'ai_handles'
        check (status in ('ai_handles', 'needs_human', 'human_took_over')),
    owner uuid references auth.users (id),
    handoff_reason text,
    gmail_thread_id text unique,
    created_at timestamptz not null default now(),
    check (status <> 'ai_handles' or owner is null)
);

create table public.messages (
    id bigint generated always as identity primary key,
    thread_id bigint not null references public.threads (id),
    direction text not null check (direction in ('in', 'out')),
    subject text not null,
    body text not null,
    status text not null check (
        (direction = 'in' and status = 'received')
        or (direction = 'out' and status in ('draft', 'approved', 'sent', 'rejected'))
    ),
    version integer not null default 1 check (version > 0),
    approved_version integer check (approved_version = version),
    facts_used jsonb not null default '[]'::jsonb
        check (jsonb_typeof(facts_used) = 'array'),
    offered_slots jsonb not null default '[]'::jsonb
        check (jsonb_typeof(offered_slots) = 'array'),
    gmail_message_id text unique,
    created_at timestamptz not null default now(),
    check (direction <> 'out' or status not in ('approved', 'sent') or approved_version is not null)
);

create table public.suppressions (
    id bigint generated always as identity primary key,
    email text not null unique check (email <> '' and email = lower(btrim(email))),
    reason text not null,
    trigger_message_id bigint references public.messages (id),
    created_at timestamptz not null default now()
);

create table public.bookings (
    id bigint generated always as identity primary key,
    thread_id bigint not null references public.threads (id),
    start_time timestamptz not null,
    calcom_booking_id text unique,
    status text not null check (status in ('booked', 'cancelled')),
    acceptance_message_id bigint not null unique references public.messages (id),
    offer_message_id bigint not null references public.messages (id),
    created_at timestamptz not null default now()
);

alter table public.companies enable row level security;
alter table public.reasons enable row level security;
alter table public.threads enable row level security;
alter table public.messages enable row level security;
alter table public.suppressions enable row level security;
alter table public.bookings enable row level security;

grant select, insert, update, delete on table
    public.companies,
    public.reasons,
    public.threads,
    public.messages,
    public.suppressions,
    public.bookings
to service_role;

commit;
