-- Season Watch: run this once in Supabase > SQL Editor > New query > Run

-- Shared market data (written only by the daily GitHub job, read by the app)
create table if not exists kv (k text primary key, v jsonb not null, updated_at timestamptz default now());
create table if not exists prices (tk text primary key, price numeric not null, day date not null, stats jsonb);
create table if not exists news (id bigserial primary key, tk text, day date, title text, source text, unique (tk, title));

-- Her own data (each signed-in user only sees their own rows)
create table if not exists watch (user_id uuid not null default auth.uid() references auth.users on delete cascade,
  tk text not null, primary key (user_id, tk));
create table if not exists buys (id bigserial primary key,
  user_id uuid not null default auth.uid() references auth.users on delete cascade,
  tk text not null, price numeric not null, amt numeric not null, day date not null,
  sold boolean not null default false, sell_price numeric);
create table if not exists manual (user_id uuid not null default auth.uid() references auth.users on delete cascade,
  tk text not null, price numeric not null, day date not null default current_date, primary key (user_id, tk));

alter table kv enable row level security;
alter table prices enable row level security;
alter table news enable row level security;
alter table watch enable row level security;
alter table buys enable row level security;
alter table manual enable row level security;

-- Signed-in users can read market data; only the service key (GitHub job) can write it.
drop policy if exists "read kv" on kv;         create policy "read kv" on kv for select to authenticated using (true);
drop policy if exists "read prices" on prices; create policy "read prices" on prices for select to authenticated using (true);
drop policy if exists "read news" on news;     create policy "read news" on news for select to authenticated using (true);

-- Personal tables: only your own rows.
drop policy if exists "own watch" on watch;   create policy "own watch" on watch for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "own buys" on buys;     create policy "own buys" on buys for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "own manual" on manual; create policy "own manual" on manual for all to authenticated using (user_id = auth.uid()) with check (user_id = auth.uid());
