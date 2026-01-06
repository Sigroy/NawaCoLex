const $ = (sel) => document.querySelector(sel);

const state = {
    data: null,
    sourcesById: new Map(),
    markerDefs: {},
    entries: [],            // entradas planas (por si luego querés debug)
    byId: new Map(),        // entryId -> entry
    groups: [],             // lista de lemas agrupados
    byGroupKey: new Map(),  // groupKey -> group
    activeGroupKey: null,
    activeSourceId: null,
    activeVariantByKey: new Map(), // `${groupKey}|${sourceId}` -> index
    lastResults: [],
    theme: "dark",
};

const ES_MARKER_LABELS = {
    lx: "Lema",
    di: "Dialecto",
    so: "Fuente",
    lz: "Paleografía",
    ps: "Categoría gramatical",
    gn: "Glosa (español)",
    ge: "Glosa (inglés)",
    dn: "Definición (español)",
    de: "Definición (inglés)",
    va: "Variante",
    ph: "Forma fonética",
    pl: "Plural",
    po: "Poseído",
    se: "Subentrada",
    sn: "Sentido",
    nt: "Nota",
    ng: "Nota (gramática)",
    nd: "Nota (discurso)",
    ec: "Comentario etimológico",
    et: "Etimología (préstamo/esp.)",
    bw: "Préstamo (lengua)",
    cf: "Referencia cruzada",
    sy: "Sinónimo",
    uv: "Uso (náhuat)",
    un: "Uso (español)",
    ue: "Uso (inglés)",
    xv: "Ejemplo (náhuat)",
    xn: "Ejemplo (español)",
    xe: "Ejemplo (inglés)",
    we: "Glosa literal (inglés)",
    wv: "Colocación/variante",
    sc: "Nombre científico",
    fm: "Función morfológica",
    if: "Flexión",
    pdl: "Etiqueta de paradigma",
    pdv: "Forma de paradigma",
};

function markerLabel(code) {
    return ES_MARKER_LABELS[code]
        || state.markerDefs?.[code]?.title
        || code;
}

function escapeHtml(s) {
    return (s ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

// Normaliza para búsqueda: minúsculas + sin acentos
function fold(str) {
    if (!str) return "";
    const s = String(str).toLowerCase();
    try {
        return s.normalize("NFD").replace(/\p{Diacritic}/gu, "");
    } catch {
        // fallback sin unicode property escapes
        return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    }
}

// Heurística: filtra “headers” tipo Campbell_LX (st -1)
function isHousekeeping(record) {
    const st = record?.fields?.st;
    if (Array.isArray(st) && st.includes("-1")) return true;
    const hw = record?.headword || record?.fields?.lx?.[0];
    if (typeof hw === "string" && /_LX$/.test(hw)) return true;
    return false;
}

function firstField(record, marker) {
    const v = record?.fields?.[marker];
    return Array.isArray(v) && v.length ? v[0] : "";
}

function joinField(record, marker) {
    const v = record?.fields?.[marker];
    if (!Array.isArray(v) || !v.length) return "";
    return v.join("\n");
}

function getSearchText(record) {
    // indexa lo más útil por ahora
    const parts = [];
    const pushAll = (mk) => {
        const arr = record?.fields?.[mk];
        if (Array.isArray(arr)) parts.push(...arr);
    };

    pushAll("lx");
    pushAll("lz");
    pushAll("va");
    pushAll("gn");
    pushAll("ge");
    pushAll("dn");
    pushAll("de");
    // también subentradas pueden ayudar
    pushAll("se");

    return fold(parts.join(" · "));
}

function scoreMatch(q, rec) {
    // scoring simple para "smart"
    const hw = fold(firstField(rec, "lx") || rec.headword || "");
    const lz = fold(firstField(rec, "lz") || "");
    const gn = fold(joinField(rec, "gn") || "");
    const ge = fold(joinField(rec, "ge") || "");
    const all = rec._search || "";

    if (!q) return 0;

    let score = 0;

    // exact
    if (hw === q) score += 1000;

    // prefix
    if (hw.startsWith(q)) score += 400;
    if (lz.startsWith(q)) score += 220;

    // contains
    if (hw.includes(q)) score += 180;
    if (lz.includes(q)) score += 120;
    if (gn.includes(q)) score += 90;
    if (ge.includes(q)) score += 70;
    if (all.includes(q)) score += 40;

    // shorter headwords slightly favored when equal
    score -= Math.min(hw.length, 30) * 0.2;

    return score;
}

function buildFieldRows(record) {
    const rows = [];

    if (Array.isArray(record.items) && record.items.length) {
        for (const it of record.items) {
            const mk = it.marker;
            const label = markerLabel(mk);
            rows.push({
                k: `${label}`,
                code: mk,
                v: it.value ?? "",
            });
        }
        return rows;
    }

    // fallback si no hay items
    const fields = record.fields || {};
    const keys = Object.keys(fields).sort((a, b) => a.localeCompare(b));
    for (const mk of keys) {
        const label = markerLabel(mk);
        const vals = fields[mk] || [];
        rows.push({k: label, code: mk, v: vals.join("\n\n")});
    }
    return rows;
}

/* =========================
   AGRUPACIÓN POR LEMA
   ========================= */

function groupKeyForEntry(entry) {
    // Para agrupar "kwawit" una sola vez: usamos el lema (lx/headword) normalizado
    const hw = entry.headword || firstField(entry, "lx") || "";
    return fold(hw);
}

function pickBestSnippetFromEntry(entry) {
    const gn = entry.fields?.gn?.[0];
    const dn = entry.fields?.dn?.[0];
    const ge = entry.fields?.ge?.[0];
    const de = entry.fields?.de?.[0];

    return gn || dn || ge || de || "";
}

function shortSourceName(sourceId) {
    const s = state.sourcesById.get(sourceId);
    return (s?.name || sourceId).trim();
}

function buildGroups(entries) {
    const byKey = new Map();

    for (const e of entries) {
        const key = groupKeyForEntry(e);
        if (!key) continue;

        if (!byKey.has(key)) {
            const display = (e.headword || firstField(e, "lx") || "").trim() || "(sin lema)";
            byKey.set(key, {
                key,
                display,
                bySource: new Map(),  // sourceId -> [entries]
                entries: [],
                _search: "",
                _headwordsFold: new Set(),
            });
        }

        const g = byKey.get(key);
        g.entries.push(e);
        g._headwordsFold.add(fold(e.headword || firstField(e, "lx") || ""));

        if (!g.bySource.has(e.source_id)) g.bySource.set(e.source_id, []);
        g.bySource.get(e.source_id).push(e);
    }

    // computar _search por grupo
    for (const g of byKey.values()) {
        const all = [];
        for (const e of g.entries) all.push(e._search || "");
        g._search = all.join(" · ");
    }

    // ordenar fuentes dentro de cada grupo (por nombre)
    for (const g of byKey.values()) {
        for (const [sid, arr] of g.bySource.entries()) {
            arr.sort((a, b) => {
                // si hay hm, úsalo, si no, record_index
                const ahm = parseInt(firstField(a, "hm") || "0", 10);
                const bhm = parseInt(firstField(b, "hm") || "0", 10);
                if (ahm !== bhm) return ahm - bhm;
                return (a.record_index || 0) - (b.record_index || 0);
            });
        }
    }

    // convertir a lista ordenada (alfabético por display)
    const list = Array.from(byKey.values()).sort((a, b) => a.display.localeCompare(b.display));

    state.byGroupKey = byKey;
    state.groups = list;
}

/* =========================
   RESULTADOS (1 POR LEMA)
   ========================= */

function renderResults(groups) {
    const el = $("#results");
    el.innerHTML = "";

    if (!groups.length) {
        el.innerHTML = `<div class="emptyState" style="margin:8px;">
      <div class="emptyIcon">🫥</div>
      <h2>Sin resultados</h2>
      <p>Prueba con otra ortografía o usa “Contiene”.</p>
    </div>`;
        return;
    }

    for (const g of groups) {
        const hw = escapeHtml(g.display || "");
        // snippet: mejor de cualquiera de sus entradas
        let snippet = "";
        for (const e of g.entries) {
            const s = pickBestSnippetFromEntry(e);
            if (s) {
                snippet = s;
                break;
            }
        }
        const snip = escapeHtml((snippet || "").slice(0, 120));

        // fuentes (badge)
        const sourceIds = Array.from(g.bySource.keys());
        sourceIds.sort((a, b) => shortSourceName(a).localeCompare(shortSourceName(b)));

        const maxShown = 3;
        const shown = sourceIds.slice(0, maxShown).map(shortSourceName);
        const rest = sourceIds.length - shown.length;
        const badge = escapeHtml(rest > 0 ? `${shown.join(" · ")} · +${rest}` : shown.join(" · "));

        const div = document.createElement("div");
        div.className = "resultItem" + (g.key === state.activeGroupKey ? " active" : "");
        div.setAttribute("role", "listitem");
        div.tabIndex = 0;
        div.dataset.key = g.key;

        div.innerHTML = `
      <div style="flex:1; min-width:0;">
        <div class="hw">${hw}</div>
        <div class="snip">${snip || "<span class='muted'>(sin glosa)</span>"}</div>
        <div class="badge">${badge || "—"}</div>
      </div>
      <div style="color: var(--faint); font-family: var(--mono); font-size: 11px; padding-top:2px;">
        ${escapeHtml(String(sourceIds.length))}×
      </div>
    `;

        div.addEventListener("click", () => openGroup(g.key));
        div.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openGroup(g.key);
            }
        });

        el.appendChild(div);
    }
}

function openGroup(groupKey, sourceId = null) {
    state.activeGroupKey = groupKey;

    const g = state.byGroupKey.get(groupKey);
    if (!g) return;

    // si vienen con sourceId, respetalo; si no, elegí uno razonable
    let sid = sourceId;
    if (!sid) {
        const currentFilter = $("#sourceFilter")?.value;
        if (currentFilter && currentFilter !== "all" && g.bySource.has(currentFilter)) {
            sid = currentFilter;
        } else {
            // primera fuente por nombre
            const sids = Array.from(g.bySource.keys())
                .sort((a, b) => shortSourceName(a).localeCompare(shortSourceName(b)));
            sid = sids[0] || null;
        }
    }

    state.activeSourceId = sid;

    // ruta estable (incluye fuente para link exacto)
    if (sid) {
        location.hash = `#/lema/${encodeURIComponent(groupKey)}/${encodeURIComponent(sid)}`;
    } else {
        location.hash = `#/lema/${encodeURIComponent(groupKey)}`;
    }

    renderResults(state.lastResults);
}

/* =========================
   DETALLE (TABS POR FUENTE)
   ========================= */

function variantKey(groupKey, sourceId) {
    return `${groupKey}|${sourceId}`;
}

function getVariantIndex(groupKey, sourceId, max) {
    const k = variantKey(groupKey, sourceId);
    let idx = state.activeVariantByKey.get(k) ?? 0;
    if (idx < 0) idx = 0;
    if (idx >= max) idx = 0;
    return idx;
}

function setVariantIndex(groupKey, sourceId, idx) {
    const k = variantKey(groupKey, sourceId);
    state.activeVariantByKey.set(k, idx);
}

function renderGroup(groupKey, preferredSourceId = null) {
    const detail = $("#detail");
    const g = state.byGroupKey.get(groupKey);

    if (!g) {
        detail.innerHTML = `<div class="emptyState">
      <div class="emptyIcon">⚠️</div>
      <h2>Lema no encontrado</h2>
      <p class="muted">Puede que el enlace sea viejo o la base de datos haya cambiado.</p>
    </div>`;
        return;
    }

    // elegir fuente activa
    const sourceIds = Array.from(g.bySource.keys()).sort((a, b) => shortSourceName(a).localeCompare(shortSourceName(b)));
    let activeSid = preferredSourceId && g.bySource.has(preferredSourceId) ? preferredSourceId : state.activeSourceId;
    if (!activeSid || !g.bySource.has(activeSid)) activeSid = sourceIds[0] || null;
    state.activeSourceId = activeSid;
    state.activeGroupKey = groupKey;

    // entrada activa dentro de la fuente (por si hay homónimos)
    const variants = activeSid ? (g.bySource.get(activeSid) || []) : [];
    const vIdx = activeSid ? getVariantIndex(groupKey, activeSid, variants.length) : 0;
    const entry = variants[vIdx] || null;

    // header
    const sourcesLine = sourceIds.map(shortSourceName).join(" · ");
    const title = escapeHtml(g.display);

    // meta del entry (depende de fuente)
    let headerSub = "";
    let biblio = "";
    if (entry) {
        const src = state.sourcesById.get(entry.source_id);
        biblio = src?.bibliography || "";

        // const ps = firstField(entry, "ps");
        // const di = firstField(entry, "di");
        // const lz = firstField(entry, "lz");
    } else {
        headerSub = `Fuentes: ${sourcesLine}`;
    }

    detail.innerHTML = `
    <div class="detailHeader">
      <div class="detailTitle">
        <h1>${title}</h1>
        <div class="sub">${escapeHtml(headerSub)}</div>
      </div>
      <div style="display:flex; gap:10px; align-items:center;">
        <button class="btn" id="copyLinkBtn" type="button" title="Copiar enlace">🔗 Copiar</button>
      </div>
    </div>

    <div class="tabs" id="sourceTabs">
      ${sourceIds.map((sid) => {
        const active = sid === activeSid ? "active" : "";
        const name = escapeHtml(shortSourceName(sid));
        return `<button class="tab ${active}" data-source="${escapeHtml(sid)}" type="button">${name}</button>`;
    }).join("")}
    </div>

    ${variants.length > 1 ? `
      <div style="margin: 10px 0 14px; display:flex; gap:10px; align-items:center;">
        <div class="muted" style="font-size:12px; font-weight:700;">Variante</div>
        <select id="variantSelect" style="max-width: 360px;">
          ${variants.map((v, i) => {
        const hm = firstField(v, "hm");
        const label = hm ? `Homónimo ${hm}` : `Entrada ${v.record_index}`;
        const selected = i === vIdx ? "selected" : "";
        return `<option value="${i}" ${selected}>${escapeHtml(label)}</option>`;
    }).join("")}
        </select>
      </div>
    ` : ""}

    <div id="entryFields"></div>

    ${entry ? `
      <div class="sectionTitle">Bibliografía</div>
      <div class="kvRow">
        <div class="k">Fuente</div>
        <div class="v">${escapeHtml(biblio || shortSourceName(entry.source_id))}</div>
      </div>
    ` : ""}
  `;

    // render fields (todo, sin resumen)
    const entryFields = $("#entryFields");
    if (!entry) {
        entryFields.innerHTML = `<div class="emptyState">
      <div class="emptyIcon">🧩</div>
      <h2>Sin contenido</h2>
      <p class="muted">Este lema no tiene registros asociados.</p>
    </div>`;
    } else {
        const rows = buildFieldRows(entry);
        entryFields.innerHTML = rows.map(r => `
      <div class="kvRow">
        <div class="k">${escapeHtml(r.k)} <code>${escapeHtml(r.code)}</code></div>
        <div class="v">${escapeHtml(r.v)}</div>
      </div>
    `).join("");
    }

    // wire tabs
    detail.querySelectorAll("#sourceTabs .tab").forEach(btn => {
        btn.addEventListener("click", () => {
            const sid = btn.dataset.source;
            state.activeSourceId = sid;
            openGroup(groupKey, sid); // actualiza hash + rerender list active
            // renderGroup se llama en route()
        });
    });

    // wire variant select
    const sel = $("#variantSelect");
    if (sel) {
        sel.addEventListener("change", () => {
            const idx = parseInt(sel.value, 10) || 0;
            setVariantIndex(groupKey, activeSid, idx);
            // rerender sin tocar hash
            renderGroup(groupKey, activeSid);
        });
    }

    // Copy link (incluye fuente seleccionada)
    $("#copyLinkBtn").addEventListener("click", async () => {
        const sid = state.activeSourceId;
        const url = sid
            ? `${location.origin}${location.pathname}#/lema/${encodeURIComponent(groupKey)}/${encodeURIComponent(sid)}`
            : `${location.origin}${location.pathname}#/lema/${encodeURIComponent(groupKey)}`;
        try {
            await navigator.clipboard.writeText(url);
            $("#copyLinkBtn").textContent = "✅ Copiado";
            setTimeout(() => $("#copyLinkBtn").textContent = "🔗 Copiar", 1200);
        } catch {
            prompt("Copia el enlace:", url);
        }
    });
}

/* =========================
   BIBLIO
   ========================= */

function renderBibliografia() {
    const detail = $("#detail");
    const sources = state.data?.sources || [];
    detail.innerHTML = `
    <div class="detailHeader">
      <div class="detailTitle">
        <h1>Bibliografía</h1>
        <div class="sub">Fuentes disponibles en la base de datos.</div>
      </div>
    </div>
    ${sources.map(s => `
      <div class="kvRow">
        <div class="k">${escapeHtml(s.name || s.id)}</div>
        <div class="v">${escapeHtml(s.bibliography || "")}</div>
      </div>
    `).join("")}
  `;
}

/* =========================
   ROUTING
   ========================= */

function route() {
    const hash = location.hash || "#/";
    const isBiblio = hash === "#/bibliografia";

    if (isBiblio) {
        renderBibliografia();
        return;
    }

    // Nueva ruta: #/lema/<groupKey>/<sourceId?>
    const mLemma = hash.match(/^#\/lema\/([^\/]+)(?:\/([^\/]+))?$/);
    if (mLemma) {
        const groupKey = decodeURIComponent(mLemma[1]);
        const sourceId = mLemma[2] ? decodeURIComponent(mLemma[2]) : null;

        state.activeGroupKey = groupKey;
        state.activeSourceId = sourceId || state.activeSourceId;

        renderGroup(groupKey, sourceId);
        renderResults(state.lastResults);
        return;
    }

    // Compat: ruta vieja si te quedó algún link: #/entrada/<id>
    const mEntry = hash.match(/^#\/entrada\/(.+)$/);
    if (mEntry) {
        const id = decodeURIComponent(mEntry[1]);
        const entry = state.byId.get(id);
        if (!entry) {
            $("#detail").innerHTML = `<div class="emptyState">
        <div class="emptyIcon">⚠️</div>
        <h2>Entrada no encontrada</h2>
        <p class="muted">Puede que el enlace sea viejo o la base de datos haya cambiado.</p>
      </div>`;
            return;
        }
        const gKey = groupKeyForEntry(entry);
        const sid = entry.source_id;
        openGroup(gKey, sid);
        return;
    }

    // default: no-op
}

/* =========================
   SEARCH (sobre grupos)
   ========================= */

function debounce(fn, ms) {
    let t = null;
    return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
    };
}

function search() {
    const qRaw = $("#q").value.trim();
    const q = fold(qRaw);
    const mode = $("#mode").value;
    const limit = parseInt($("#limit").value, 10);
    const srcFilter = $("#sourceFilter").value;

    if (!q) {
        if (srcFilter !== "all") {
            // state.groups ya está alfabético por display
            const base = state.groups.filter(g => g.bySource.has(srcFilter));
            const results = base.slice(0, limit);

            state.lastResults = results;

            $("#status").textContent =
                `Mostrando ${results.length} de ${base.length} lemas en “${shortSourceName(srcFilter)}”. ` +
                `Escribe para filtrar dentro de esta fuente.`;

            renderResults(results);
            return;
        }

        // comportamiento actual para “Todas”
        state.lastResults = [];
        $("#status").textContent = `Listo.`;
        renderResults([]);
        return;
    }

    const filtered = [];

    for (const g of state.groups) {
        if (srcFilter !== "all" && !g.bySource.has(srcFilter)) continue;

        let ok = false;

        if (mode === "exact") {
            ok = g._headwordsFold.has(q); // exacto contra cualquier fuente del grupo
        } else if (mode === "prefix") {
            // prefix sobre el display (y también sobre el search general)
            ok = fold(g.display).startsWith(q) || g._search.includes(q);
        } else if (mode === "contains") {
            ok = g._search.includes(q);
        } else {
            // smart
            ok = g._search.includes(q);
        }

        if (ok) filtered.push(g);
    }

    let results = filtered;

    if (mode === "smart") {
        // score por grupo: max score entre sus entradas
        results = filtered
            .map(g => {
                let best = 0;
                for (const e of g.entries) {
                    const s = scoreMatch(q, e);
                    if (s > best) best = s;
                }
                return {g, s: best};
            })
            .filter(x => x.s > 0)
            .sort((a, b) => b.s - a.s)
            .map(x => x.g);
    } else {
        results.sort((a, b) => (a.display || "").localeCompare(b.display || ""));
    }

    results = results.slice(0, limit);
    state.lastResults = results;

    $("#status").textContent = `${results.length} resultado(s) para “${qRaw}”.`;
    renderResults(results);
}

/* =========================
   THEME + INIT
   ========================= */

function setupTheme() {
    const saved = localStorage.getItem("nawat_theme");
    state.theme = saved || "dark";
    document.documentElement.dataset.theme = state.theme === "light" ? "light" : "";
    $("#themeToggle").textContent = state.theme === "light" ? "☀️" : "🌙";

    $("#themeToggle").addEventListener("click", () => {
        state.theme = (state.theme === "light") ? "dark" : "light";
        document.documentElement.dataset.theme = state.theme === "light" ? "light" : "";
        $("#themeToggle").textContent = state.theme === "light" ? "☀️" : "🌙";
        localStorage.setItem("nawat_theme", state.theme);
    });
}

async function init() {
    setupTheme();

    $("#q").addEventListener("input", debounce(search, 120));
    $("#mode").addEventListener("change", search);
    $("#limit").addEventListener("change", search);

    $("#sourceFilter").addEventListener("change", () => {
        const sfVal = $("#sourceFilter").value;
        $("#q").placeholder = (sfVal === "all")
            ? "Buscar palabra en náhuat, español o inglés..."
            : `Buscar dentro de “${shortSourceName(sfVal)}”… (vacío = explorar)`;
        search();
    });

    $("#clearBtn").addEventListener("click", () => {
        $("#q").value = "";
        $("#status").textContent = "Listo.";
        state.lastResults = [];
        renderResults([]);
        $("#q").focus();
    });

    $("#q").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && state.lastResults.length) {
            openGroup(state.lastResults[0].key);
        }
    });

    try {
        const res = await fetch("./raw_lexicon.json", {cache: "no-cache"});
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        state.data = await res.json();

        // sources
        const sources = state.data.sources || [];
        sources.forEach(s => state.sourcesById.set(s.id, s));

        // marker defs
        state.markerDefs = state.data.marker_definitions || {};

        // fill source filter
        const sf = $("#sourceFilter");
        for (const s of sources) {
            const opt = document.createElement("option");
            opt.value = s.id;
            opt.textContent = s.name || s.id;
            sf.appendChild(opt);
        }

        // flatten lexicons->records into entries
        const lexicons = state.data.lexicons || [];
        const entries = [];

        for (const lex of lexicons) {
            const sourceId = lex.source_id;
            const records = lex.records || [];

            for (const rec of records) {
                if (isHousekeeping(rec)) continue;

                const entry = {
                    ...rec,
                    source_id: sourceId,
                    id: `${sourceId}:${rec.record_index}`,
                };
                entry._search = getSearchText(entry);

                entries.push(entry);
                state.byId.set(entry.id, entry);
            }
        }

        state.entries = entries;

        // build groups
        buildGroups(entries);

        $("#countPill").textContent = String(state.groups.length);
        $("#status").textContent = `${state.groups.length} lemas cargados (${entries.length} registros).`;

        // route
        window.addEventListener("hashchange", route);
        route();

    } catch (err) {
        console.error(err);
        $("#status").textContent = "Error cargando la base de datos.";
        $("#detail").innerHTML = `<div class="emptyState">
      <div class="emptyIcon">💥</div>
      <h2>No se pudo cargar la base de datos</h2>
      <p class="muted"></p>
    </div>`;
    }
}

init();
