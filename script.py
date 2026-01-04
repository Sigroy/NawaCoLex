#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Tuple, Any

# IMPORTANT: allow hyphens in marker names (e.g. if-prefix, if-pt)
MARKER_LINE_RE = re.compile(r"^\\([A-Za-z0-9_][A-Za-z0-9_-]*)\s*(.*)$")


def decode_bytes(data: bytes) -> Tuple[str, str]:
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1", errors="replace"), "latin-1(replace)"


def iter_files(inputs: List[Path], glob_pattern: str) -> List[Path]:
    files: List[Path] = []
    for p in inputs:
        if p.is_dir():
            files.extend(sorted(p.glob(glob_pattern)))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"Path not found: {p}")

    # De-dup while preserving order
    seen = set()
    unique: List[Path] = []
    for f in files:
        if f not in seen:
            unique.append(f)
            seen.add(f)
    return unique


def process_text_for_counts_and_samples(
    text: str,
    file_path: str,
    samples_per_marker: int,
) -> Tuple[Counter, Dict[str, List[Dict[str, Any]]]]:
    counts = Counter()
    samples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    current_lx = None
    current_se = None

    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.rstrip("\n\r")
        m = MARKER_LINE_RE.match(line)
        if not m:
            continue

        marker = m.group(1)          # now includes hyphens like if-prefix
        value = m.group(2).strip()

        counts[marker] += 1

        # Track context
        if marker == "lx":
            current_lx = value or None
            current_se = None
        elif marker == "se":
            current_se = value or None

        # Save sample if requested
        if samples_per_marker > 0 and len(samples[marker]) < samples_per_marker:
            samples[marker].append(
                {
                    "file": file_path,
                    "line": line_no,
                    "lx": current_lx,
                    "se": current_se,
                    "value": value,
                    "raw": line,
                }
            )

    return counts, samples


def split_root(marker: str) -> Tuple[str, str | None]:
    """
    Returns (root, suffix) where:
      - root is the part before the first '-'
      - suffix is everything after the first '-', or None if no '-'
    Examples:
      "if" -> ("if", None)
      "if-prefix" -> ("if", "prefix")
      "if-prefix-x" -> ("if", "prefix-x")
    """
    if "-" not in marker:
        return marker, None
    root, rest = marker.split("-", 1)
    return root, rest


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract SFM/MDF markers from .txt files.")
    ap.add_argument("paths", nargs="+", help="File(s) and/or directory(ies) to scan.")
    ap.add_argument("--glob", default="*.txt", help='Glob for directories (default: "*.txt").')
    ap.add_argument("--counts", action="store_true", help="Show counts per marker (full marker names).")
    ap.add_argument("--per-file", action="store_true", help="Also show markers per file.")
    ap.add_argument("--json", action="store_true", help="Output as JSON.")
    ap.add_argument("--samples", type=int, default=0, help="Store up to N sample occurrences per marker.")
    ap.add_argument(
        "--rare-threshold",
        type=int,
        default=0,
        help="If >0, print full samples only for markers whose TOTAL count <= threshold (requires --samples).",
    )
    ap.add_argument(
        "--where",
        metavar="MARKER",
        help="Show which files contain a given marker (exact match). Example: --where if-prefix",
    )
    ap.add_argument(
        "--where-family",
        metavar="ROOT",
        help="Show which files contain ROOT or any ROOT-* marker. Example: --where-family if",
    )
    ap.add_argument(
        "--where-all",
        action="store_true",
        help="Show which files contain each marker (can be verbose).",
    )

    # NEW: submarker reporting
    ap.add_argument(
        "--submarkers",
        action="store_true",
        help="Show marker families like if-* and their counts.",
    )
    ap.add_argument(
        "--submarker-of",
        metavar="ROOT",
        help="Show submarkers only for a given root marker (e.g., if). Implies --submarkers.",
    )

    args = ap.parse_args()

    input_paths = [Path(p).expanduser() for p in args.paths]
    files = iter_files(input_paths, args.glob)

    total = Counter()
    per_file: Dict[str, Counter] = defaultdict(Counter)
    encodings = {}
    samples_total: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for f in files:
        data = f.read_bytes()
        text, enc = decode_bytes(data)
        encodings[str(f)] = enc

        c, samples = process_text_for_counts_and_samples(text, str(f), args.samples)
        total.update(c)
        per_file[str(f)] = c

        for mk, items in samples.items():
            samples_total[mk].extend(items)
            if args.samples > 0:
                samples_total[mk] = samples_total[mk][: args.samples]

    markers_sorted = sorted(total.keys())

    # marker -> list of (file, count_in_file)
    marker_files: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for fname, ctr in per_file.items():
        for mk, ct in ctr.items():
            marker_files[mk].append((fname, ct))
    for mk in marker_files:
        marker_files[mk].sort(key=lambda t: (-t[1], t[0]))

    # Build family/submarker stats (root -> suffix -> count)
    root_counts = Counter()
    family_counts: Dict[str, Counter] = defaultdict(Counter)
    for mk, ct in total.items():
        root, suffix = split_root(mk)
        root_counts[root] += ct
        if suffix is not None:
            family_counts[root][suffix] += ct

    if args.json:
        out = {
            "files_scanned": [str(f) for f in files],
            "encodings": encodings,
            "markers": markers_sorted,
            "counts": dict(total) if args.counts else None,
            "per_file": {k: dict(v) for k, v in per_file.items()} if args.per_file else None,
            "samples": samples_total if args.samples > 0 else None,
            "marker_files": {mk: marker_files[mk] for mk in marker_files} if (args.where_all or args.where or args.where_family) else None,
            "root_counts": dict(root_counts) if (args.submarkers or args.submarker_of) else None,
            "family_counts": {r: dict(c) for r, c in family_counts.items()} if (args.submarkers or args.submarker_of) else None,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    # WHERE (exact)
    if args.where:
        mk = args.where.strip()
        if mk not in marker_files:
            print(f"Marker '{mk}' not found in any scanned file.")
            return
        print(f"Marker '{mk}' appears in {len(marker_files[mk])} file(s):")
        for fname, ct in marker_files[mk]:
            print(f"  {ct}\t{fname}")
        return

    # WHERE FAMILY (root or root-*)
    if args.where_family:
        root = args.where_family.strip()
        hits: List[Tuple[str, int]] = []
        for fname, ctr in per_file.items():
            ct = 0
            for mk, n in ctr.items():
                if mk == root or mk.startswith(root + "-"):
                    ct += n
            if ct > 0:
                hits.append((fname, ct))
        hits.sort(key=lambda t: (-t[1], t[0]))

        if not hits:
            print(f"No markers found for family '{root}' (neither '{root}' nor '{root}-*').")
            return

        print(f"Family '{root}' (matches '{root}' and '{root}-*') appears in {len(hits)} file(s):")
        for fname, ct in hits:
            print(f"  {ct}\t{fname}")
        return

    # SUBMARKERS REPORT
    if args.submarker_of:
        args.submarkers = True

    if args.submarkers:
        roots = [args.submarker_of] if args.submarker_of else sorted(family_counts.keys())
        print("--- Marker families (root -> submarkers) ---")
        for root in roots:
            if root is None:
                continue
            root = root.strip()
            if root not in root_counts:
                print(f"\n{root}: (not found)")
                continue

            plain_ct = total.get(root, 0)
            fam_total = root_counts[root]
            print(f"\n{root} (family total {fam_total}; plain '{root}' = {plain_ct})")

            subs = family_counts.get(root)
            if not subs:
                print("  (no submarkers)")
                continue

            for suffix in sorted(subs.keys(), key=lambda s: (-subs[s], s)):
                print(f"  {root}-{suffix}\t{subs[suffix]}")
        return

    if args.where_all:
        print("--- Marker -> files ---")
        for mk in sorted(marker_files.keys()):
            files_list = ", ".join([f"{ct}× {Path(fname).name}" for fname, ct in marker_files[mk]])
            print(f"{mk}: {files_list}")

    print(f"Files scanned: {len(files)}")

    if args.counts:
        for mk in sorted(total.keys(), key=lambda k: (-total[k], k)):
            print(f"{mk}\t{total[mk]}")
    else:
        print("Markers:")
        for mk in markers_sorted:
            print(mk)

    if args.per_file:
        print("\n--- Per file ---")
        for fname in sorted(per_file.keys()):
            mks = sorted(per_file[fname].keys())
            print(f"\n{fname}")
            print("  " + ", ".join(mks))

    if args.samples > 0 and args.rare_threshold > 0:
        print(f"\n--- Rare markers (total <= {args.rare_threshold}) with samples ---")
        rare = [mk for mk, ct in total.items() if ct <= args.rare_threshold]
        for mk in sorted(rare, key=lambda m: (total[m], m)):
            print(f"\n{mk} (total {total[mk]})")
            for s in samples_total.get(mk, []):
                print(f"  {s['file']}:{s['line']}  lx={s['lx']}  se={s['se']}  value={s['value']}")


if __name__ == "__main__":
    main()
