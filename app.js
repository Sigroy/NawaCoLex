const $ = (sel) => document.querySelector(sel);

const state = {
    data: null,
    sourcesById: new Map(),
    markerDefs: {},
    entries: [],        // lista plana de entradas para buscar
    byId: new Map(),    // id -> entry
    activeId: null,
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
    uv: "Uso (náwat)",
    un: "Uso (español)",
    ue: "Uso (inglés)",
    xv: "Ejemplo (náwat)",
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

// Crea un id estable y corto para enlazar
function makeId(sourceId, recordIndex) {
    return `${sourceId}:${recordIndex}`;
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
    const all = rec._search;

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

function renderResults(results) {
    const el = $("#results");
    el.innerHTML = "";

    if (!results.length) {
        el.innerHTML = `<div class="emptyState" style="margin:8px;">
      <div class="emptyIcon">🫥</div>
      <h2>Sin resultados</h2>
      <p>Prueba con otra ortografía o usa “Contiene”.</p>
    </div>`;
        return;
    }

    for (const r of results) {
        const hw = escapeHtml(r.headword || "");
        const gn = escapeHtml((r.fields?.gn?.[0] || r.fields?.dn?.[0] || r.fields?.ge?.[0] || "").slice(0, 120));
        const srcName = escapeHtml(state.sourcesById.get(r.source_id)?.name || r.source_id);

        const div = document.createElement("div");
        div.className = "resultItem" + (r.id === state.activeId ? " active" : "");
        div.setAttribute("role", "listitem");
        div.tabIndex = 0;
        div.dataset.id = r.id;

        div.innerHTML = `
      <div style="flex:1; min-width:0;">
        <div class="hw">${hw}</div>
        <div class="snip">${gn || "<span class='muted'>(sin glosa)</span>"}</div>
        <div class="badge">${srcName}</div>
      </div>
      <div style="color: var(--faint); font-family: var(--mono); font-size: 11px; padding-top:2px;">
        ${escapeHtml(String(r.record_index))}
      </div>
    `;

        div.addEventListener("click", () => openEntry(r.id));
        div.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                openEntry(r.id);
            }
        });

        el.appendChild(div);
    }
}

function buildFieldRows(record, ordered = false) {
    // ordered=false: agrupa por marker
    // ordered=true: respeta el orden original (items)
    const rows = [];

    if (ordered && Array.isArray(record.items)) {
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

    const fields = record.fields || {};
    const keys = Object.keys(fields).sort((a, b) => a.localeCompare(b));
    for (const mk of keys) {
        const label = markerLabel(mk);
        const vals = fields[mk] || [];
        rows.push({k: label, code: mk, v: vals.join("\n\n")});
    }
    return rows;
}

function renderEntry(entry) {
    const detail = $("#detail");
    const src = state.sourcesById.get(entry.source_id);
    const srcName = src?.name || entry.source_id;
    const biblio = src?.bibliography || "";

    const hw = entry.headword || firstField(entry, "lx") || "(sin lema)";
    const ps = firstField(entry, "ps");
    const di = firstField(entry, "di");
    const lz = firstField(entry, "lz");
    const gn = joinField(entry, "gn");
    const ge = joinField(entry, "ge");

    const headerSub = [
        ps ? `• ${ps}` : "",
        di ? `• Dialecto: ${di}` : "",
        lz ? `• lz: ${lz}` : "",
        `• Fuente: ${srcName}`
    ].filter(Boolean).join(" ");

    detail.innerHTML = `
    <div class="detailHeader">
      <div class="detailTitle">
        <h1>${escapeHtml(hw)}</h1>
        <div class="sub">${escapeHtml(headerSub)}</div>
      </div>
      <div style="display:flex; gap:10px; align-items:center;">
        <button class="btn" id="copyLinkBtn" type="button" title="Copiar enlace">🔗 Copiar</button>
      </div>
    </div>

    <div class="tabs">
      <button class="tab active" data-tab="resumen" type="button">Resumen</button>
      <button class="tab" data-tab="completo" type="button">Completo</button>
    </div>

    <div id="tab_resumen"></div>
    <div id="tab_completo" style="display:none;"></div>

    <div class="sectionTitle">Bibliografía</div>
    <div class="kvRow">
      <div class="k">Fuente</div>
      <div class="v">${escapeHtml(biblio || srcName)}</div>
    </div>
  `;

    // resumen: muestra lo más útil primero (sin perder nada)
    const resumen = $("#tab_resumen");
    const blocks = [];

    const addIf = (mk, titleOverride = null) => {
        const v = entry.fields?.[mk];
        if (Array.isArray(v) && v.length) {
            blocks.push({
                label: titleOverride || markerLabel(mk),
                code: mk,
                value: v.join("\n\n"),
            });
        }
    };

    // Prioridad (tú puedes ajustar)
    addIf("gn");
    addIf("ge");
    addIf("dn");
    addIf("de");
    addIf("ps");
    addIf("va");
    addIf("ph");
    addIf("pl");
    addIf("po");
    addIf("uv");
    addIf("un");
    addIf("ue");
    addIf("se");

    // Ejemplos
    addIf("xv");
    addIf("xn");
    addIf("xe");

    // Notas / etimología
    addIf("ec");
    addIf("et");
    addIf("ee");
    addIf("en");
    addIf("nt");
    addIf("ng");
    addIf("nd");

    resumen.innerHTML = blocks.length
        ? blocks.map(b => `
      <div class="kvRow">
        <div class="k">${escapeHtml(b.label)} <code>${escapeHtml(b.code)}</code></div>
        <div class="v">${escapeHtml(b.value)}</div>
      </div>
    `).join("")
        : `<div class="emptyState"><h2>Sin campos principales</h2><p class="muted">Abre “Completo” para ver todo.</p></div>`;

    // completo: todo, ordenado por items para respetar el SFM
    const completo = $("#tab_completo");
    const rows = buildFieldRows(entry, true);
    completo.innerHTML = rows.map(r => `
    <div class="kvRow">
      <div class="k">${escapeHtml(r.k)} <code>${escapeHtml(r.code)}</code></div>
      <div class="v">${escapeHtml(r.v)}</div>
    </div>
  `).join("");

    // Tabs
    detail.querySelectorAll(".tab").forEach(btn => {
        btn.addEventListener("click", () => {
            detail.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            const which = btn.dataset.tab;
            $("#tab_resumen").style.display = which === "resumen" ? "" : "none";
            $("#tab_completo").style.display = which === "completo" ? "" : "none";
        });
    });

    // Copy link
    $("#copyLinkBtn").addEventListener("click", async () => {
        const url = `${location.origin}${location.pathname}#/entrada/${encodeURIComponent(entry.id)}`;
        try {
            await navigator.clipboard.writeText(url);
            $("#copyLinkBtn").textContent = "✅ Copiado";
            setTimeout(() => $("#copyLinkBtn").textContent = "🔗 Copiar", 1200);
        } catch {
            // fallback
            prompt("Copia el enlace:", url);
        }
    });
}

function openEntry(id) {
    state.activeId = id;
    location.hash = `#/entrada/${encodeURIComponent(id)}`;
    // re-marcar activo en lista sin recalcular todo
    renderResults(state.lastResults);
}

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

function route() {
    const hash = location.hash || "#/";
    const mEntry = hash.match(/^#\/entrada\/(.+)$/);
    const isBiblio = hash === "#/bibliografia";

    if (isBiblio) {
        renderBibliografia();
        return;
    }

    if (mEntry) {
        const id = decodeURIComponent(mEntry[1]);
        const entry = state.byId.get(id);
        if (entry) {
            renderEntry(entry);
            state.activeId = id;
            renderResults(state.lastResults);
        } else {
            $("#detail").innerHTML = `<div class="emptyState">
        <div class="emptyIcon">⚠️</div>
        <h2>Entrada no encontrada</h2>
        <p class="muted">Puede que el enlace sea viejo o la base de datos haya cambiado.</p>
      </div>`;
        }
        return;
    }

    // default
    // no-op
}

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
        state.lastResults = [];
        $("#status").textContent = `Listo.`;
        renderResults([]);
        return;
    }

    const filtered = [];
    for (const e of state.entries) {
        if (srcFilter !== "all" && e.source_id !== srcFilter) continue;

        let ok = false;

        if (mode === "exact") {
            const hw = fold(e.headword || "");
            ok = (hw === q);
        } else if (mode === "prefix") {
            ok = (fold(e.headword || "").startsWith(q)) || (fold(firstField(e, "lz") || "").startsWith(q));
        } else if (mode === "contains") {
            ok = e._search.includes(q);
        } else {
            // smart
            ok = e._search.includes(q);
        }

        if (ok) filtered.push(e);
    }

    let results = filtered;

    if (mode === "smart") {
        results = filtered
            .map(e => ({e, s: scoreMatch(q, e)}))
            .filter(x => x.s > 0)
            .sort((a, b) => b.s - a.s)
            .map(x => x.e);
    } else {
        // stable-ish sort: headword then source
        results.sort((a, b) => (a.headword || "").localeCompare(b.headword || "") || (a.source_id || "").localeCompare(b.source_id || ""));
    }

    results = results.slice(0, limit);
    state.lastResults = results;

    $("#status").textContent = `${results.length} resultado(s) para “${qRaw}”.`;
    renderResults(results);
}

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
    $("#sourceFilter").addEventListener("change", search);

    $("#clearBtn").addEventListener("click", () => {
        $("#q").value = "";
        $("#status").textContent = "Listo.";
        state.lastResults = [];
        renderResults([]);
        $("#q").focus();
    });

    $("#q").addEventListener("keydown", (e) => {
        if (e.key === "Enter" && state.lastResults.length) {
            openEntry(state.lastResults[0].id);
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
                    id: makeId(sourceId, rec.record_index),
                };
                entry._search = getSearchText(entry);

                entries.push(entry);
                state.byId.set(entry.id, entry);
            }
        }

        state.entries = entries;

        $("#countPill").textContent = String(entries.length);
        $("#status").textContent = `${entries.length} entradas cargadas.`;

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
