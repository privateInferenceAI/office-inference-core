#!/usr/bin/env python3
"""Sort a corpus into a ladder of test groups, by chunk budget rather than by file count.

**Why a budget and not a percentage.** A percentage of *files* says nothing about the work: this corpus
averages 487 KB a file with a 24 KB median and a 1.6 GB maximum, so a 2.5% slice of files can be a three-day
ingest. Chunks are what get embedded, stored and searched, so chunks are what a group is sized in.

**Why the groups are a sample and not a partition of the whole corpus.** Nineteen divisions of 39 million
chunks leaves a smallest group of a million, which is four days. No arrangement of nineteen parts makes a
small part small. So the ladder covers a testable range and the rest is archived: a partition would commit
every one of those chunks to a scheme chosen before any of it had been tried.

**Why the large files are sized to their group.** One monster in every group collapses the ladder -- the
1.6 GB text file is 362,000 chunks, which is seventy times group 2's whole budget, so groups 2 through 10
would be the same file with a rounding error of small ones attached and would stop being different sizes.
Each group instead gets one file worth 10 to 20% of *its own* budget, which tests "large relative to its
context" at every scale, and the true monsters get their own group so a failure can be attributed.

**Files are moved, not copied.** A move is a rename, so it costs nothing and the corpus is not duplicated;
a hardlink would look cheaper and would be worse, because deleting the original then frees no space while
the group still references the inode.

    python3 scripts/build_corpus_groups.py --corpus ~/corpora --out ~/corpus-groups
    python3 scripts/build_corpus_groups.py --corpus ~/corpora --out ~/corpus-groups --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ingestion"))
import formats  # noqa: E402

#: Chars of extractable text a byte of each format yields. The `.xls` figure is measured: a 7.9 MB workbook
#: returned 5.18 million characters, which is 0.66 of its bytes, but that workbook is a dense table and most
#: are not, so the figure is deliberately conservative.
DENSITY = {"txt": 1.00, "csv": 0.90, "rtf": 0.55, "docx": 0.60, "xlsx": 0.40, "pptx": 0.40,
           "doc": 0.35, "html": 0.30, "htm": 0.30, "xls": 0.25, "pdf": 0.50, "eml": 0.80}
DEFAULT_DENSITY = 0.50

#: Characters in a filled chunk. The chunker targets 384 to 512 tokens, so 1,500 to 3,000 characters; a
#: small file does not fill one and a large one does, which is why every estimate here is a range and the
#: groups are sized to the pessimistic end.
CHARS_PER_CHUNK = 2250

#: The ladder. Ten small steps, five medium, three large, and one for the monsters.
BUDGETS = [5_000, 10_000, 15_000, 20_000, 25_000, 30_000, 35_000, 40_000, 45_000, 50_000,
           75_000, 100_000, 150_000, 200_000, 250_000,
           400_000, 600_000, 800_000,
           1_500_000]

#: A large file is one over this, and it is where a group's proportional "large" file is drawn from.
LARGE_BYTES = 10_000_000

#: Group 1 is small files only, so a failure there is unambiguous. Groups 2 to 18 each get one file sized to
#: 10 to 20% of their budget. Group 19 is the monsters and nothing else.
FIRST_GROUP_WITH_A_LARGE_FILE = 2
MONSTER_GROUP = 19
LARGE_SHARE = (0.10, 0.20)


def chunks_of(path: Path) -> float:
    """Estimated chunks, to the pessimistic end of the range."""
    ext = path.suffix.lower().lstrip(".")
    return path.stat().st_size * DENSITY.get(ext, DEFAULT_DENSITY) / CHARS_PER_CHUNK


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="where the files are now")
    parser.add_argument("--out", type=Path, required=True, help="where the groups go")
    parser.add_argument("--seed", default="office-inference-corpus",
                        help="fixes the assignment, so the same corpus gives the same groups")
    parser.add_argument("--dry-run", action="store_true", help="report the plan and move nothing")
    args = parser.parse_args(argv)

    files = [p for p in sorted(args.corpus.rglob("*"))
             if p.is_file() and p.suffix.lower() in formats.ACCEPTED_SUFFIXES]
    if not files:
        parser.error(f"no accepted files under {args.corpus}")
    print(f"  {len(files):,} files, {sum(p.stat().st_size for p in files)/1e9:.1f} GB")

    # Names are flattened into one folder per group, so a collision would silently lose a file. Checked
    # rather than assumed: the source uses numbered subfolders and could in principle repeat a name.
    names: dict[str, list[Path]] = {}
    for p in files:
        names.setdefault(p.name, []).append(p)
    collisions = {n: v for n, v in names.items() if len(v) > 1}
    if collisions:
        print(f"  ** {len(collisions)} duplicate basename(s) cannot be flattened:", file=sys.stderr)
        for n, v in list(collisions.items())[:5]:
            print(f"       {n}  in {len(v)} places: {[str(x) for x in v[:3]]}", file=sys.stderr)
        return 2
    print(f"  {len(names):,} distinct names, so flattening cannot lose a file")

    rng = random.Random(args.seed)
    big = sorted((p for p in files if p.stat().st_size > LARGE_BYTES), key=lambda p: p.stat().st_size)
    small = [p for p in files if p.stat().st_size <= LARGE_BYTES]
    rng.shuffle(small)

    groups: dict[int, list[Path]] = {n: [] for n in range(1, len(BUDGETS) + 1)}
    remaining = list(small)

    for index, budget in enumerate(BUDGETS, start=1):
        if index == MONSTER_GROUP:
            continue
        target = budget
        # The proportional large file first, so it is inside the budget rather than on top of it.
        if index >= FIRST_GROUP_WITH_A_LARGE_FILE:
            share = rng.uniform(*LARGE_SHARE) * budget * CHARS_PER_CHUNK
            candidates = [p for p in big if p.stat().st_size * DEFAULT_DENSITY <= share]
            if candidates:
                chosen = min(candidates, key=lambda p: abs(p.stat().st_size * DEFAULT_DENSITY - share))
                groups[index].append(chosen)
                big.remove(chosen)
                target -= chunks_of(chosen)
        filled = 0.0
        while remaining and filled < target:
            candidate = remaining.pop()
            size = chunks_of(candidate)
            # Never overshoot by more than a tenth: a single huge file landing in a small group is the
            # failure this whole design is against.
            if filled + size > target * 1.1 and groups[index]:
                remaining.insert(0, candidate)
                break
            groups[index].append(candidate)
            filled += size
        if not groups[index]:
            print(f"  ** group {index} came out empty", file=sys.stderr)

    # The monsters, **filled to their budget like every other group and not to a file count.** The first
    # version took the largest four hundred, which is 33.7 GB and 12.5 million chunks against a budget of
    # 1.5 million -- eight times its own target, and the largest group in the ladder by a factor nobody
    # chose. A count of files says nothing about the work, which is the whole reason the ladder is measured
    # in chunks.
    monster_budget = BUDGETS[MONSTER_GROUP - 1]
    groups[MONSTER_GROUP] = []
    monster_filled = 0.0
    # **Walked with a stride, not taken from the top.** Taking the largest first gave a group of two files:
    # the 1.6 GB and 1.5 GB text files are 1.4 million chunks between them, so they consume the entire
    # budget and the group stops being a sample of large files and becomes a sample of those two. A stride
    # spans the range instead, so the group holds a spread from a few megabytes to the largest file there
    # is, which is what a test for "does a big document break this" actually wants.
    ordered = sorted(big, key=lambda p: p.stat().st_size, reverse=True)
    stride = max(1, len(ordered) // 220)
    for candidate in ordered[::stride]:
        size = chunks_of(candidate)
        if monster_filled + size > monster_budget * 1.05 and groups[MONSTER_GROUP]:
            break
        groups[MONSTER_GROUP].append(candidate)
        monster_filled += size
    groups[MONSTER_GROUP].sort(key=lambda p: p.stat().st_size)
    for p in groups[MONSTER_GROUP]:
        if p in big:
            big.remove(p)
        if p in remaining:
            remaining.remove(p)
    # **Every large file nobody picked goes to the remainder, and the first version lost them.** The
    # remainder was built from the leftover *small* files only, so 1,577 files over ten megabytes -- 47 GB --
    # were assigned to no group and left sitting in the source folder. The run reported success and moved
    # 236,519 of 238,095 files. A file in no group is a file that gets missed, and the count that would have
    # caught it is the one nobody adds up by hand.
    remaining.extend(big)
    if remaining:
        groups[20] = remaining
    assert sum(len(v) for v in groups.values()) == len(files), (
        f"assignment lost files: {sum(len(v) for v in groups.values())} of {len(files)}")

    print()
    print(f"  {'group':<9}{'files':>9}{'size':>9}{'chunks':>12}  largest file")
    print("  " + "-" * 62)
    for n in sorted(groups):
        members = groups[n]
        if not members:
            continue
        size = sum(p.stat().st_size for p in members)
        chunks = sum(chunks_of(p) for p in members)
        biggest = max(members, key=lambda p: p.stat().st_size)
        label = f"group {n:02d}" if n <= 19 else "remainder"
        print(f"  {label:<9}{len(members):>9,}{size/1e9:>8.2f}G{chunks:>12,.0f}  "
              f"{biggest.stat().st_size/1e6:.0f} MB {biggest.name}")

    if args.dry_run:
        print("\n  dry run: nothing moved")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    moved = 0
    for n in sorted(groups):
        if not groups[n]:
            continue
        folder = args.out / (f"group-{n:02d}" if n <= 19 else "remainder")
        folder.mkdir(parents=True, exist_ok=True)
        manifest = {"group": n, "target_chunks": BUDGETS[n - 1] if n <= 19 else None,
                    "files": [], "moved_from": str(args.corpus)}
        for p in groups[n]:
            size = p.stat().st_size
            digest = hashlib.sha256(p.read_bytes()).hexdigest() if size < 50_000_000 else None
            shutil.move(str(p), str(folder / p.name))
            moved += 1
            manifest["files"].append({"name": p.name, "bytes": size, "sha256": digest,
                                      "original": str(p.relative_to(args.corpus))})
        manifest["count"] = len(manifest["files"])
        manifest["bytes"] = sum(f["bytes"] for f in manifest["files"])
        (args.out / f"manifest-{folder.name}.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"  {folder.name}: {manifest['count']:,} files, {manifest['bytes']/1e9:.2f} GB")

    print(f"\n  moved {moved:,} files into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
