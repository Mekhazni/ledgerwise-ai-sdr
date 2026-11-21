begin;

create table public.pending_invitations (
    id uuid primary key default gen_random_uuid(),
    user_id uuid unique references auth.users (id) on delete set null,
    email text not null unique check (email = lower(btrim(email)) and email <> ''),
    full_name text not null check (btrim(full_name) <> ''),
    phone text,
    role text not null check (role in ('admin', 'reviewer')),
    status text not null check (status in ('sending', 'awaiting', 'failed', 'cancelled', 'accepted')),
    last_attempt_at timestamptz not null,
    expires_at timestamptz
);

alter table public.pending_invitations enable row level security;
revoke all on public.pending_invitations from public, anon, authenticated, service_role;
grant select, insert, update on public.pending_invitations to service_role;

alter table public.threads drop constraint threads_owner_fkey;
alter table public.threads add constraint threads_owner_fkey
    foreign key (owner) references auth.users (id) on delete set null;

create function public.complete_employee_invitation(p_user uuid)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    invitation public.pending_invitations%rowtype;
begin
    lock table public.reviewers in share row exclusive mode;
    select * into invitation from public.pending_invitations
    where user_id = p_user for update;
    if not found then
        return 'not_found';
    end if;
    if invitation.status = 'accepted' then
        return 'already_accepted';
    end if;
    if invitation.status <> 'awaiting' or invitation.expires_at is null
        or invitation.expires_at <= clock_timestamp() then
        return 'unavailable';
    end if;
    if not exists (
        select 1 from auth.users
        where id = p_user and lower(email) = invitation.email
            and email_confirmed_at is not null and nullif(encrypted_password, '') is not null
    ) then
        return 'password_required';
    end if;
    insert into public.reviewers (user_id, full_name, phone, role, active)
    values (p_user, invitation.full_name, invitation.phone, invitation.role, true)
    on conflict (user_id) do nothing;
    update public.pending_invitations set status = 'accepted' where id = invitation.id;
    return 'accepted';
end;
$$;

create function public.delete_employee(p_actor uuid, p_user uuid, p_email text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    employee public.reviewers%rowtype;
    employee_email text;
begin
    -- Match the lock used by update_employee.
    lock table public.reviewers in share row exclusive mode;
    if not exists (
        select 1 from public.reviewers where user_id = p_actor and role = 'admin' and active
    ) then
        return 'forbidden';
    end if;
    select * into employee from public.reviewers where user_id = p_user;
    if not found then
        return 'not_found';
    end if;
    select email into employee_email from auth.users where id = p_user for update;
    if not found or employee_email is null then
        return 'not_found';
    end if;
    if lower(employee_email) is distinct from lower(btrim(p_email)) then
        return 'email_mismatch';
    end if;
    if employee.role = 'admin' and employee.active and not exists (
        select 1 from public.reviewers as other
        join auth.users as account on account.id = other.user_id
        where other.user_id <> p_user and other.role = 'admin' and other.active
            and account.email_confirmed_at is not null
            and nullif(account.encrypted_password, '') is not null
            and (account.banned_until is null or account.banned_until <= now())
    ) then
        return 'last_admin';
    end if;
    delete from public.pending_invitations where user_id = p_user;
    delete from auth.users where id = p_user;
    return 'deleted';
end;
$$;

revoke all on function public.complete_employee_invitation(uuid)
from public, anon, authenticated, service_role;
grant execute on function public.complete_employee_invitation(uuid) to service_role;
revoke all on function public.delete_employee(uuid, uuid, text)
from public, anon, authenticated, service_role;
grant execute on function public.delete_employee(uuid, uuid, text) to service_role;

commit;
