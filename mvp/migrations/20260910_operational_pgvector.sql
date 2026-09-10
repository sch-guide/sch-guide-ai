-- 2026-09-10: Streamlit Cloud 운영 검색을 Supabase pgvector에 영구 저장합니다.
-- Supabase SQL Editor에서 한 번 실행합니다. 기존 활성 문서와 계정은 유지됩니다.
begin;
create schema if not exists extensions;
create extension if not exists vector with schema extensions;

alter table public.guide_documents add column if not exists source_type text not null default 'pdf';
alter table public.guide_documents add column if not exists chunk_count integer not null default 0;
alter table public.guide_documents add column if not exists indexed_at timestamptz;
alter table public.guide_documents add column if not exists last_error text not null default '';
alter table public.guide_documents add column if not exists replaces_id uuid;
alter table public.guide_chunks alter column page drop not null;
alter table public.guide_chunks add column if not exists source_type text not null default 'pdf';
alter table public.guide_chunks add column if not exists location text not null default '';
alter table public.guide_chunks add column if not exists normalized_text text not null default '';
alter table public.guide_chunks add column if not exists previous_chunk_id uuid;
alter table public.guide_chunks add column if not exists next_chunk_id uuid;
alter table public.guide_chunks add column if not exists parent_id uuid;

create table if not exists public.guide_review_requests (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    reporter_hash text not null check(length(reporter_hash)=32),
    document_ids uuid[] not null check(cardinality(document_ids) between 1 and 10),
    chunk_ids uuid[] not null check(cardinality(chunk_ids) between 1 and 40),
    status text not null default 'open' check(status in ('open','reviewing','closed'))
);
alter table public.guide_review_requests enable row level security;
revoke all on public.guide_review_requests from anon, authenticated;
grant select, update(status) on public.guide_review_requests to authenticated;
drop policy if exists guide_review_admin on public.guide_review_requests;
create policy guide_review_admin on public.guide_review_requests for all to authenticated
using(public.guide_is_admin()) with check(public.guide_is_admin());

create or replace function public.guide_publish(doc jsonb, parts jsonb) returns uuid
language plpgsql security definer set search_path=''
as $$
declare d public.guide_documents; p jsonb;
begin
    if not public.guide_is_admin() then raise exception 'ADMIN_REQUIRED'; end if;
    if jsonb_typeof(parts)<>'array' or jsonb_array_length(parts) not between 1 and 5000
    then raise exception 'INVALID_CHUNKS'; end if;
    insert into public.guide_documents(
        id,document_name,title,section,updated_date,file_hash,page_count,model,status,
        source_type,chunk_count,indexed_at,last_error,replaces_id
    ) values (
        (doc->>'id')::uuid,doc->>'document_name',doc->>'title',coalesce(doc->>'section',''),
        nullif(doc->>'updated_date','')::date,doc->>'file_hash',(doc->>'page_count')::integer,
        doc->>'model','active',coalesce(doc->>'source_type','pdf'),
        jsonb_array_length(parts),now(),'',
        nullif(doc->>'replaces_id','')::uuid
    ) returning * into d;
    for p in select value from jsonb_array_elements(parts) loop
        if nullif(p->>'page','') is not null and (p->>'page')::integer>d.page_count
        then raise exception 'INVALID_PAGE'; end if;
        insert into public.guide_chunks(
            id,document_id,document_name,page,title,section,updated_date,text,index,embedding,
            source_type,location,normalized_text,previous_chunk_id,next_chunk_id,parent_id
        ) values (
            (coalesce(p->>'id',p->>'chunk_id'))::uuid,d.id,d.document_name,
            nullif(coalesce(p->>'page',p->>'page_number'),'')::integer,d.title,
            coalesce(p->>'section',p->>'section_title',''),d.updated_date,
            coalesce(p->>'text',p->>'raw_text'),(p->>'index')::integer,
            (p->>'embedding')::extensions.vector,coalesce(p->>'source_type',d.source_type),
            coalesce(p->>'location',''),coalesce(p->>'normalized_text',''),
            nullif(p->>'previous_chunk_id','')::uuid,nullif(p->>'next_chunk_id','')::uuid,
            nullif(p->>'parent_id','')::uuid
        );
    end loop;
    return d.id;
end $$;
revoke all on function public.guide_publish(jsonb,jsonb) from public,anon;
grant execute on function public.guide_publish(jsonb,jsonb) to authenticated;

create or replace function public.guide_reindex(doc jsonb, parts jsonb) returns uuid
language plpgsql security definer set search_path=''
as $$
declare d public.guide_documents; p jsonb; target uuid := (doc->>'id')::uuid;
begin
    if not public.guide_is_admin() then raise exception 'ADMIN_REQUIRED'; end if;
    if jsonb_typeof(parts)<>'array' or jsonb_array_length(parts) not between 1 and 5000
    then raise exception 'INVALID_CHUNKS'; end if;
    select * into d from public.guide_documents where id=target and status='active' for update;
    if d.id is null then raise exception 'INACTIVE_DOCUMENT'; end if;
    delete from public.guide_chunks where document_id=target;
    for p in select value from jsonb_array_elements(parts) loop
        insert into public.guide_chunks(
            id,document_id,document_name,page,title,section,updated_date,text,index,embedding,
            source_type,location,normalized_text,previous_chunk_id,next_chunk_id,parent_id
        ) values (
            (coalesce(p->>'id',p->>'chunk_id'))::uuid,target,d.document_name,
            nullif(coalesce(p->>'page',p->>'page_number'),'')::integer,
            coalesce(doc->>'title',d.title),coalesce(p->>'section',p->>'section_title',''),
            nullif(doc->>'updated_date','')::date,coalesce(p->>'text',p->>'raw_text'),
            (p->>'index')::integer,(p->>'embedding')::extensions.vector,
            coalesce(p->>'source_type',d.source_type),coalesce(p->>'location',''),
            coalesce(p->>'normalized_text',''),nullif(p->>'previous_chunk_id','')::uuid,
            nullif(p->>'next_chunk_id','')::uuid,nullif(p->>'parent_id','')::uuid
        );
    end loop;
    update public.guide_documents set
        title=coalesce(doc->>'title',title),section=coalesce(doc->>'section',section),
        updated_date=nullif(doc->>'updated_date','')::date,source_type=coalesce(doc->>'source_type',source_type),
        chunk_count=jsonb_array_length(parts),indexed_at=now(),last_error=''
    where id=target;
    delete from public.guide_checklists where document_id=target;
    return target;
end $$;
revoke all on function public.guide_reindex(jsonb,jsonb) from public,anon;
grant execute on function public.guide_reindex(jsonb,jsonb) to authenticated;

drop function if exists public.guide_search(extensions.vector,text[],uuid[],integer);
create function public.guide_search(
    query_embedding extensions.vector(384),query_terms text[],document_ids uuid[],match_count integer default 40
) returns table(
    id uuid,document_id uuid,document_name text,page integer,title text,section text,
    updated_date date,text text,index integer,similarity double precision,source_type text,
    location text,normalized_text text,previous_chunk_id uuid,next_chunk_id uuid,parent_id uuid
)
language sql stable security invoker set search_path=''
as $$
    with scored as (
        select c.*,(1-(c.embedding operator(extensions.<=>) query_embedding)) as sim,
            (select count(*) from unnest(query_terms[1:16]) term
             where length(term)>1 and strpos(regexp_replace(lower(c.text),'\s+','','g'),
                                               regexp_replace(lower(term),'\s+','','g'))>0) as lexical
        from public.guide_chunks c join public.guide_documents d on d.id=c.document_id
        where c.document_id=any(document_ids) and d.status='active' and public.guide_is_member()
    ), ranked as (
        select s.*,row_number() over(order by s.sim desc,s.id) dense_rank,
               row_number() over(order by s.lexical desc,s.sim desc,s.id) keyword_rank
        from scored s
    )
    select r.id,r.document_id,r.document_name,r.page,r.title,r.section,r.updated_date,r.text,
           r.index,r.sim,r.source_type,r.location,r.normalized_text,r.previous_chunk_id,
           r.next_chunk_id,r.parent_id
    from ranked r
    where r.dense_rank<=greatest(1,least(match_count,80)/2)
       or (r.lexical>0 and r.keyword_rank<=greatest(1,least(match_count,80)/2))
    order by r.sim desc,r.id limit greatest(1,least(match_count,80))
$$;
revoke all on function public.guide_search(extensions.vector,text[],uuid[],integer) from public,anon;
grant execute on function public.guide_search(extensions.vector,text[],uuid[],integer) to authenticated;

create or replace function public.guide_request_review(document_ids uuid[],chunk_ids uuid[]) returns uuid
language plpgsql security definer set search_path=''
as $$
declare request_id uuid;
begin
    if not public.guide_is_member() then raise exception 'MEMBER_REQUIRED'; end if;
    if cardinality(document_ids) not between 1 and 10 or cardinality(chunk_ids) not between 1 and 40
       or exists(select 1 from unnest(document_ids) d where not exists(
          select 1 from public.guide_documents g where g.id=d and g.status='active'))
       or exists(select 1 from unnest(chunk_ids) c where not exists(
          select 1 from public.guide_chunks g where g.id=c and g.document_id=any(document_ids)))
    then raise exception 'INVALID_REVIEW_SOURCE'; end if;
    insert into public.guide_review_requests(reporter_hash,document_ids,chunk_ids)
    values(md5(auth.uid()::text),document_ids,chunk_ids) returning id into request_id;
    return request_id;
end $$;
revoke all on function public.guide_request_review(uuid[],uuid[]) from public,anon;
grant execute on function public.guide_request_review(uuid[],uuid[]) to authenticated;
commit;
