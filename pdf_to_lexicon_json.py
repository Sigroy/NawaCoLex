#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pdfplumber

# -------------------------
# Config / POS
# -------------------------

BULLETS = {"•", "·", "∙", "◦", "●", "○", "▪", "–", "-"}

# TODO: Añadir más POS según el diccionario
# POS "base" (normalizado sin puntos)
POS_KEYS = {
    "n",
    "adj",
    "adv",
    "vi",
    "vt",
    "v dit",
    "conj subord",
    "sus",
    "verb",
    "preverb",
    "ina",
    "pron",
    "prep",
    "conj",
    # multi-token
    "sus ina",
    # a veces aparece como "v tr."
    "v tr",
}

def pos_key(token: str) -> str:
    # normaliza token para comparar POS
    return token.strip().lower().rstrip(".,;:")

def split_pos_from_tokens(tokens: List[str], j: int) -> Tuple[Optional[str], int]:
    """
    Intenta reconocer POS en tokens[j] (1 token) o tokens[j:j+2] (2 tokens).
    Retorna (pos_str, pos_len).
    """
    if j >= len(tokens):
        return None, 0

    # 2 tokens: "sus ina."
    if j + 1 < len(tokens):
        k2 = f"{pos_key(tokens[j])} {pos_key(tokens[j+1])}"
        if k2 in POS_KEYS:
            return f"{tokens[j]} {tokens[j+1]}", 2

        # "v tr." => tratémoslo como vt.
        if k2 == "v tr":
            return "vt.", 2

    # 1 token
    k1 = pos_key(tokens[j])
    if k1 in POS_KEYS:
        # normaliza un poco salida (mantén como en texto)
        return tokens[j], 1

    return None, 0


# -------------------------
# Regex helpers
# -------------------------

# Detecta token dialecto [Cuis.] (lo esperamos justo antes del POS)
DIALECT_TOKEN_RE = re.compile(r"^\[(?P<dialect>[^\]]+)\]$")

# Prefijo de sentido: "1." / "2." / "(1)" / "(2)" / "1)" / "2)"
SENSE_TOKEN_RE = re.compile(r"^(?:\(\d+\)|\d+)[.)]$")

# científico: [Bixa orellana] (opcional punto final)
SC_RE = re.compile(r"\[(?P<sc>[A-Z][a-z]+(?:\s+[a-z][a-z\-]+)+)\.?\]")


def normalize_space(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


def join_lines_safely(lines: List[str]) -> str:
    """
    Une líneas respetando cortes por salto:
    - si línea termina en '-' (corte de palabra), junta sin espacio
    - si no, junta con '\n' para conservar estructura del diccionario
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
                lines.append(ln.rstrip() if ln else "")
    return lines


def strip_leading_bullet(line: str) -> str:
    s = line.lstrip()
    if s and s[0] in BULLETS:
        return s[1:].lstrip()
    return s


def looks_like_entry_start(line: str, max_pos_index: int = 7) -> Optional[Dict[str, str]]:
    """
    Detecta inicio de entrada buscando POS temprano.
    Soporta lemas multi-palabra: 'achtu tamachtiluyan n ...'
    Soporta variantes separadas por coma: 'achkaw, echkaw sus ina. ...'
    Soporta dialecto como token: '[Cuis.]'
    Soporta sentido antes de POS: 'achiut 1. n ...'
    """
    s = strip_leading_bullet(line).strip()
    if not s:
        return None

    tokens = s.split()
    if len(tokens) < 2:
        return None

    # Busca un POS en una ventana pequeña al inicio (heurística muy efectiva)
    upper = min(len(tokens) - 1, max_pos_index)
    for j in range(1, upper + 1):
        pos, pos_len = split_pos_from_tokens(tokens, j)
        if not pos:
            continue

        # tokens antes de POS
        before = tokens[:j]
        after_tokens = tokens[j + pos_len :]
        after = " ".join(after_tokens).strip()

        # extrae sentido justo antes del POS (en before)
        sense_tokens: List[str] = []
        i = len(before) - 1
        while i >= 0 and SENSE_TOKEN_RE.match(before[i]):
            sense_tokens.insert(0, before[i])
            i -= 1
        sense_prefix = " ".join(sense_tokens).strip()
        lemma_tokens = before[: i + 1]

        if not lemma_tokens:
            continue

        # dialecto como token final del lema: "[Cuis.]"
        dialect = ""
        if lemma_tokens and DIALECT_TOKEN_RE.match(lemma_tokens[-1]):
            m = DIALECT_TOKEN_RE.match(lemma_tokens[-1])
            dialect = normalize_space(m.group("dialect")) if m else ""
            lemma_tokens = lemma_tokens[:-1]
            if not lemma_tokens:
                continue

        lemma_raw = normalize_space(" ".join(lemma_tokens))

        # validación ligera: evitar falsos positivos locos
        # (un lema debería tener al menos una letra)
        if not re.search(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñʼ’]", lemma_raw):
            continue

        tail = after
        if sense_prefix:
            tail = f"{sense_prefix} {tail}".strip()

        return {
            "lemma_raw": lemma_raw,
            "dialect": dialect,
            "pos": pos,
            "tail": tail,
        }

    return None


def parse_pdf_dictionary_to_records(lines: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    cur_def_lines: List[str] = []

    def flush():
        nonlocal cur, cur_def_lines, records
        if not cur:
            return

        definition = join_lines_safely(cur_def_lines).strip()

        # saca científico si aparece dentro
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

            # variantes separadas por coma dentro del lema (y el lema puede ser multi-palabra)
            # ej: "achkaw, echkaw" o "achijchin, atzijtzin"
            parts = [normalize_space(p) for p in start["lemma_raw"].split(",")]
            parts = [p for p in parts if p]

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
    ap = argparse.ArgumentParser(
        description="Convierte un diccionario PDF (texto real) a JSON de lexicon y opcionalmente lo fusiona con raw_lexicon.json."
    )
    ap.add_argument("pdf", help="Ruta al PDF.")
    ap.add_argument("--source-id", required=True, help='ID corto para la fuente (ej: "DICC_PDF").')
    ap.add_argument("--source-name", required=True, help='Nombre para la fuente (ej: "Diccionario X (PDF)").')
    ap.add_argument("--bibliography", required=True, help="Bibliografía (texto).")
    ap.add_argument("--out", required=True, help="Salida JSON (archivo).")
    ap.add_argument("--merge-into", help="Si lo das, lee este raw_lexicon.json y escribe uno nuevo con la fuente agregada en --out.")
    ap.add_argument("--pretty", action="store_true", help="JSON bonito (indentado).")
    args = ap.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    lines = extract_pdf_lines(pdf_path)
    if not lines:
        raise RuntimeError("No se extrajo texto. Si el PDF fuera escaneado, necesitarías OCR.")

    records = parse_pdf_dictionary_to_records(lines)

    new_source = {
        "id": args.source_id,
        "name": args.source_name,
        "bibliography": args.bibliography,
    }
    new_lexicon = {
        "source_id": args.source_id,
        "records": records,
    }

    if args.merge_into:
        base_path = Path(args.merge_into)
        base = json.loads(base_path.read_text(encoding="utf-8"))
        payload = merge_into_raw_lexicon(base, new_source, new_lexicon)
    else:
        payload = {
            "sources": [new_source],
            "lexicons": [new_lexicon],
        }

    indent = 2 if args.pretty else None
    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=indent), encoding="utf-8")
    print(f"OK: {len(records)} registros extraídos. Escrito: {args.out}")


if __name__ == "__main__":
    main()
