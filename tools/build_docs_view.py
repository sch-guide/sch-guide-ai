"""Build the read-only, offline SCHAT documentation review surface."""

from __future__ import annotations

import argparse
import html
import json
import posixpath
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

GROUP_ORDER = (
    "08_멘토_검토",
    "07_쉬운_작업일지",
    "개요",
    "00_기획",
    "01_분석",
    "02_요구사항",
    "03_설계",
    "04_구현_테스트",
    "05_검수_매뉴얼",
    "06_변경이력",
)

INDEX_HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="description" content="SCHAT 공식 문서의 읽기 전용 검수 화면">
  <title>SCHAT 프로젝트 문서 검수</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <a class="skip-link" href="#document-content">본문으로 건너뛰기</a>
  <div class="app-shell">
    <aside class="sidebar" id="sidebar" aria-label="프로젝트 문서 목록">
      <div class="sidebar-head">
        <div class="brand-mark" aria-hidden="true">S</div>
        <div>
          <p class="eyebrow">SCHAT OFFICIAL DOCS</p>
          <h1>프로젝트 문서 검수</h1>
        </div>
      </div>
      <div class="official-notice">
        <span class="notice-dot" aria-hidden="true"></span>
        <div><strong>공식 정본</strong><span>docs/ 원본을 읽기 전용으로 표시합니다.</span></div>
      </div>
      <div class="sidebar-summary">
        <span>전체 문서</span><strong><span id="document-count">0</span>건</strong>
      </div>
      <div class="toolbar">
        <label class="search-box" for="document-search">
          <span aria-hidden="true">⌕</span>
          <input id="document-search" type="search" placeholder="문서명·제목·본문 검색" autocomplete="off">
          <kbd>/</kbd>
        </label>
        <button class="icon-button" id="theme-toggle" type="button" aria-label="다크 모드로 전환" aria-pressed="false">
          <span class="theme-icon" aria-hidden="true">◐</span>
        </button>
      </div>
      <p class="search-status" id="search-status" aria-live="polite"></p>
      <nav class="document-tree" id="document-tree" aria-label="문서 탐색"></nav>
      <div class="sidebar-foot">
        <span class="readonly-pill">읽기 전용</span>
        <span>docs_view · 렌더링 복사본</span>
      </div>
    </aside>

    <div class="workspace">
      <header class="mobile-bar">
        <button class="menu-button" id="menu-toggle" type="button" aria-controls="sidebar" aria-expanded="false">☰</button>
        <span>프로젝트 문서 검수</span>
        <button class="icon-button mobile-theme" id="mobile-theme-toggle" type="button" aria-label="테마 전환">◐</button>
      </header>
      <main class="document-pane" id="document-content" tabindex="-1">
        <div class="document-chrome">
          <nav class="breadcrumb" id="breadcrumb" aria-label="현재 문서 경로"></nav>
          <span class="source-badge">SOURCE · docs/</span>
        </div>
        <section class="metadata-card" id="metadata-card" hidden></section>
        <article class="markdown-body" id="document-body">
          <div class="loading-state">문서를 불러오는 중입니다.</div>
        </article>
        <footer class="document-footer">
          이 화면은 열람용 복사본입니다. 공식 문서의 유일한 정본은 <strong>docs/</strong>입니다.
        </footer>
      </main>
    </div>
  </div>
  <div class="sidebar-scrim" id="sidebar-scrim" hidden></div>
  <script src="assets/documents.js"></script>
  <script src="assets/app.js"></script>
</body>
</html>
"""

STYLE_CSS = r""":root {
  color-scheme: light;
  --bg: #f4f7f6;
  --surface: #ffffff;
  --surface-soft: #f0f5f3;
  --surface-hover: #e8f2ef;
  --text: #1e2926;
  --muted: #66736f;
  --line: #d9e3df;
  --accent: #167c68;
  --accent-strong: #0d6554;
  --accent-soft: #ddf3ec;
  --code-bg: #172420;
  --code-text: #e6f2ee;
  --shadow: 0 12px 34px rgba(23, 54, 46, .08);
  --sidebar-width: 356px;
  font-family: Inter, Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", system-ui, sans-serif;
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #101715;
  --surface: #17211e;
  --surface-soft: #1d2a26;
  --surface-hover: #24342f;
  --text: #e8f0ed;
  --muted: #9caaa5;
  --line: #30423c;
  --accent: #55c7aa;
  --accent-strong: #7cddc4;
  --accent-soft: #203d35;
  --code-bg: #0b100f;
  --code-text: #d8e9e4;
  --shadow: 0 12px 36px rgba(0, 0, 0, .28);
}

* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; background: var(--bg); color: var(--text); }
button, input { font: inherit; }
button { color: inherit; }
a { color: var(--accent-strong); }

.skip-link { position: fixed; left: 16px; top: -80px; z-index: 100; padding: 10px 14px; background: var(--surface); border: 2px solid var(--accent); border-radius: 8px; }
.skip-link:focus { top: 12px; }
.app-shell { min-height: 100%; }
.sidebar { position: fixed; inset: 0 auto 0 0; z-index: 20; width: var(--sidebar-width); display: flex; flex-direction: column; background: var(--surface); border-right: 1px solid var(--line); box-shadow: 8px 0 30px rgba(24, 56, 48, .04); }
.sidebar-head { display: flex; gap: 12px; align-items: center; padding: 25px 22px 16px; }
.brand-mark { display: grid; place-items: center; width: 42px; height: 42px; flex: 0 0 auto; border-radius: 13px; color: white; font-size: 19px; font-weight: 800; background: linear-gradient(145deg, var(--accent), #28a58a); box-shadow: 0 7px 16px rgba(22, 124, 104, .22); }
.eyebrow { margin: 0 0 3px; color: var(--accent); font-size: 10px; font-weight: 800; letter-spacing: .12em; }
.sidebar h1 { margin: 0; font-size: 20px; letter-spacing: -.04em; }
.official-notice { display: flex; gap: 10px; align-items: flex-start; margin: 0 18px 13px; padding: 11px 12px; border: 1px solid color-mix(in srgb, var(--accent) 25%, var(--line)); border-radius: 10px; background: var(--accent-soft); font-size: 12px; }
.notice-dot { width: 8px; height: 8px; margin-top: 4px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 15%, transparent); }
.official-notice div { display: grid; gap: 2px; }
.official-notice span { color: var(--muted); }
.sidebar-summary { display: flex; justify-content: space-between; padding: 0 22px 10px; color: var(--muted); font-size: 12px; }
.sidebar-summary strong { color: var(--text); }
.toolbar { display: grid; grid-template-columns: 1fr 42px; gap: 8px; padding: 0 18px 14px; }
.search-box { display: flex; align-items: center; gap: 8px; height: 42px; padding: 0 10px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); color: var(--muted); transition: border-color .15s, box-shadow .15s; }
.search-box:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 16%, transparent); }
.search-box input { min-width: 0; width: 100%; border: 0; outline: 0; color: var(--text); background: transparent; }
.search-box input::placeholder { color: var(--muted); }
kbd { padding: 1px 6px; border: 1px solid var(--line); border-bottom-width: 2px; border-radius: 5px; background: var(--surface); color: var(--muted); font-size: 10px; }
.icon-button, .menu-button { display: grid; place-items: center; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-soft); cursor: pointer; }
.icon-button:hover, .menu-button:hover { border-color: var(--accent); background: var(--surface-hover); }
.search-status { min-height: 18px; margin: -5px 22px 7px; color: var(--muted); font-size: 11px; }
.document-tree { flex: 1; min-height: 0; overflow: auto; padding: 2px 11px 18px; scrollbar-width: thin; scrollbar-color: var(--line) transparent; }
.tree-group { margin: 5px 0 12px; }
.group-heading { display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 10px; border: 0; background: transparent; color: var(--muted); font-size: 12px; font-weight: 800; text-align: left; cursor: pointer; }
.group-heading .chevron { font-size: 10px; transition: transform .15s; }
.tree-group.collapsed .chevron { transform: rotate(-90deg); }
.group-heading .folder-icon { color: var(--accent); }
.count-badge { margin-left: auto; min-width: 23px; padding: 2px 7px; border-radius: 999px; color: var(--accent-strong); background: var(--accent-soft); font-size: 10px; text-align: center; }
.group-documents { display: grid; gap: 2px; }
.tree-group.collapsed .group-documents { display: none; }
.document-button { position: relative; width: 100%; padding: 9px 12px 9px 31px; border: 0; border-radius: 8px; background: transparent; color: var(--text); line-height: 1.35; text-align: left; cursor: pointer; }
.document-button::before { content: ""; position: absolute; left: 15px; top: 14px; width: 5px; height: 5px; border-radius: 50%; background: var(--line); }
.document-button:hover { background: var(--surface-hover); }
.document-button.active { color: var(--accent-strong); background: var(--accent-soft); font-weight: 700; }
.document-button.active::before { background: var(--accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 15%, transparent); }
.document-title { display: block; font-size: 13px; }
.document-name { display: block; margin-top: 2px; color: var(--muted); font-size: 10px; overflow: hidden; text-overflow: ellipsis; }
.empty-results { padding: 28px 18px; color: var(--muted); font-size: 13px; text-align: center; }
.sidebar-foot { display: flex; align-items: center; gap: 8px; padding: 13px 18px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10px; }
.readonly-pill { padding: 3px 7px; border-radius: 999px; color: var(--accent-strong); background: var(--accent-soft); font-weight: 800; }

.workspace { min-height: 100vh; margin-left: var(--sidebar-width); }
.mobile-bar { display: none; }
.document-pane { width: min(1040px, calc(100% - 64px)); margin: 0 auto; padding: 26px 0 70px; outline: 0; }
.document-chrome { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 37px; margin-bottom: 15px; }
.breadcrumb { color: var(--muted); font-size: 12px; }
.breadcrumb span + span::before { content: "/"; margin: 0 7px; color: var(--line); }
.source-badge { padding: 5px 9px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); background: var(--surface); font-size: 9px; font-weight: 800; letter-spacing: .08em; }
.metadata-card { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 0 0 14px; padding: 13px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface-soft); }
.metadata-item { padding: 8px 10px; border-left: 3px solid var(--accent); }
.metadata-key { display: block; color: var(--muted); font-size: 10px; font-weight: 800; text-transform: uppercase; }
.metadata-value { display: block; margin-top: 3px; font-size: 13px; overflow-wrap: anywhere; }
.markdown-body { min-height: calc(100vh - 150px); padding: clamp(28px, 5vw, 62px) clamp(25px, 7vw, 76px); border: 1px solid var(--line); border-radius: 16px; background: var(--surface); box-shadow: var(--shadow); font-size: 15px; line-height: 1.78; }
.markdown-body > :first-child { margin-top: 0; }
.markdown-body > :last-child { margin-bottom: 0; }
.markdown-body h1, .markdown-body h2, .markdown-body h3, .markdown-body h4 { line-height: 1.35; letter-spacing: -.035em; scroll-margin-top: 22px; }
.markdown-body h1 { margin: 0 0 32px; padding-bottom: 18px; border-bottom: 2px solid var(--accent); font-size: clamp(27px, 4vw, 38px); }
.markdown-body h2 { margin: 46px 0 18px; padding-bottom: 9px; border-bottom: 1px solid var(--line); font-size: 24px; }
.markdown-body h3 { margin: 32px 0 13px; font-size: 19px; }
.markdown-body h4 { margin: 25px 0 10px; font-size: 16px; }
.markdown-body p { margin: 13px 0; }
.markdown-body ul, .markdown-body ol { padding-left: 1.5rem; }
.markdown-body li { margin: 6px 0; padding-left: 3px; }
.markdown-body blockquote { margin: 20px 0; padding: 12px 18px; border-left: 4px solid var(--accent); border-radius: 0 9px 9px 0; background: var(--surface-soft); color: var(--muted); }
.markdown-body blockquote p { margin: 3px 0; }
.table-wrap { overflow-x: auto; margin: 22px 0; border: 1px solid var(--line); border-radius: 10px; }
.markdown-body table { width: 100%; border-collapse: collapse; background: var(--surface); font-size: 13px; }
.markdown-body th, .markdown-body td { padding: 10px 12px; border-right: 1px solid var(--line); border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
.markdown-body th:last-child, .markdown-body td:last-child { border-right: 0; }
.markdown-body tr:last-child td { border-bottom: 0; }
.markdown-body th { background: var(--surface-soft); font-weight: 800; }
.markdown-body td.align-right, .markdown-body th.align-right { text-align: right; }
.markdown-body td.align-center, .markdown-body th.align-center { text-align: center; }
.markdown-body code { padding: .15em .4em; border-radius: 5px; background: var(--surface-soft); color: var(--accent-strong); font-family: "Cascadia Code", Consolas, monospace; font-size: .9em; }
.markdown-body pre { overflow: auto; margin: 20px 0; padding: 18px; border-radius: 11px; background: var(--code-bg); color: var(--code-text); line-height: 1.55; }
.markdown-body pre code { padding: 0; color: inherit; background: transparent; }
.markdown-body hr { margin: 38px 0; border: 0; border-top: 1px solid var(--line); }
.markdown-body a { text-decoration-color: color-mix(in srgb, var(--accent) 45%, transparent); text-underline-offset: 3px; }
.markdown-body a:hover { color: var(--accent); text-decoration-thickness: 2px; }
.markdown-body img { max-width: 100%; border: 1px solid var(--line); border-radius: 10px; }
.loading-state { display: grid; min-height: 360px; place-items: center; color: var(--muted); }
.document-footer { padding: 22px 10px 0; color: var(--muted); font-size: 11px; text-align: center; }
.sidebar-scrim { display: none; }

@media (max-width: 900px) {
  .sidebar { transform: translateX(-102%); transition: transform .2s ease; }
  body.sidebar-open .sidebar { transform: translateX(0); }
  .workspace { margin-left: 0; }
  .mobile-bar { position: sticky; top: 0; z-index: 12; display: grid; grid-template-columns: 42px 1fr 42px; align-items: center; gap: 9px; height: 58px; padding: 8px 14px; border-bottom: 1px solid var(--line); background: color-mix(in srgb, var(--surface) 92%, transparent); backdrop-filter: blur(12px); font-weight: 800; }
  .menu-button, .mobile-theme { width: 40px; height: 40px; }
  .document-pane { width: min(100% - 26px, 820px); padding-top: 14px; }
  .document-chrome { padding: 0 3px; }
  .markdown-body { padding: 28px 22px; border-radius: 12px; }
  .sidebar-scrim { position: fixed; inset: 0; z-index: 15; display: block; background: rgba(0, 0, 0, .38); }
  .sidebar-scrim[hidden] { display: none; }
}

@media (max-width: 540px) {
  :root { --sidebar-width: min(90vw, 350px); }
  .source-badge { display: none; }
  .markdown-body { font-size: 14px; }
  .markdown-body h1 { font-size: 27px; }
  .markdown-body h2 { font-size: 21px; }
}

@media print {
  .sidebar, .mobile-bar, .document-chrome, .document-footer { display: none !important; }
  .workspace { margin: 0; }
  .document-pane { width: 100%; padding: 0; }
  .markdown-body { border: 0; box-shadow: none; }
}
"""

APP_JS = r"""(() => {
  "use strict";

  const data = window.DOCS_VIEW_DATA;
  const documents = data.documents;
  const byId = new Map(documents.map((document) => [document.id, document]));
  const tree = document.getElementById("document-tree");
  const body = document.getElementById("document-body");
  const breadcrumb = document.getElementById("breadcrumb");
  const metadataCard = document.getElementById("metadata-card");
  const search = document.getElementById("document-search");
  const searchStatus = document.getElementById("search-status");
  const themeButtons = [document.getElementById("theme-toggle"), document.getElementById("mobile-theme-toggle")];
  const menuToggle = document.getElementById("menu-toggle");
  const scrim = document.getElementById("sidebar-scrim");
  let activeId = data.default_document_id;

  document.getElementById("document-count").textContent = String(data.document_count);

  function normalize(value) {
    return String(value || "").normalize("NFKC").toLocaleLowerCase("ko-KR");
  }

  function documentMatches(documentItem, query) {
    if (!query) return true;
    return normalize([
      documentItem.name,
      documentItem.title,
      documentItem.path,
      documentItem.search_text,
    ].join(" ")).includes(query);
  }

  function renderTree(query = "") {
    const normalizedQuery = normalize(query.trim());
    tree.replaceChildren();
    let visibleCount = 0;

    data.groups.forEach((group) => {
      const visibleDocuments = group.document_ids
        .map((id) => byId.get(id))
        .filter((documentItem) => documentMatches(documentItem, normalizedQuery));
      if (!visibleDocuments.length) return;
      visibleCount += visibleDocuments.length;

      const section = document.createElement("section");
      section.className = "tree-group";
      const heading = document.createElement("button");
      heading.className = "group-heading";
      heading.type = "button";
      heading.setAttribute("aria-expanded", "true");
      heading.innerHTML = `<span class="chevron" aria-hidden="true">▼</span><span class="folder-icon" aria-hidden="true">▰</span><span>${escapeHtml(group.name)}</span><span class="count-badge">${visibleDocuments.length}</span>`;
      heading.addEventListener("click", () => {
        section.classList.toggle("collapsed");
        heading.setAttribute("aria-expanded", String(!section.classList.contains("collapsed")));
      });

      const items = document.createElement("div");
      items.className = "group-documents";
      visibleDocuments.forEach((documentItem) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "document-button" + (documentItem.id === activeId ? " active" : "");
        button.dataset.documentId = documentItem.id;
        button.innerHTML = `<span class="document-title">${escapeHtml(documentItem.title)}</span><span class="document-name">${escapeHtml(documentItem.name)}</span>`;
        button.addEventListener("click", () => selectDocument(documentItem.id));
        items.append(button);
      });
      section.append(heading, items);
      tree.append(section);
    });

    if (!visibleCount) {
      const empty = document.createElement("p");
      empty.className = "empty-results";
      empty.textContent = "검색 결과가 없습니다.";
      tree.append(empty);
    }
    searchStatus.textContent = normalizedQuery ? `${visibleCount}개 문서 검색됨` : "";
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function renderMetadata(documentItem) {
    const entries = Object.entries(documentItem.frontmatter || {});
    metadataCard.replaceChildren();
    metadataCard.hidden = entries.length === 0;
    entries.forEach(([key, value]) => {
      const item = document.createElement("div");
      item.className = "metadata-item";
      item.innerHTML = `<span class="metadata-key">${escapeHtml(key)}</span><span class="metadata-value">${escapeHtml(value)}</span>`;
      metadataCard.append(item);
    });
  }

  function selectDocument(id, updateHash = true) {
    const documentItem = byId.get(id) || byId.get(data.default_document_id);
    if (!documentItem) return;
    activeId = documentItem.id;
    body.innerHTML = documentItem.html;
    breadcrumb.innerHTML = documentItem.path.split("/").map((part) => `<span>${escapeHtml(part)}</span>`).join("");
    renderMetadata(documentItem);
    document.title = `${documentItem.title} · SCHAT 문서 검수`;
    renderTree(search.value);
    if (updateHash) history.replaceState(null, "", `#doc=${encodeURIComponent(documentItem.id)}`);
    body.querySelectorAll("a[data-doc-id]").forEach((link) => {
      link.addEventListener("click", (event) => {
        event.preventDefault();
        selectDocument(link.dataset.docId);
      });
    });
    document.getElementById("document-content").scrollTo({ top: 0, behavior: "instant" });
    closeSidebar();
  }

  function idFromHash() {
    const params = new URLSearchParams(window.location.hash.slice(1));
    return params.get("doc") || data.default_document_id;
  }

  function setTheme(theme) {
    document.documentElement.dataset.theme = theme;
    themeButtons.forEach((button) => {
      if (!button) return;
      const dark = theme === "dark";
      button.setAttribute("aria-pressed", String(dark));
      button.setAttribute("aria-label", dark ? "라이트 모드로 전환" : "다크 모드로 전환");
      button.querySelector(".theme-icon")?.replaceChildren(document.createTextNode(dark ? "☀" : "◐"));
    });
    try { localStorage.setItem("schat-docs-theme", theme); } catch (_) { /* file:// privacy mode */ }
  }

  function initialTheme() {
    try {
      const saved = localStorage.getItem("schat-docs-theme");
      if (saved === "light" || saved === "dark") return saved;
    } catch (_) { /* file:// privacy mode */ }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function toggleTheme() {
    setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  }

  function openSidebar() {
    document.body.classList.add("sidebar-open");
    menuToggle.setAttribute("aria-expanded", "true");
    scrim.hidden = false;
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    menuToggle.setAttribute("aria-expanded", "false");
    scrim.hidden = true;
  }

  search.addEventListener("input", () => renderTree(search.value));
  themeButtons.forEach((button) => button?.addEventListener("click", toggleTheme));
  menuToggle.addEventListener("click", () => document.body.classList.contains("sidebar-open") ? closeSidebar() : openSidebar());
  scrim.addEventListener("click", closeSidebar);
  window.addEventListener("hashchange", () => selectDocument(idFromHash(), false));
  window.addEventListener("keydown", (event) => {
    if (event.key === "/" && document.activeElement !== search) {
      event.preventDefault();
      search.focus();
    }
    if (event.key === "Escape") {
      search.value = "";
      renderTree();
      closeSidebar();
    }
  });

  setTheme(initialTheme());
  renderTree();
  selectDocument(idFromHash(), false);
})();
"""


@dataclass(frozen=True)
class BuildSummary:
    document_count: int
    rendered_count: int


def _parse_frontmatter(markdown: str) -> tuple[dict[str, str], str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return {}, markdown
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, "\n".join(lines[end + 1 :])


def _plain_inline(text: str) -> str:
    escaped = html.escape(text, quote=True)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"~~(.+?)~~", r"<del>\1</del>", escaped)
    return escaped


_INLINE_TOKEN = re.compile(r"(`[^`\n]+`|!\[[^\]]*\]\([^)]+\)|\[[^\]]+\]\([^)]+\))")


def _resolve_link(
    target: str, current_path: str, document_ids: dict[str, str]
) -> tuple[str, str | None, bool]:
    parts = urlsplit(target)
    if parts.scheme in {"http", "https", "mailto"}:
        return target, None, True
    if target.startswith("#"):
        return target, None, False
    clean_target = parts.path.replace("\\", "/")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(current_path), clean_target))
    if resolved in document_ids:
        identifier = document_ids[resolved]
        return f"#doc={quote(identifier, safe='')}", identifier, False
    suffix = f"#{parts.fragment}" if parts.fragment else ""
    return f"../docs/{quote(resolved, safe='/')}" + suffix, None, False


def _render_inline(text: str, current_path: str, document_ids: dict[str, str]) -> str:
    output: list[str] = []
    cursor = 0
    for match in _INLINE_TOKEN.finditer(text):
        output.append(_plain_inline(text[cursor : match.start()]))
        token = match.group(0)
        if token.startswith("`"):
            output.append(f"<code>{html.escape(token[1:-1])}</code>")
        else:
            image = token.startswith("!")
            label_start = 2 if image else 1
            label_end = token.index("]")
            label = token[label_start:label_end]
            target = token[label_end + 2 : -1]
            href, document_id, external = _resolve_link(target, current_path, document_ids)
            if image:
                output.append(
                    f'<img src="{html.escape(href, quote=True)}" alt="{html.escape(label, quote=True)}">'
                )
            else:
                attributes = [f'href="{html.escape(href, quote=True)}"']
                if document_id:
                    attributes.append(f'data-doc-id="{html.escape(document_id, quote=True)}"')
                if external:
                    attributes.extend(['target="_blank"', 'rel="noopener noreferrer"'])
                output.append(f"<a {' '.join(attributes)}>{_plain_inline(label)}</a>")
        cursor = match.end()
    output.append(_plain_inline(text[cursor:]))
    return "".join(output)


def _table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    stripped = line.strip()
    if not stripped:
        return True
    if re.match(r"^#{1,6}\s+", line) or re.match(r"^```", stripped):
        return True
    if re.match(r"^\s*([-*+]\s+|\d+\.\s+|>\s?)", line):
        return True
    if stripped in {"---", "***", "___"}:
        return True
    return index + 1 < len(lines) and "|" in line and _is_table_separator(lines[index + 1])


def render_markdown(markdown: str, *, current_path: str, document_ids: dict[str, str]) -> str:
    """Render the supported official-doc Markdown subset without remote code."""

    _, markdown = _parse_frontmatter(markdown)
    lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    heading_counter = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        fence = re.match(r"^```\s*([\w+-]*)", stripped)
        if fence:
            language = fence.group(1)
            code_lines: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            index += 1 if index < len(lines) else 0
            language_class = f' class="language-{html.escape(language, quote=True)}"' if language else ""
            output.append(f"<pre><code{language_class}>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue

        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", line)
        if heading:
            level = len(heading.group(1))
            heading_counter += 1
            output.append(
                f'<h{level} id="section-{heading_counter}">{_render_inline(heading.group(2), current_path, document_ids)}</h{level}>'
            )
            index += 1
            continue

        if stripped in {"---", "***", "___"}:
            output.append("<hr>")
            index += 1
            continue

        if index + 1 < len(lines) and "|" in line and _is_table_separator(lines[index + 1]):
            headers = _table_cells(line)
            separators = _table_cells(lines[index + 1])
            alignments = [
                "align-center"
                if cell.startswith(":") and cell.endswith(":")
                else "align-right"
                if cell.endswith(":")
                else "align-left"
                for cell in separators
            ]
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(_table_cells(lines[index]))
                index += 1
            head = "".join(
                f'<th class="{alignments[position] if position < len(alignments) else "align-left"}">{_render_inline(cell, current_path, document_ids)}</th>'
                for position, cell in enumerate(headers)
            )
            body_rows = []
            for row in rows:
                body_rows.append(
                    "<tr>"
                    + "".join(
                        f'<td class="{alignments[position] if position < len(alignments) else "align-left"}">{_render_inline(cell, current_path, document_ids)}</td>'
                        for position, cell in enumerate(row)
                    )
                    + "</tr>"
                )
            output.append(
                f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'
            )
            continue

        if re.match(r"^\s*>\s?", line):
            quote_lines: list[str] = []
            while index < len(lines) and re.match(r"^\s*>\s?", lines[index]):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[index]))
                index += 1
            output.append(
                f"<blockquote><p>{_render_inline(' '.join(quote_lines), current_path, document_ids)}</p></blockquote>"
            )
            continue

        unordered = re.match(r"^\s*[-*+]\s+(.+)", line)
        ordered = re.match(r"^\s*\d+\.\s+(.+)", line)
        if unordered or ordered:
            tag = "ul" if unordered else "ol"
            items: list[str] = []
            pattern = r"^\s*[-*+]\s+(.+)" if unordered else r"^\s*\d+\.\s+(.+)"
            while index < len(lines):
                item = re.match(pattern, lines[index])
                if not item:
                    break
                items.append(f"<li>{_render_inline(item.group(1), current_path, document_ids)}</li>")
                index += 1
            output.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        paragraph = [line]
        index += 1
        while index < len(lines) and not _starts_block(lines, index):
            paragraph.append(lines[index])
            index += 1
        rendered_lines = [_render_inline(part.rstrip(), current_path, document_ids) for part in paragraph]
        output.append(f"<p>{' '.join(rendered_lines)}</p>")

    return "\n".join(output)


def _document_title(markdown: str, fallback: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", markdown, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _search_text(markdown: str) -> str:
    text = re.sub(r"```.*?```", " ", markdown, flags=re.DOTALL)
    text = re.sub(r"!?(?:\[([^]]*)\])\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`|~-]+", " ", text)
    return " ".join(text.split())


def _standalone_html(title: str, article: str, css_path: str) -> str:
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · SCHAT 문서</title><link rel="stylesheet" href="{html.escape(css_path, quote=True)}"></head>
<body><main class="document-pane"><div class="official-notice"><div><strong>열람용 복사본</strong><span>공식 정본은 docs/입니다.</span></div></div>
<article class="markdown-body">{article}</article></main></body></html>
"""


def build_docs_view(docs_dir: Path, output_dir: Path) -> BuildSummary:
    """Build a deterministic static review UI from the official docs tree."""

    docs_dir = Path(docs_dir).resolve()
    output_dir = Path(output_dir).resolve()
    source_files = sorted(docs_dir.rglob("*.md"), key=lambda path: path.relative_to(docs_dir).as_posix())
    relative_paths = [path.relative_to(docs_dir).as_posix() for path in source_files]
    document_ids = {path: str(PurePosixPath(path).with_suffix("")) for path in relative_paths}

    assets_dir = output_dir / "assets"
    rendered_root = output_dir / "rendered" / "docs"
    if rendered_root.exists():
        shutil.rmtree(rendered_root)
    assets_dir.mkdir(parents=True, exist_ok=True)
    rendered_root.mkdir(parents=True, exist_ok=True)

    documents: list[dict[str, Any]] = []
    group_members: dict[str, list[str]] = {name: [] for name in GROUP_ORDER}
    for source, relative in zip(source_files, relative_paths, strict=True):
        markdown = source.read_text(encoding="utf-8")
        frontmatter, _ = _parse_frontmatter(markdown)
        identifier = document_ids[relative]
        title = _document_title(markdown, source.stem)
        folder = PurePosixPath(relative).parts[0] if "/" in relative else "개요"
        article = render_markdown(markdown, current_path=relative, document_ids=document_ids)
        documents.append(
            {
                "id": identifier,
                "path": f"docs/{relative}",
                "folder": folder,
                "name": source.stem,
                "title": title,
                "frontmatter": frontmatter,
                "search_text": _search_text(markdown),
                "html": article,
            }
        )
        group_members.setdefault(folder, []).append(identifier)

        rendered_path = rendered_root / PurePosixPath(relative).with_suffix(".html")
        rendered_path.parent.mkdir(parents=True, exist_ok=True)
        css_path = posixpath.relpath((assets_dir / "style.css").as_posix(), rendered_path.parent.as_posix())
        rendered_path.write_text(_standalone_html(title, article, css_path), encoding="utf-8")

    groups = [
        {
            "name": name,
            "count": len(group_members.get(name, [])),
            "document_ids": group_members.get(name, []),
        }
        for name in GROUP_ORDER
        if group_members.get(name)
    ]
    payload = {
        "document_count": len(documents),
        "default_document_id": "README",
        "groups": groups,
        "documents": documents,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (assets_dir / "style.css").write_text(STYLE_CSS, encoding="utf-8")
    (assets_dir / "app.js").write_text(APP_JS, encoding="utf-8")
    (assets_dir / "documents.js").write_text(
        "window.DOCS_VIEW_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    return BuildSummary(len(documents), len(documents))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--docs", type=Path, default=root / "docs")
    parser.add_argument("--output", type=Path, default=root / "docs_view")
    args = parser.parse_args()
    summary = build_docs_view(args.docs, args.output)
    print(f"docs_view generated: documents={summary.document_count} rendered={summary.rendered_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
