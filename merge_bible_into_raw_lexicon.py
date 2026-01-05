#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict

# Marker lines: allow hyphens (if-pt, if-prefix, etc.)
MARKER_LINE_RE = re.compile(r"^\\([A-Za-z0-9_][A-Za-z0-9_-]*)\s*(.*)$")

# Minimal marker defs to add (only if base JSON has marker_definitions or you ask to create it)
MARKER_DEFS: Dict[str, Dict[str, Any]] = {
    "lx": {"db_name": "lexeme", "title": "Lexeme"},
    "ps": {"db_name": "part_of_speech", "title": "Part of Speech"},
    "gn": {"db_name": "gloss_es", "title": "Gloss (Spanish)"},
    "ge": {"db_name": "gloss_en", "title": "Gloss (English)"},
    "dn": {"db_name": "definition_es", "title": "Definition (Spanish)"},
    "de": {"db_name": "definition_en", "title": "Definition (English)"},
    "if": {"db_name": "inflection", "title": "Inflection"},
    "in": {"db_name": "inflection_gloss_es", "title": "Inflection Gloss (Spanish)"},
    "ie": {"db_name": "inflection_gloss_en", "title": "Inflection Gloss (English)"},
    "et": {"db_name": "etymology", "title": "Etymology"},
    "cf": {"db_name": "cross_reference", "title": "Cross Reference"},
    "se": {"db_name": "subentry", "title": "Subentry"},
    "sn": {"db_name": "sense_number", "title": "Sense Number"},
    "nt": {"db_name": "note", "title": "Note"},
    "ph": {"db_name": "phonetic_or_count", "title": "Phonetic Form / Count (source-dependent)"},
    "r":  {"db_name": "bible_refs", "title": "Bible References"},
    "gk": {"db_name": "greek_lemma", "title": "Greek Lemma/Keyword"},
    "st": {"db_name": "status", "title": "Status"},
    "_sh": {"db_name": "sfm_header", "title": "SFM Header"},
}

def decode_mixed_lines(data: bytes) -> Tuple[str, str]:
    """
    Decode a file that is potentially mixed-encoding by line.
    Strategy:
      - split into lines by bytes
      - for each line: try UTF-8 strict; if fails, cp1252; if fails, latin-1
    Returns (text, decoding_strategy_label)
    """
    lines = data.splitlines(keepends=False)
    out: List[str] = []

    # Handle UTF-8 BOM if present (only affects first line)
    if lines and lines[0].startswith(b"\xef\xbb\xbf"):
        lines[0] = lines[0][3:]

    for bline in lines:
        if bline == b"":
            out.append("")
            continue
        try:
            out.append(bline.decode("utf-8"))
            continue
        except UnicodeDecodeError:
            pass
        try:
            out.append(bline.decode("cp1252"))
            continue
        except UnicodeDecodeError:
            out.append(bline.decode("latin-1", errors="replace"))

    return "\n".join(out), "per-line: utf-8 else cp1252 else latin-1"

def split_marker(marker: str) -> Tuple[str, Optional[str]]:
    if "-" not in marker:
        return marker, None
    root, rest = marker.split("-", 1)
    return root, rest

def parse_sfm(text: str, drop_markers: Set[str]) -> Tuple[List[Dict[str, Any]], Set[str]]:
    """
    Parse SFM into records split on \\lx. Returns (records, used_markers).
    Keeps order + duplicates in `items`, plus aggregated `fields`.
    """
    records_out: List[Dict[str, Any]] = []
    used_markers: Set[str] = set()

    current_items: List[Dict[str, Any]] = []
    current_fields: Dict[str, List[str]] = defaultdict(list)
    current_has_lx = False
    last_item: Optional[Dict[str, Any]] = None

    def headword_from_fields(fields: Dict[str, List[str]]) -> Optional[str]:
        v = fields.get("lx")
        return v[0] if v else None

    def flush():
        nonlocal current_items, current_fields, current_has_lx, last_item
        if not current_items and not current_fields:
            current_items = []
            current_fields = defaultdict(list)
            current_has_lx = False
            last_item = None
            return

        record_index = len(records_out) + 1
        fields_dict = dict(current_fields)

        records_out.append({
            "record_index": record_index,
            "headword": headword_from_fields(fields_dict),
            "fields": fields_dict,
            "items": current_items,
        })

        current_items = []
        current_fields = defaultdict(list)
        current_has_lx = False
        last_item = None

    lines = text.splitlines()

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")

        m = MARKER_LINE_RE.match(line)
        if m:
            marker = m.group(1)
            value = m.group(2).rstrip()

            if marker in drop_markers:
                last_item = None
                continue

            if marker == "lx" and current_has_lx:
                flush()
            if marker == "lx":
                current_has_lx = True

            root, subpath = split_marker(marker)
            item = {"marker": marker, "root": root, "subpath": subpath, "value": value}

            current_items.append(item)
            current_fields[marker].append(value)
            used_markers.add(marker)
            last_item = item
            continue

        # continuation lines
        if last_item is None:
            continue

        if line.strip() == "":
            # preserve blank line only if next line is not a marker
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if MARKER_LINE_RE.match(next_line.rstrip("\r\n")):
                continue
            last_item["value"] = (last_item["value"] + "\n") if last_item["value"] else ""
            mk = last_item["marker"]
            current_fields[mk][-1] = last_item["value"]
            continue

        cont = line.rstrip()
        last_item["value"] = (last_item["value"] + "\n" + cont) if last_item["value"] else cont
        mk = last_item["marker"]
        current_fields[mk][-1] = last_item["value"]

    flush()
    return records_out, used_markers

def ensure_source(base: Dict[str, Any], source: Dict[str, Any]) -> None:
    base.setdefault("sources", [])
    existing = {s.get("id") for s in base["sources"] if isinstance(s, dict)}
    if source["id"] not in existing:
        base["sources"].append(source)

def add_or_replace_lexicon(base: Dict[str, Any], source_id: str, records: List[Dict[str, Any]], replace: bool) -> None:
    base.setdefault("lexicons", [])
    idx = None
    for i, lex in enumerate(base["lexicons"]):
        if isinstance(lex, dict) and lex.get("source_id") == source_id:
            idx = i
            break

    lex_obj = {"source_id": source_id, "records": records}

    if idx is None:
        base["lexicons"].append(lex_obj)
    else:
        if not replace:
            raise ValueError(f"raw_lexicon_old.json ya contiene un lexicon con source_id={source_id}. Usa --replace para reemplazarlo.")
        base["lexicons"][idx] = lex_obj

def update_marker_definitions(base: Dict[str, Any], used: Set[str], create_if_missing: bool) -> None:
    if "marker_definitions" not in base and not create_if_missing:
        return
    base.setdefault("marker_definitions", {})
    md = base["marker_definitions"]
    if not isinstance(md, dict):
        return

    for mk in sorted(used):
        if mk in md:
            continue
        meta = MARKER_DEFS.get(mk)
        if meta:
            md[mk] = {"code": mk, **meta}
        else:
            md[mk] = {"code": mk, "db_name": mk, "title": mk, "unknown": True}

def main():
    ap = argparse.ArgumentParser(description="Parse BibLex SFM file and merge into existing raw_lexicon_old.json")
    ap.add_argument("bible_file", help="Path to BibLex file (e.g., BibLex.db or BibLex.txt)")
    ap.add_argument("raw_lexicon", help="Path to existing raw_lexicon_old.json")
    ap.add_argument("--out", required=True, help="Write merged JSON here (use same path if you want overwrite)")

    ap.add_argument("--source-id", default="king_nt_2013", help="source_id to use inside lexicons[]")
    ap.add_argument("--source-name", default="King, Alan R. (2013)", help="sources[].name for this source")
    ap.add_argument("--bibliography", default="King, Alan R. El Léxico Náhuat del Nuevo Testamento. 2013.", help="sources[].bibliography")

    ap.add_argument("--replace", action="store_true", help="Replace existing lexicon for --source-id if present")

    ap.add_argument("--drop-st", action="store_true", help="Drop \\st markers while parsing")
    ap.add_argument("--drop-sh", action="store_true", help="Drop \\_sh markers while parsing")
    ap.add_argument("--drop-markers", default="", help="Comma-separated extra markers to drop (e.g. 'so,dt')")

    ap.add_argument("--update-marker-defs", action="store_true", help="Update base.marker_definitions with markers used by BibLex")
    ap.add_argument("--create-marker-defs", action="store_true", help="If base has no marker_definitions, create it when updating")

    args = ap.parse_args()

    bible_path = Path(args.bible_file)
    raw_path = Path(args.raw_lexicon)
    out_path = Path(args.out)

    if not bible_path.exists():
        raise FileNotFoundError(bible_path)
    if not raw_path.exists():
        raise FileNotFoundError(raw_path)

    # Load base JSON (UTF-8)
    base = json.loads(raw_path.read_text(encoding="utf-8"))

    # Decode Bible file with mixed-per-line strategy
    data = bible_path.read_bytes()
    text, strategy = decode_mixed_lines(data)

    drop: Set[str] = set()
    if args.drop_st:
        drop.add("st")
    if args.drop_sh:
        drop.add("_sh")
    if args.drop_markers.strip():
        for mk in args.drop_markers.split(","):
            mk = mk.strip()
            if mk:
                drop.add(mk)

    records, used = parse_sfm(text, drop_markers=drop)

    # Add source metadata (if not present)
    source_obj = {
        "id": args.source_id,
        "name": args.source_name,
        "bibliography": args.bibliography,
        # Helpful (optional) debug info:
        "file_decoding": strategy,
    }
    ensure_source(base, source_obj)

    # Add or replace lexicon
    add_or_replace_lexicon(base, args.source_id, records, replace=args.replace)

    # Optionally update marker definitions
    if args.update_marker_defs:
        update_marker_definitions(base, used, create_if_missing=args.create_marker_defs)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK: merged {len(records)} BibLex records into {out_path}")
    print(f"Decoding used: {strategy}")
    if drop:
        print(f"Dropped markers: {', '.join(sorted(drop))}")

if __name__ == "__main__":
    main()
