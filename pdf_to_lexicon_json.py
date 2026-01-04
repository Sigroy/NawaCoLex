#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

# -------------------------
# POS (más robusto)
# -------------------------

# Para matching interno usamos “claves” normalizadas (sin puntos, minúsculas).
POS_KEYS = {
    "n",
    "adj",
    "adv",
    "vi",
    "vt",
    "sus",
    "verb",
    "preverb",
    "sus ina",  # “sus ina.”
    "ina",
    "pron",
    "prep",
    "conj",
}

def pos_key(token: str) -> str:
    # normaliza: lower + quita puntuación final típica
    return token.strip().lower().rstrip(".,;:")

def split_pos(rest: str) -> Tuple[Optional[str], str]:
    """
    Devuelve (pos, tail). pos se conserva “como aparece” (ej: 'sus.' / 'vi.'),
    pero el reconocimiento se hace con claves normalizadas.
    """
    r = rest.strip()
    parts = r.split()
    if not parts:
        return None, rest.strip()

    # Caso 2 tokens: "sus ina."
    if len(parts) >= 2:
        k2 = f"{pos_key(parts[0])} {pos_key(parts[1])}"
        if k2 in POS_KEYS:
            pos = f"{parts[0]} {parts[1]}".strip()
            tail = r[len(pos):].strip()
            return pos, tail

        # Caso especial: "v tr." / "v. tr."
        if pos_key(parts[0]) in {"v", "v."} and pos_key(parts[1]) in {"tr", "tr.", "trans", "trans."}:
            pos = f"{parts[0]} {parts[1]}".strip()
            tail = r[len(pos):].strip()
            return "vt.", tail  # lo guardamos como vt.

    # Caso 1 token
    k1 = pos_key(parts[0])
    if k1 in POS_KEYS:
        pos = parts[0]
        tail = r[len(parts[0]):].strip()
        return pos, tail

    return None, rest.strip()


# -------------------------
# Regex / parsing
# -------------------------

START_RE = re.compile(
    r"""
    ^\s*
    (?P<bullet>[•·∙◦●○▪–-])?
    \s*
    (?P<lema>[A-Za-zÁÉÍÓÚÜÑáéíóúüñʼ’:\-]+(?:\s*,\s*[A-Za-zÁÉÍÓÚÜÑáéíóúüñʼ’:\-]+)*)
    \s+
    (?P<rest>.+)
    $""",
    re.VERBOSE,
)

DIALECT_RE = re.compile(r"^\s*\[(?P<dialect>[^\]]+)\]\s*(?P<after>.*)$")
SC_RE = re.compile(r"\[(?P<sc>[A-Z][a-z]+(?:\s+[a-z][a-z\-]+)+)\.?\]")

def normalize_space(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()

def join_lines_safely(lines: List[str]) -> str:
    """
    Une líneas conservando saltos:
    - si la línea anterior termina en '-' => une sin espacio (palabra cortada)
    - si no => conserva como líneas separadas (con '\n')
    """
    out: List[str] = []
    for ln in lines:
        ln = ln.rstrip()
        if not ln:
            if out and out[-1] != "":
                out.append("")
            continue
        if not out:
            out.append(ln)
            continue
        prev = out[-1]
        if prev.endswith("-"):
            out[-1] = prev[:-1] + ln.lstrip()
        else:
            out.append(ln)
    return "\n".join(out).strip()

def extract_pdf_lines(pdf_path: Path) -> List[str]:
    lines: List[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=2, y_tolerance=3)
            if not text:
                continue
            for ln in text.splitlines():
                ln = ln.rstrip()
                lines.append(ln if ln else "")
    return lines

def looks_like_entry_start(line: str) -> Optional[Dict[str, str]]:
    m = START_RE.match(line)
    if not m:
        return None

    lemma_raw = normalize_space(m.group("lema"))  # <-- FIX: ahora sí existe
    rest = m.group("rest").strip()

    # dialecto: "[Cuis.] adj. ..."
    dialect = ""
    m_d = DIALECT_RE.match(rest)
    if m_d:
        dialect = normalize_space(m_d.group("dialect"))
        rest = (m_d.group("after") or "").strip()

    pos, tail = split_pos(rest)
    if not pos:
        return None

    return {
        "lemma_raw": lemma_raw,
        "dialect": dialect,
        "pos": pos,
        "tail": tail,
    }

def parse_pdf_dictionary_to_records(lines: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    cur_def_lines: List[str] = []

    def flush():
        nonlocal cur, cur_def_lines, records
        if not cur:
            return

        definition = join_lines_safely(cur_def_lines).strip()

        # saca científico si aparece
        sc = ""
        m_sc = SC_RE.search(definition)
        if m_sc:
            sc = m_sc.group("sc").strip()
            definition = (definition[:m_sc.start()] + definition[m_sc.end():]).strip()
            definition = normalize_space(definition)

        fields: Dict[str, List[str]] = {"lx": [cur["lx"]]}
        items: List[Dict[str, Any]] = [{"marker": "lx", "root": "lx", "subpath": None, "value": cur["lx"]}]

        if cur.get("va"):
            fields["va"] = cur["va"]
            for v in cur["va"]:
                items.append({"marker": "va", "root": "va", "subpath": None, "value": v})

        if cur.get("ps"):
            fields["ps"] = [cur["ps"]]
            items.append({"marker": "ps", "root": "ps", "subpath": None, "value": cur["ps"]})

        if cur.get("di"):
            fields["di"] = [cur["di"]]
            items.append({"marker": "di", "root": "di", "subpath": None, "value": cur["di"]})

        if sc:
            fields["sc"] = [sc]
            items.append({"marker": "sc", "root": "sc", "subpath": None, "value": sc})

        if definition:
            fields["dn"] = [definition]
            items.append({"marker": "dn", "root": "dn", "subpath": None, "value": definition})

        records.append({
            "record_index": len(records) + 1,
            "headword": cur["lx"],
            "fields": fields,
            "items": items,
        })

        cur = None
        cur_def_lines = []

    for raw in lines:
        ln = raw.strip()

        if not ln:
            if cur is not None:
                cur_def_lines.append("")
            continue

        start = looks_like_entry_start(ln)
        if start:
            flush()

            parts = [normalize_space(p) for p in start["lemma_raw"].split(",")]
            lx = parts[0]
            va = parts[1:] if len(parts) > 1 else []

            cur = {
                "lx": lx,
                "va": va,
                "ps": start["pos"],
                "di": start["dialect"] or "",
            }

            tail = start["tail"].strip()
            if tail:
                cur_def_lines.append(tail)
            continue

        if cur is not None:
            cur_def_lines.append(ln)

    flush()
    return records

def merge_into_raw_lexicon(base: Dict[str, Any], new_source: Dict[str, Any], new_lexicon: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    out.setdefault("sources", [])
    out.setdefault("lexicons", [])
    out.setdefault("marker_definitions", {})

    existing_sources = {s.get("id") for s in out["sources"] if isinstance(s, dict)}
    if new_source["id"] not in existing_sources:
        out["sources"].append(new_source)

    existing_lex = {l.get("source_id") for l in out["lexicons"] if isinstance(l, dict)}
    if new_lexicon["source_id"] in existing_lex:
        raise ValueError(f"Ya existe un lexicon con source_id={new_lexicon['source_id']} en el JSON base.")
    out["lexicons"].append(new_lexicon)

    return out

def main():
    ap = argparse.ArgumentParser(description="Convierte un diccionario PDF a tu JSON de lexicon y opcionalmente lo fusiona con raw_lexicon.json.")
    ap.add_argument("pdf", help="Ruta al PDF.")
    ap.add_argument("--source-id", required=True, help='ID corto (ej: "DICC_PDF").')
    ap.add_argument("--source-name", required=True, help='Nombre (ej: "Diccionario X (PDF)").')
    ap.add_argument("--bibliography", required=True, help="Bibliografía (texto).")
    ap.add_argument("--out", required=True, help="Salida JSON.")
    ap.add_argument("--merge-into", help="raw_lexicon.json base (opcional).")
    ap.add_argument("--pretty", action="store_true", help="Indentado bonito.")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    lines = extract_pdf_lines(pdf_path)
    if not lines:
        raise RuntimeError("No se extrajo texto. Si el PDF fuera escaneado, necesitarías OCR.")

    records = parse_pdf_dictionary_to_records(lines)

    new_source = {"id": args.source_id, "name": args.source_name, "bibliography": args.bibliography}
    new_lexicon = {"source_id": args.source_id, "records": records}

    if args.merge_into:
        base_path = Path(args.merge_into)
        base = json.loads(base_path.read_text(encoding="utf-8"))
        payload = merge_into_raw_lexicon(base, new_source, new_lexicon)
    else:
        payload = {"sources": [new_source], "lexicons": [new_lexicon]}

    indent = 2 if args.pretty else None
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    print(f"OK: {len(records)} registros extraídos. Escrito: {args.out}")

if __name__ == "__main__":
    main()
