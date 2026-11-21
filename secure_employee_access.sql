begin;

revoke all on public.reviewers from public, anon, authenticated, service_role;
drop policy if exists "Read own reviewer access" on public.reviewers;
grant select, insert on public.reviewers to service_role;

revoke all on public.companies, public.reasons, public.threads,
    public.messages, public.suppressions, public.bookings
from public, anon, authenticated;

create function public.update_employee(
    p_actor uuid, p_user uuid, p_name text, p_phone text,
    p_role text, p_active boolean, p_confirm_deactivation boolean
) returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    employee public.reviewers%rowtype;
begin
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
    if employee.active and not p_active and p_confirm_deactivation is not true then
        return 'confirm_deactivation';
    end if;
    if employee.active and employee.role = 'admin' and (not p_active or p_role <> 'admin')
        and not exists (
            select 1
            from public.reviewers as other
            join auth.users as account on account.id = other.user_id
            where other.user_id <> p_user and other.role = 'admin' and other.active
                and account.email_confirmed_at is not null
                and nullif(account.encrypted_password, '') is not null
        ) then
        return 'last_admin';
    end if;
    update public.reviewers
    set full_name = p_name, phone = p_phone, role = p_role, active = p_active
    where user_id = p_user;
    return 'saved';
end;
$$;

revoke all on function public.update_employee(uuid, uuid, text, text, text, boolean, boolean)
from public, anon, authenticated, service_role;
grant execute on function public.update_employee(uuid, uuid, text, text, text, boolean, boolean)
to service_role;

commit;
