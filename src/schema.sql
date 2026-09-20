-- Supabase SQL Editor에서 실행합니다. 기존 rag_* 테이블과 병원 자료는 변경하지 않습니다.
-- 원내 한 병원/한 프로젝트용입니다. 여러 병원을 한 DB에 섞지 마세요.
begin;
create schema if not exists extensions;
create extension if not exists vector with schema extensions;

create table if not exists public.guide_profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    role text not null check (role in ('admin','staff')),
    active boolean not null default true
);
create table if not exists public.guide_documents (
    id uuid primary key,
    document_name text not null check (length(document_name) between 1 and 250),
    title text not null check (length(title) between 1 and 250),
    section text not null default '' check (length(section) <= 250),
    updated_date date,
    file_hash text not null unique check (length(file_hash) = 64),
    page_count integer not null check (page_count between 1 and 1000),
    model text not null check (model = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'),
    status text not null default 'active' check (status in ('active','retired')),
    created_by uuid not null default auth.uid() references auth.users(id),
    created_at timestamptz not null default now()
);
create table if not exists public.guide_chunks (
    id uuid primary key,
    document_id uuid not null references public.guide_documents(id),
    document_name text not null,
    page integer not null check (page >= 1),
    title text not null,
    section text not null default '',
    updated_date date,
    text text not null check (length(text) between 1 and 4000),
    index integer not null check (index >= 0),
    embedding extensions.vector(384) not null,
    unique(document_id, index)
);
create index if not exists guide_chunks_document on public.guide_chunks(document_id);
create table if not exists public.guide_checklists (
    id uuid primary key,
    document_id uuid not null references public.guide_documents(id),
    title text not null check (length(title) between 1 and 200),
    keywords text[] not null check (cardinality(keywords) between 1 and 16),
    items jsonb not null check (jsonb_typeof(items) = 'array' and jsonb_array_length(items) between 1 and 40),
    created_by uuid not null default auth.uid() references auth.users(id),
    created_at timestamptz not null default now()
);

-- 권한 검사는 요청한 직원 본인의 JWT만 사용합니다.
create or replace function public.guide_is_member() returns boolean
language sql stable security definer set search_path = ''
as $$ select exists(select 1 from public.guide_profiles where user_id=auth.uid() and active) $$;
create or replace function public.guide_is_admin() returns boolean
language sql stable security definer set search_path = ''
as $$ select exists(select 1 from public.guide_profiles where user_id=auth.uid() and active and role='admin') $$;
revoke all on function public.guide_is_member() from public, anon;
revoke all on function public.guide_is_admin() from public, anon;
grant execute on function public.guide_is_member(), public.guide_is_admin() to authenticated;

alter table public.guide_profiles enable row level security;
alter table public.guide_documents enable row level security;
alter table public.guide_chunks enable row level security;
alter table public.guide_checklists enable row level security;
drop policy if exists guide_profile_self on public.guide_profiles;
create policy guide_profile_self on public.guide_profiles for select to authenticated
using (user_id=auth.uid());
drop policy if exists guide_document_read on public.guide_documents;
create policy guide_document_read on public.guide_documents for select to authenticated
using (public.guide_is_member() and (status='active' or public.guide_is_admin()));
drop policy if exists guide_document_retire on public.guide_documents;
create policy guide_document_retire on public.guide_documents for update to authenticated
using (public.guide_is_admin()) with check (public.guide_is_admin());
drop policy if exists guide_chunk_read on public.guide_chunks;
create policy guide_chunk_read on public.guide_chunks for select to authenticated
using (public.guide_is_member() and exists (
    select 1 from public.guide_documents d where d.id=document_id and d.status='active'
));
drop policy if exists guide_checklist_read on public.guide_checklists;
create policy guide_checklist_read on public.guide_checklists for select to authenticated
using (public.guide_is_member() and exists (
    select 1 from public.guide_documents d where d.id=document_id and d.status='active'
));

revoke all on public.guide_profiles, public.guide_documents, public.guide_chunks, public.guide_checklists
from anon, authenticated;
grant select on public.guide_profiles, public.guide_documents, public.guide_chunks, public.guide_checklists
to authenticated;
grant update(status) on public.guide_documents to authenticated;

-- 문서와 모든 문단을 한 트랜잭션으로 등록합니다. 중간까지 등록된 문서는 남지 않습니다.
create or replace function public.guide_publish(doc jsonb, parts jsonb) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare d public.guide_documents; p jsonb;
begin
    if not public.guide_is_admin() then raise exception 'ADMIN_REQUIRED'; end if;
    if jsonb_typeof(parts) <> 'array' or jsonb_array_length(parts) not between 1 and 5000
    then raise exception 'INVALID_CHUNKS'; end if;
    insert into public.guide_documents(id,document_name,title,section,updated_date,file_hash,page_count,model)
    values ((doc->>'id')::uuid, doc->>'document_name', doc->>'title', coalesce(doc->>'section',''),
            (doc->>'updated_date')::date, doc->>'file_hash', (doc->>'page_count')::integer, doc->>'model')
    returning * into d;
    for p in select value from jsonb_array_elements(parts) loop
        if (p->>'page')::integer > d.page_count then raise exception 'INVALID_PAGE'; end if;
        insert into public.guide_chunks
        values ((p->>'id')::uuid,d.id,d.document_name,(p->>'page')::integer,d.title,d.section,
                d.updated_date,p->>'text',(p->>'index')::integer,(p->>'embedding')::extensions.vector);
    end loop;
    return d.id;
end $$;
revoke all on function public.guide_publish(jsonb,jsonb) from public, anon;
grant execute on function public.guide_publish(jsonb,jsonb) to authenticated;

-- SECURITY INVOKER: 벡터 검색에도 요청한 직원의 RLS가 적용됩니다.
create or replace function public.guide_search(
    query_embedding extensions.vector(384), query_terms text[], document_ids uuid[], match_count integer default 40
) returns table (
    id uuid, document_id uuid, document_name text, page integer, title text, section text,
    updated_date date, text text, index integer, similarity double precision
)
language sql stable security invoker set search_path = ''
as $$
    with scored as (
        select c.*, (1 - (c.embedding operator(extensions.<=>) query_embedding)) as sim,
            (select count(*) from unnest(query_terms[1:16]) term
             where length(term)>1 and strpos(
                regexp_replace(lower(c.text), '\s+', '', 'g'),
                regexp_replace(lower(term), '\s+', '', 'g'))>0) as lexical
        from public.guide_chunks c
        where c.document_id=any(document_ids) and public.guide_is_member()
    ), ranked as (
        select s.*,
            row_number() over(order by s.sim desc,s.id) as dense_rank,
            row_number() over(order by s.lexical desc,s.sim desc,s.id) as keyword_rank
        from scored s
    )
    select r.id,r.document_id,r.document_name,r.page,r.title,r.section,r.updated_date,r.text,r.index,r.sim
    from ranked r
    where r.dense_rank <= greatest(1,least(match_count,80)/2)
       or (r.lexical>0 and r.keyword_rank <= greatest(1,least(match_count,80)/2))
    order by r.sim desc,r.id
    limit greatest(1,least(match_count,80))
$$;
revoke all on function public.guide_search(extensions.vector,text[],uuid[],integer) from public, anon;
grant execute on function public.guide_search(extensions.vector,text[],uuid[],integer) to authenticated;

-- 검토한 체크리스트만 등록합니다. 원문 인용과 문서 연결도 서버에서 다시 검사합니다.
create or replace function public.guide_save_checklist(entry jsonb) returns uuid
language plpgsql security definer set search_path = ''
as $$
declare item jsonb; source_text text; doc_id uuid := (entry->>'document_id')::uuid;
        item_quote text; new_id uuid := (entry->>'id')::uuid;
begin
    if not public.guide_is_admin() then raise exception 'ADMIN_REQUIRED'; end if;
    if not exists(select 1 from public.guide_documents where id=doc_id and status='active')
    then raise exception 'INACTIVE_DOCUMENT'; end if;
    if jsonb_typeof(entry->'items') <> 'array' or jsonb_array_length(entry->'items') not between 1 and 40
    then raise exception 'INVALID_ITEMS'; end if;
    for item in select value from jsonb_array_elements(entry->'items') loop
        select text into source_text from public.guide_chunks
        where id=(item->>'chunk_id')::uuid and document_id=doc_id;
        item_quote := regexp_replace(btrim(item->>'quote'), '\s+', ' ', 'g');
        if source_text is null or item_quote is null or length(item_quote)<4
           or strpos(regexp_replace(btrim(source_text), '\s+', ' ', 'g'),item_quote)=0
           or coalesce(item->>'stage','') not in ('사전 확인','준비','시행 전','시행 후','기타')
        then raise exception 'INVALID_EVIDENCE'; end if;
    end loop;
    insert into public.guide_checklists(id,document_id,title,keywords,items)
    values(new_id,doc_id,entry->>'title',
           array(select jsonb_array_elements_text(entry->'keywords')),entry->'items');
    return new_id;
end $$;
revoke all on function public.guide_save_checklist(jsonb) from public, anon;
grant execute on function public.guide_save_checklist(jsonb) to authenticated;
commit;

-- 계정 생성은 Supabase Authentication > Users에서 관리자가 합니다.
-- 공개 회원가입은 Authentication 설정에서 비활성화하세요.
-- 아래 주석을 복사하여 실제 사용자 UUID로 바꾼 뒤 별도로 실행하세요.
-- insert into public.guide_profiles(user_id,role) values ('실제-사용자-UUID','admin');
-- insert into public.guide_profiles(user_id,role) values ('실제-직원-UUID','staff');
-- 퇴사자 접근 차단:
-- update public.guide_profiles set active=false where user_id='실제-사용자-UUID';
