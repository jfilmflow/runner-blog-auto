-- ============================================================
-- 러너 블로그 · P3 로그인/사용량 : Supabase SQL
-- Supabase 대시보드 → 왼쪽 "SQL Editor" → New query → 아래 전체 붙여넣고 Run
--   (전에 표를 이미 만들었어도 이 파일 전체를 다시 Run 해도 안전합니다.)
-- ============================================================

-- 이번 달 생성 편수를 유저별로 저장하는 표
create table if not exists public.usage_counters (
  user_id    uuid        not null,
  period     text        not null,          -- 'YYYY-MM' (예: 2026-08)
  count      int         not null default 0,
  updated_at timestamptz not null default now(),
  primary key (user_id, period)
);

-- RLS 켜기: 정책을 안 만들면 사용자는 표에 직접 손을 못 댐(카운트 위조 방지).
-- 대신 아래 두 함수(security definer)를 통해서만 자기 것 조회/증가 가능.
alter table public.usage_counters enable row level security;

-- 이번 달 '내' 편수 조회 (auth.uid() = 로그인한 본인)
create or replace function public.my_usage(p_period text)
returns int
language sql
security definer
set search_path = public
stable
as $$
  select coalesce(
    (select count from public.usage_counters
      where user_id = auth.uid() and period = p_period), 0);
$$;

-- 생성 성공 시 '내' 편수 +1 (원자적). 증가만 가능 → 사용자가 못 내림.
create or replace function public.bump_usage(p_period text)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  cur int;
begin
  insert into public.usage_counters (user_id, period, count)
    values (auth.uid(), p_period, 1)
    on conflict (user_id, period)
    do update set count = public.usage_counters.count + 1,
                  updated_at = now()
    returning count into cur;
  return cur;
end;
$$;

-- 로그인한 사용자만 이 함수들을 실행할 수 있게 허용
grant execute on function public.my_usage(text)  to authenticated;
grant execute on function public.bump_usage(text) to authenticated;


-- ============================================================
-- P4 결제/구독 : 유저 플랜(free/pro) 저장
-- ============================================================
create table if not exists public.subscriptions (
  user_id    uuid        primary key,
  plan       text        not null default 'free',   -- 'free' | 'pro'
  status     text,                                   -- LS 상태(active/cancelled/expired 등)
  renews_at  timestamptz,
  updated_at timestamptz not null default now()
);

-- RLS: 사용자는 직접 못 건드림. 서버(service_role)만 웹훅으로 기록.
alter table public.subscriptions enable row level security;

-- 로그인한 '나'의 현재 플랜 조회 (없으면 free)
create or replace function public.my_plan()
returns text
language sql
security definer
set search_path = public
stable
as $$
  select coalesce((select plan from public.subscriptions where user_id = auth.uid()), 'free');
$$;

grant execute on function public.my_plan() to authenticated;
