-- 車帳本 Supabase 第一階段：資料表、權限、照片空間
-- 用法：Supabase 左邊選單 SQL Editor → New query → 整段貼上 → Run。可以重複執行。
-- 版本 2（2026-11-19）：編號改用文字（配合車帳本現有資料）。
--   如果跑過版本 1、而且雲端還沒有任何車，會先清掉舊表重建；已經有車就不動。

do $$
declare n int;
begin
  if (select data_type from information_schema.columns where table_schema='public' and table_name='vehicles' and column_name='id') = 'uuid' then
    execute 'select count(*) from public.vehicles' into n;
  end if;
  if n = 0 then
    drop table if exists public.photos, public.odometer_logs, public.expenses, public.maintenance_logs,
      public.charge_logs, public.fuel_logs, public.vehicle_members, public.vehicles cascade;
    drop function if exists public.is_member(uuid) cascade;
    drop function if exists public.is_owner(uuid) cascade;
  end if;
end $$;

-- ========== 1. 使用者資料 ==========
create table if not exists public.profiles (
  id          uuid primary key references auth.users(id) on delete cascade,
  nickname    text,
  plan        text not null default 'free' check (plan in ('free','starter','advanced')),
  plan_until  timestamptz,
  prefs       jsonb not null default '{}'::jsonb,   -- 語言、主題色、家用電價、自動帶入牌價…
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

-- 新使用者註冊時自動建一筆 profiles
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, nickname)
  values (new.id, coalesce(new.raw_user_meta_data->>'name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ========== 2. 車輛與成員 ==========
create table if not exists public.vehicles (
  id          text primary key,                       -- 手機產生
  owner_id    uuid not null references auth.users(id) on delete cascade default auth.uid(),
  data        jsonb not null default '{}'::jsonb,     -- 車牌、暱稱、廠牌、車型、燃料、油箱、電池、交車日…
  created_by  uuid references auth.users(id) on delete set null default auth.uid(),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  deleted_at  timestamptz
);

create table if not exists public.vehicle_members (
  vehicle_id  text not null references public.vehicles(id) on delete cascade,
  user_id     uuid not null references auth.users(id) on delete cascade,
  role        text not null check (role in ('owner','member')),
  created_at  timestamptz not null default now(),
  primary key (vehicle_id, user_id)
);
create index if not exists vehicle_members_user_idx on public.vehicle_members(user_id);

-- 判斷權限用的函式（security definer 避免權限規則互相遞迴）
create or replace function public.is_member(v text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from vehicle_members where vehicle_id = v and user_id = auth.uid());
$$;
create or replace function public.is_owner(v text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from vehicle_members where vehicle_id = v and user_id = auth.uid() and role = 'owner');
$$;

-- 建車時：檢查方案車數，並自動把建立的人設成車主
create or replace function public.vehicle_before_insert() returns trigger
language plpgsql security definer set search_path = public as $$
declare lim int; n int; p text;
begin
  if exists (select 1 from vehicles where id = new.id) then return new; end if;   -- 已有的車（同步時更新）不算新增
  new.owner_id := auth.uid();
  select coalesce(case when plan_until is null or plan_until > now() then plan end, 'free')
    into p from profiles where id = auth.uid();
  lim := case coalesce(p, 'free') when 'free' then 3 when 'starter' then 5 else null end;
  if lim is not null then
    select count(*) into n from vehicles where owner_id = auth.uid() and deleted_at is null;
    if n >= lim then
      raise exception 'VEHICLE_LIMIT:%', lim using hint = '目前方案的車輛數已滿';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists vehicles_before_insert on public.vehicles;
create trigger vehicles_before_insert before insert on public.vehicles
  for each row execute function public.vehicle_before_insert();

create or replace function public.vehicle_after_insert() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into vehicle_members (vehicle_id, user_id, role) values (new.id, new.owner_id, 'owner')
  on conflict do nothing;
  return new;
end $$;
drop trigger if exists vehicles_after_insert on public.vehicles;
create trigger vehicles_after_insert after insert on public.vehicles
  for each row execute function public.vehicle_after_insert();

-- ========== 3. 紀錄（加油、充電、保養、其他費用、里程、照片） ==========
-- 每張表欄位相同：車、日期、里程放成欄位方便查詢，其餘內容放 data（和車帳本現在的格式一樣）
do $$
declare t text;
begin
  foreach t in array array['fuel_logs','charge_logs','maintenance_logs','expenses','odometer_logs','photos'] loop
    execute format($f$
      create table if not exists public.%1$I (
        id          text not null,
        vehicle_id  text not null references public.vehicles(id) on delete cascade,
        log_date    date,
        odometer    numeric,
        data        jsonb not null default '{}'::jsonb,
        created_by  uuid references auth.users(id) on delete set null default auth.uid(),
        created_at  timestamptz not null default now(),
        updated_at  timestamptz not null default now(),
        deleted_at  timestamptz,
        primary key (vehicle_id, id)
      )$f$, t);
    execute format('create index if not exists %1$I on public.%2$I (vehicle_id, updated_at)', t || '_sync_idx', t);
  end loop;
end $$;

-- 照片多兩個欄位：屬於哪筆紀錄、檔案位置
alter table public.photos add column if not exists log_table text;
alter table public.photos add column if not exists log_id text;
alter table public.photos add column if not exists storage_path text;

-- ========== 4. 修改時間由伺服器填（同步靠它判斷「上次之後改過的」） ==========
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  if tg_op = 'UPDATE' then new.created_at := old.created_at; new.created_by := old.created_by; end if;
  return new;
end $$;

do $$
declare t text;
begin
  foreach t in array array['vehicles','fuel_logs','charge_logs','maintenance_logs','expenses','odometer_logs','photos'] loop
    execute format('drop trigger if exists %1$I on public.%2$I', t || '_touch', t);
    execute format('create trigger %1$I before insert or update on public.%2$I for each row execute function public.touch_updated_at()', t || '_touch', t);
  end loop;
end $$;

create or replace function public.touch_profile() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  if current_user in ('authenticated','anon') then              -- 方案不能自己改
    if tg_op = 'UPDATE' then new.plan := old.plan; new.plan_until := old.plan_until;
    else new.plan := 'free'; new.plan_until := null; end if;
  end if;
  return new;
end $$;
drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before insert or update on public.profiles
  for each row execute function public.touch_profile();

-- 車主不能透過改資料把車變成別人的
create or replace function public.vehicle_keep_owner() returns trigger
language plpgsql as $$
begin new.owner_id := old.owner_id; return new; end $$;
drop trigger if exists vehicles_keep_owner on public.vehicles;
create trigger vehicles_keep_owner before update on public.vehicles
  for each row execute function public.vehicle_keep_owner();

-- ========== 5. 權限（Row Level Security） ==========
alter table public.profiles        enable row level security;
alter table public.vehicles        enable row level security;
alter table public.vehicle_members enable row level security;

drop policy if exists "自己的資料" on public.profiles;
create policy "自己的資料" on public.profiles for all to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

drop policy if exists "成員看車" on public.vehicles;
drop policy if exists "建立自己的車" on public.vehicles;
drop policy if exists "車主改車" on public.vehicles;
drop policy if exists "車主刪車" on public.vehicles;
create policy "成員看車"     on public.vehicles for select to authenticated using (public.is_member(id));
create policy "建立自己的車" on public.vehicles for insert to authenticated with check (owner_id = auth.uid());
create policy "車主改車"     on public.vehicles for update to authenticated using (public.is_owner(id)) with check (public.is_owner(id));
create policy "車主刪車"     on public.vehicles for delete to authenticated using (public.is_owner(id));

drop policy if exists "成員看成員" on public.vehicle_members;
drop policy if exists "退出或車主移除" on public.vehicle_members;
create policy "成員看成員" on public.vehicle_members for select to authenticated using (public.is_member(vehicle_id));
create policy "退出或車主移除" on public.vehicle_members for delete to authenticated
  using ((user_id = auth.uid() and role <> 'owner') or (public.is_owner(vehicle_id) and role <> 'owner'));
-- 加入共管走第三階段的邀請流程，這裡不開放直接新增

do $$
declare t text;
begin
  foreach t in array array['fuel_logs','charge_logs','maintenance_logs','expenses','odometer_logs','photos'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "成員讀寫" on public.%I', t);
    execute format('create policy "成員讀寫" on public.%I for all to authenticated using (public.is_member(vehicle_id)) with check (public.is_member(vehicle_id))', t);
  end loop;
end $$;

-- ========== 6. 照片空間（私人，路徑第一層是車輛編號） ==========
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('photos', 'photos', false, 20971520, array['image/jpeg','image/png','image/webp','application/pdf'])
on conflict (id) do update set file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "車輛成員看照片" on storage.objects;
drop policy if exists "車輛成員傳照片" on storage.objects;
drop policy if exists "車輛成員改照片" on storage.objects;
drop policy if exists "車輛成員刪照片" on storage.objects;
create policy "車輛成員看照片" on storage.objects for select to authenticated
  using (bucket_id = 'photos' and public.is_member((storage.foldername(name))[1]));
create policy "車輛成員傳照片" on storage.objects for insert to authenticated
  with check (bucket_id = 'photos' and public.is_member((storage.foldername(name))[1]));
create policy "車輛成員改照片" on storage.objects for update to authenticated
  using (bucket_id = 'photos' and public.is_member((storage.foldername(name))[1]));
create policy "車輛成員刪照片" on storage.objects for delete to authenticated
  using (bucket_id = 'photos' and public.is_member((storage.foldername(name))[1]));

-- 完成。下面這行應該列出 9 張表：
select table_name from information_schema.tables
where table_schema = 'public' and table_name in
  ('profiles','vehicles','vehicle_members','fuel_logs','charge_logs','maintenance_logs','expenses','odometer_logs','photos')
order by 1;
