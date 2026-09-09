-- 현재 앱용: Supabase Auth + 관리자만 접근하는 비공개 원본 Storage.
-- 검색 인덱스는 앱 서버의 SQLite/FAISS에 저장하므로 pgvector 설치는 필요하지 않습니다.
-- Supabase SQL Editor에서 관리자가 실행합니다. 실제 문서는 이 스크립트에 넣지 않습니다.
begin;
create table if not exists public.guide_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    role text not null check (role in ('admin','staff')),
    active boolean not null default true
);
alter table public.guide_profiles enable row level security;
revoke all on public.guide_profiles from anon, authenticated;
grant select on public.guide_profiles to authenticated;
drop policy if exists guide_profile_self on public.guide_profiles;
create policy guide_profile_self on public.guide_profiles for select to authenticated
using(user_id=auth.uid());

create or replace function public.guide_is_admin() returns boolean
language sql stable security definer set search_path=''
as $$ select exists(select 1 from public.guide_profiles
    where user_id=auth.uid() and active and role='admin') $$;
revoke all on function public.guide_is_admin() from public, anon;
grant execute on function public.guide_is_admin() to authenticated;

insert into storage.buckets(id,name,public,file_size_limit,allowed_mime_types)
values('guide-originals','guide-originals',false,20971520,array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
])
on conflict(id) do update set public=false,file_size_limit=excluded.file_size_limit,
    allowed_mime_types=excluded.allowed_mime_types;

-- 일반 직원은 원본 다운로드(SELECT)도 불가능합니다.
drop policy if exists guide_originals_admin on storage.objects;
create policy guide_originals_admin on storage.objects for all to authenticated
using(bucket_id='guide-originals' and public.guide_is_admin())
with check(bucket_id='guide-originals' and public.guide_is_admin());

-- 다른 버킷에 쓰던 넓은 허용 정책이 있어도 이 버킷은 관리자만 접근하도록 제한합니다.
drop policy if exists guide_originals_guard on storage.objects;
create policy guide_originals_guard on storage.objects as restrictive for all to public
using(bucket_id<>'guide-originals' or (auth.uid() is not null and public.guide_is_admin()))
with check(bucket_id<>'guide-originals' or (auth.uid() is not null and public.guide_is_admin()));
commit;

-- Authentication에서 사용자를 생성한 뒤 실제 UUID로 바꾸어 필요한 줄만 실행합니다.
-- insert into public.guide_profiles(user_id,role,active)
-- values('관리자-UUID','admin',true);
-- insert into public.guide_profiles(user_id,role,active)
-- values('직원-UUID','staff',true);
-- 퇴사/이동: update public.guide_profiles set active=false where user_id='직원-UUID';
-- Authentication의 공개 신규 가입은 비활성화하세요. 프로필 없는 계정은 앱에서도 접근을 거부합니다.
