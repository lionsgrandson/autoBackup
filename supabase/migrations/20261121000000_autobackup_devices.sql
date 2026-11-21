-- Server-managed device assignments. Only the service role uses this table.
create table if not exists public.autobackup_devices (
  id uuid primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  device_name text not null check (char_length(device_name) between 1 and 80),
  active boolean not null default true,
  created_at timestamptz not null default now()
);
create index if not exists autobackup_devices_owner_idx
  on public.autobackup_devices(user_id);
alter table public.autobackup_devices enable row level security;
revoke all privileges on table public.autobackup_devices from public, anon, authenticated;
-- No anon/authenticated RLS policy: only service_role is allowed.
