#!/usr/bin/env python3
"""
Convert SFM/MDF-style lexicon .txt files into JSON, preserving each source "as-is".

Key behaviors
- Records are split on \\lx (start of a new entry).
- Marker names may include hyphens: \\if-pt, \\if-prefix, etc.
- Lines that don't start with "\\" are treated as continuation lines of the previous marker value
  (joined with "\\n" to preserve formatting).
- Blank lines are ignored *only* when they look like entry separators (blank line followed by a marker).
  Otherwise, they are preserved inside the current field value.

New (requested)
- --minimal: outputs a GitHub-friendly JSON (no local paths, no generated_at, no files_scanned, no file_name)
- Built-in source bibliography + file->source mapping
- --drop-sh / --extract-sh: handle \\_sh as metadata (or drop it)

Usage
  python sfm_to_json.py "D:\\...\\python-script" --glob "*.txt" --out raw_lexicon_old.json --pretty --minimal --drop-sh
  python sfm_to_json.py "D:\\...\\python-script" --glob "*.txt" --out raw_lexicon_old.json --pretty --minimal --extract-sh
  python sfm_to_json.py file1.txt file2.txt --out raw_lexicon_old.json --minimal --drop-sh
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Allow hyphens in marker names, e.g. if-prefix, if-pt, etc.
MARKER_LINE_RE = re.compile(r"^\\([A-Za-z0-9_][A-Za-z0-9_-]*)\s*(.*)$")


# --- Source bibliography / catalog (you can expand this anytime) ---
SOURCES_CATALOG: List[Dict[str, str]] = [
    {
        "id": "arauz_1960",
        "name": "Aráuz, Próspero (1960)",
        "bibliography": "Aráuz, Próspero. El Pipil de la Región de los Itzalcos. San Salvador, El Salvador: Departamento Editorial del Ministerio de Cultura, 1960.",
    },
    {
        "id": "king_nt_2013",
        "name": "King, Alan R. (2013)",
        "bibliography": "King, Alan R. El Léxico Náhuat del Nuevo Testamento. 2013.",
    },
    {
        "id": "campbell_1985",
        "name": "Campbell, Lyle (1985)",
        "bibliography": "Campbell, Lyle. El Idioma Pipil (Nahuate) de El Salvador. Mouton, 1985.",
    },
    {
        "id": "hernandez_2019",
        "name": "Hernández, Werner (2019)",
        "bibliography": "Hernández, Werner. Nawat Mujmusta. Secretaría de Cultura de San Salvador, Editorial Municipal, 2019.",
    },
    {
        "id": "king_lbn_2011",
        "name": "King, Alan R. (2011)",
        "bibliography": "King, Alan R. Léxico del Náhuat Básico. 2011.",
    },
    {
        "id": "ramirez_2004",
        "name": "Ramírez, Genaro (2004)",
        "bibliography": "Ramírez, Genaro. Naja Ni Genaro. IRIN (Iniciativa para la Recuperación del Idioma Náhuat), 2004.",
    },
    {
        "id": "schultze_jena_1935",
        "name": "Schultze Jena, Leonhard (1935)",
        "bibliography": "Schultze Jena, Leonhard. Mythen in der Muttersprache der Pipil von Izalco. Jena, Alemania: Verlag von Gustav Fischer, 1935.",
    },
    {
        "id": "todd_1953",
        "name": "Todd, Juan (1953)",
        "bibliography": 'Todd, Juan. Notas del náhuat de Nahuizalco. San Salvador: Editorial "Nosotros", 1953.',
    },
]

# Map input file base-names to a source_id from the catalog above
FILE_TO_SOURCE_ID: Dict[str, str] = {
    "Arauz.txt": "arauz_1960",
    "Campbell Dictionary.txt": "campbell_1985",
    "Hernandez.txt": "hernandez_2019",
    "LBN.txt": "king_lbn_2011",
    "Ramirez.txt": "ramirez_2004",
    "Schultze.txt": "schultze_jena_1935",
    "Todd.txt": "todd_1953",
    # If you later add a file for the NT lexicon, map it here:
    # "King NT Lexicon.txt": "king_nt_2013",
}


# ---- Marker definitions (optional; used when --include-marker-defs) ----
MARKER_DEFS: Dict[str, Dict[str, Any]] = {
    "an": {"db_name": "antonym", "title": "Antonym"},
    "be": {"db_name": "subentry_gloss_en", "title": "Subentry Gloss (English)"},
    "bt": {"db_name": "morphology_note", "title": "Morphology Note"},
    "bw": {"db_name": "borrowed_word_language", "title": "Borrowed Word Language"},
    "cf": {"db_name": "cross_reference", "title": "Cross Reference"},
    "de": {"db_name": "definition_en", "title": "Definition (English)"},
    "di": {"db_name": "dialect_code", "title": "Dialect"},
    "dm": {"db_name": "diminutive", "title": "Diminutive"},
    "dn": {"db_name": "definition_es", "title": "Definition (Spanish)"},
    "dt": {"db_name": "date", "title": "Date"},
    "ec": {"db_name": "etymology_comment", "title": "Etymology Comment"},
    "ee": {"db_name": "encyclopedic_info_en", "title": "Encyclopedic Info (English)"},
    "en": {"db_name": "encyclopedic_info_es", "title": "Encyclopedic Info (Spanish)"},
    "et": {"db_name": "etymology_es", "title": "Etymology (Spanish)"},
    "fm": {"db_name": "morphological_function", "title": "Morphological Function"},
    "fr": {"db_name": "form", "title": "Form"},
    "gd": {"db_name": "gloss_de", "title": "Gloss/Definition (German)"},
    "ge": {"db_name": "gloss_en", "title": "Gloss (English)"},
    "gn": {"db_name": "gloss_es", "title": "Gloss (Spanish)"},
    "gr": {"db_name": "grammatical_info", "title": "Grammatical Info"},
    "hm": {"db_name": "homonym_number", "title": "Homonym Number"},
    "if": {"db_name": "inflection", "title": "Inflection"},
    "in": {"db_name": "inflection_gloss_es", "title": "Inflection Gloss (Spanish)"},
    "lx": {"db_name": "lexeme", "title": "Lexeme"},
    "lz": {"db_name": "source_spelling", "title": "Source Spelling"},
    "mn": {"db_name": "main_entry_reference", "title": "Main Entry Reference"},
    "nd": {"db_name": "note_discourse", "title": "Note (Discourse)"},
    "ng": {"db_name": "note_grammar", "title": "Note (Grammar)"},
    "nt": {"db_name": "note", "title": "Note"},
    "pd": {"db_name": "paradigm_note", "title": "Paradigm Note"},
    "pdl": {"db_name": "paradigm_label", "title": "Paradigm Label"},
    "pdv": {"db_name": "paradigm_value", "title": "Paradigm Value"},
    "ph": {"db_name": "phonetic_form", "title": "Phonetic Form"},
    "pl": {"db_name": "plural_form", "title": "Plural Form"},
    "po": {"db_name": "possessed_form", "title": "Possessed Form"},
    "ps": {"db_name": "part_of_speech", "title": "Part of Speech"},
    "re": {"db_name": "reversal_en", "title": "Reversal (English)"},
    "ref": {"db_name": "reference", "title": "Reference"},
    "rn": {"db_name": "reversal_es", "title": "Reversal (Spanish)"},
    "sc": {"db_name": "scientific_name", "title": "Scientific Name"},
    "se": {"db_name": "subentry", "title": "Subentry"},
    "sg": {"db_name": "singular_form", "title": "Singular Form"},
    "sn": {"db_name": "sense_number", "title": "Sense Number"},
    "so": {"db_name": "source_code", "title": "Source Code"},
    "st": {"db_name": "status", "title": "Status"},
    "sy": {"db_name": "synonym", "title": "Synonym"},
    "tx": {"db_name": "text_example", "title": "Text Example"},
    "ue": {"db_name": "usage_en", "title": "Usage (English)"},
    "un": {"db_name": "usage_es", "title": "Usage (Spanish)"},
    "uv": {"db_name": "usage_nv", "title": "Usage (Nawat)"},
    "va": {"db_name": "variant_form", "title": "Variant Form"},
    "vx": {"db_name": "extra_wordform", "title": "Extra Wordform"},
    "we": {"db_name": "literal_gloss_en", "title": "Literal Gloss (English)"},
    "wv": {"db_name": "phrase_variant", "title": "Phrase Variant"},
    "xe": {"db_name": "example_en", "title": "Example (English)"},
    "xn": {"db_name": "example_es", "title": "Example (Spanish)"},
    "xv": {"db_name": "example_nv", "title": "Example (Nawat)"},
    "_sh": {"db_name": "sfm_header", "title": "SFM Header"},
}

# Optional: IF submarkers
IF_SUBMARKERS = {
    "if-prefix": ("inflection_prefix", "Inflection: Prefix"),
    "if-pt": ("inflection_preterite", "Inflection: Preterite"),
    "if-ir": ("inflection_imperative", "Inflection: Imperative"),
    "if-pf": ("inflection_perfect", "Inflection: Perfect"),
    "if-fut": ("inflection_future", "Inflection: Future"),
    "if-rfl": ("inflection_reflexive", "Inflection: Reflexive"),
    "if-em": ("inflection_emphatic", "Inflection: Emphatic"),
    "if-inac": ("inflection_inaccusative", "Inflection: Inaccusative"),
    "if-pp": ("inflection_past_perfect", "Inflection: Past Perfect"),
    "if-iter": ("inflection_iterative", "Inflection: Iterative"),
    "if-tuya": ("inflection_tuya", "Inflection: -tuya"),
    "if-dir": ("inflection_directional", "Inflection: Directional"),
    "if-uk": ("inflection_uk", "Inflection: -uk"),
    "if-app": ("inflection_applicative", "Inflection: Applicative"),
    "if-comb": ("inflection_combining_form", "Inflection: Combining Form"),
    "if-san": ("inflection_san", "Inflection: -san"),
}
for k, (dbn, title) in IF_SUBMARKERS.items():
    MARKER_DEFS.setdefault(k, {"db_name": dbn, "title": title})


def decode_bytes(data: bytes) -> Tuple[str, str]:
    """
    Robust decoding for files that may be mixed-encoding by line:
      - Try to decode each line as UTF-8 (keeps Greek/Hebrew).
      - If that fails for that line, decode it as cp1252 (fixes Spanish ñ/á/é/ó/ú, etc.).
      - If that still fails, fallback to latin-1 with replacement.

    Returns (full_text, label_for_debugging).
    """
    lines = data.splitlines(keepends=False)

    # strip UTF-8 BOM if present on first line
    if lines and lines[0].startswith(b"\xef\xbb\xbf"):
        lines[0] = lines[0][3:]

    out_lines: List[str] = []
    for bline in lines:
        if bline == b"":
            out_lines.append("")
            continue

        try:
            out_lines.append(bline.decode("utf-8"))
            continue
        except UnicodeDecodeError:
            pass

        try:
            out_lines.append(bline.decode("cp1252"))
            continue
        except UnicodeDecodeError:
            out_lines.append(bline.decode("latin-1", errors="replace"))

    return "\n".join(out_lines), "per-line: utf-8 else cp1252 else latin-1"


def iter_files(inputs: List[Path], glob_pattern: str) -> List[Path]:
    files: List[Path] = []
    for p in inputs:
        if p.is_dir():
            files.extend(sorted(p.glob(glob_pattern)))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"Path not found: {p}")

    seen = set()
    unique: List[Path] = []
    for f in files:
        if f not in seen:
            unique.append(f)
            seen.add(f)
    return unique


def split_marker(marker: str) -> Tuple[str, Optional[str]]:
    if "-" not in marker:
        return marker, None
    root, rest = marker.split("-", 1)
    return root, rest


@dataclass
class Record:
    items: List[Dict[str, Any]]
    fields: Dict[str, List[str]]
    record_index: int

    @property
    def headword(self) -> Optional[str]:
        v = self.fields.get("lx")
        return v[0] if v else None


def finalize_record(rec: Record, out_list: List[Dict[str, Any]]) -> None:
    if not rec.items and not rec.fields:
        return
    out_list.append(
        {
            "record_index": rec.record_index,
            "headword": rec.headword,
            "fields": rec.fields,
            "items": rec.items,  # preserves order + duplicates
        }
    )


def parse_sfm(text: str) -> List[Dict[str, Any]]:
    """
    Parse an SFM file into a list of records.
    Records are split on \\lx boundaries.
    """
    records_out: List[Dict[str, Any]] = []

    current_items: List[Dict[str, Any]] = []
    current_fields: Dict[str, List[str]] = defaultdict(list)
    current_record_has_lx = False
    last_item: Optional[Dict[str, Any]] = None

    rec_idx = 1

    def flush():
        nonlocal current_items, current_fields, current_record_has_lx, last_item, rec_idx
        finalize_record(Record(current_items, dict(current_fields), rec_idx), records_out)
        rec_idx += 1
        current_items = []
        current_fields = defaultdict(list)
        current_record_has_lx = False
        last_item = None

    lines = text.splitlines()

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")

        m = MARKER_LINE_RE.match(line)
        if m:
            marker = m.group(1)
            value = m.group(2).rstrip()

            # New record if we hit a new \\lx and we already have an \\lx in the current record
            if marker == "lx" and current_record_has_lx:
                flush()

            if marker == "lx":
                current_record_has_lx = True

            root, subpath = split_marker(marker)
            item = {"marker": marker, "root": root, "subpath": subpath, "value": value}

            current_items.append(item)
            current_fields[marker].append(value)
            last_item = item
            continue

        # Continuation line
        if last_item is None:
            continue

        # Blank lines: preserve only if the next line is NOT a new marker (i.e., likely part of a note/text)
        if line.strip() == "":
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            if MARKER_LINE_RE.match(next_line.rstrip("\r\n")):
                # separator between markers/entries
                continue
            # meaningful blank line inside value
            last_item["value"] = last_item["value"] + "\n" if last_item["value"] else ""
            mk = last_item["marker"]
            current_fields[mk][-1] = last_item["value"]
            continue

        cont = line.rstrip()
        last_item["value"] = (last_item["value"] + "\n" + cont) if last_item["value"] else cont
        mk = last_item["marker"]
        current_fields[mk][-1] = last_item["value"]

    flush()
    return records_out


def build_marker_catalog(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    used = set()
    for r in records:
        for it in r.get("items", []):
            used.add(it["marker"])
    catalog: Dict[str, Dict[str, Any]] = {}
    for mk in sorted(used):
        meta = MARKER_DEFS.get(mk)
        if meta:
            catalog[mk] = {"code": mk, **meta}
        else:
            catalog[mk] = {"code": mk, "db_name": mk, "title": mk, "unknown": True}
    return catalog


def strip_marker_from_records(records: List[Dict[str, Any]], marker: str) -> None:
    for r in records:
        if "fields" in r and marker in r["fields"]:
            del r["fields"][marker]
        if "items" in r:
            r["items"] = [it for it in r["items"] if it.get("marker") != marker]


def extract_marker_values(records: List[Dict[str, Any]], marker: str) -> List[str]:
    vals: List[str] = []
    for r in records:
        mvals = r.get("fields", {}).get(marker)
        if mvals:
            vals.extend(mvals)
    return vals


def source_id_for_file(file_name: str) -> str:
    return FILE_TO_SOURCE_ID.get(file_name, file_name.replace(".txt", "").strip().lower().replace(" ", "_"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Convert SFM/MDF .txt lexicon files to JSON (lossless).")
    ap.add_argument("paths", nargs="+", help="File(s) and/or directory(ies) to scan.")
    ap.add_argument("--glob", default="*.txt", help='Glob for directories (default: "*.txt").')

    out_group = ap.add_mutually_exclusive_group(required=True)
    out_group.add_argument("--out", help="Write one combined JSON file to this path.")
    out_group.add_argument("--out-dir", help="Write JSON files into this directory.")

    ap.add_argument("--per-file", action="store_true", help="(With --out-dir) write one JSON per input .txt file.")
    ap.add_argument("--pretty", action="store_true", help="Pretty-print JSON (larger files).")
    ap.add_argument("--include-marker-defs", action="store_true", help="Include marker definitions in output.")

    # Metadata controls
    ap.add_argument(
        "--minimal",
        action="store_true",
        help="GitHub-friendly output: removes local paths + generated_at/files_scanned/file_name/encoding.",
    )
    ap.add_argument(
        "--include-encoding",
        action="store_true",
        help="(Useful for debugging) Include the detected encoding in output even in --minimal mode.",
    )

    # _sh handling
    ap.add_argument(
        "--extract-sh",
        action="store_true",
        help="Treat \\_sh as file-level metadata (remove it from records).",
    )
    ap.add_argument(
        "--drop-sh",
        action="store_true",
        help="Drop \\_sh entirely (remove it from records and do not store it).",
    )

    args = ap.parse_args()

    input_paths = [Path(p).expanduser() for p in args.paths]
    files = iter_files(input_paths, args.glob)
    if not files:
        raise SystemExit("No files found.")

    # Combined output
    if args.out:
        lexicons: List[Dict[str, Any]] = []
        global_used_markers = set()

        for f in files:
            data = f.read_bytes()
            text, enc = decode_bytes(data)
            records = parse_sfm(text)

            file_metadata: Dict[str, Any] = {}

            if args.drop_sh or args.extract_sh:
                sh_values = extract_marker_values(records, "_sh")
                strip_marker_from_records(records, "_sh")
                if args.extract_sh and (not args.drop_sh) and sh_values:
                    file_metadata["_sh"] = sh_values[0] if len(sh_values) == 1 else sh_values

            for r in records:
                for it in r.get("items", []):
                    global_used_markers.add(it["marker"])

            sid = source_id_for_file(f.name)

            lex_obj: Dict[str, Any] = {
                "source_id": sid,
                "records": records,
            }

            # Only include extra metadata when NOT minimal (or explicitly requested)
            if not args.minimal:
                lex_obj["file_name"] = f.name
                lex_obj["file"] = str(f)
                lex_obj["encoding"] = enc
                if file_metadata:
                    lex_obj["file_metadata"] = file_metadata
            else:
                if args.include_encoding:
                    lex_obj["encoding"] = enc
                # generally you don't want _sh at all; only include if you asked for extract-sh
                if args.extract_sh and (not args.drop_sh) and file_metadata:
                    lex_obj["file_metadata"] = file_metadata

            lexicons.append(lex_obj)

        out_obj: Dict[str, Any] = {
            # Always include bibliography catalog (useful for your website)
            "sources": SOURCES_CATALOG,
            "lexicons": lexicons,
        }

        if args.include_marker_defs:
            marker_defs = {}
            for mk in sorted(global_used_markers):
                meta = MARKER_DEFS.get(mk)
                marker_defs[mk] = {"code": mk, **(meta or {"db_name": mk, "title": mk, "unknown": True})}
            out_obj["marker_definitions"] = marker_defs

        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fp:
            json.dump(out_obj, fp, ensure_ascii=False, indent=2 if args.pretty else None)
        return

    # Per-file output
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.per_file:
        raise SystemExit(
            "You used --out-dir without --per-file.\n"
            "Either:\n"
            "  (1) use --out to write one combined JSON file, OR\n"
            "  (2) add --per-file to write one JSON per input .txt file."
        )

    for f in files:
        data = f.read_bytes()
        text, enc = decode_bytes(data)
        records = parse_sfm(text)

        file_metadata: Dict[str, Any] = {}

        if args.drop_sh or args.extract_sh:
            sh_values = extract_marker_values(records, "_sh")
            strip_marker_from_records(records, "_sh")
            if args.extract_sh and (not args.drop_sh) and sh_values:
                file_metadata["_sh"] = sh_values[0] if len(sh_values) == 1 else sh_values

        sid = source_id_for_file(f.name)

        out_obj: Dict[str, Any] = {
            "sources": SOURCES_CATALOG,
            "source_id": sid,
            "records": records,
        }

        if args.include_marker_defs:
            out_obj["marker_definitions"] = build_marker_catalog(records)

        if not args.minimal:
            out_obj["file_name"] = f.name
            out_obj["file"] = str(f)
            out_obj["encoding"] = enc
            if file_metadata:
                out_obj["file_metadata"] = file_metadata
        else:
            if args.include_encoding:
                out_obj["encoding"] = enc
            if args.extract_sh and (not args.drop_sh) and file_metadata:
                out_obj["file_metadata"] = file_metadata

        out_path = out_dir / (f.stem + ".json")
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(out_obj, fp, ensure_ascii=False, indent=2 if args.pretty else None)


if __name__ == "__main__":
    main()
