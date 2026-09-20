(() => {
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
