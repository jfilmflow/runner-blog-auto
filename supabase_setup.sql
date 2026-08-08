-- ============================================================
-- 러너 블로그 · P3 로그인/사용량 : Supabase SQL
-- Supabase 대시보드 → 왼쪽 "SQL Editor" → New query → 아래 전체 붙여넣고 Run
-- ============================================================

-- 이번 달 생성 편수를 유저별로 저장하는 표
create table if not exists public.usage_counters (
  user_id    uuid        not null,
  period     text        not null,          -- 'YYYY-MM' (예: 2026-08)
  count      int         not null default 0,
  updated_at timestamptz not null default now(),
  primary key (user_id, period)
);

-- RLS 켜기: 정책을 안 만들면 일반 사용자는 접근 불가,
-- 서버(service_role 키)만 읽고 쓸 수 있음 = 사용량을 사용자가 못 건드림(안전).
alter table public.usage_counters enable row level security;

-- 생성 성공 시 +1 (원자적). 서버에서 이 함수를 호출합니다.
create or replace function public.increment_usage(p_user uuid, p_period text)
returns int
language plpgsql
security definer
set search_path = public
as $$
declare
  cur int;
begin
  insert into public.usage_counters (user_id, period, count)
    values (p_user, p_period, 1)
    on conflict (user_id, period)
    do update set count = public.usage_counters.count + 1,
                  updated_at = now()
    returning count into cur;
  return cur;
end;
$$;
