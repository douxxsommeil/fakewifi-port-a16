#!/usr/bin/env python3
"""Diff two tree manifests produced by tree_manifest.py.

Exit code 0 always; prints a report. The caller parses the report.
"""
import sys

IDX = {
    "file": {"hash": 2, "mode": 3, "uid": 4, "gid": 5, "size": 6, "selinux": 7, "caps": 8},
    "link": {"target": 2, "uid": 3, "gid": 4},
    "dir": {"mode": 2, "uid": 3, "gid": 4, "selinux": 5},
    "special": {"mode": 2},
}


def load(path):
    d = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            d[parts[0]] = parts[1:]
    return d


def main():
    A, B = load(sys.argv[1]), load(sys.argv[2])
    onlyA = sorted(set(A) - set(B))
    onlyB = sorted(set(B) - set(A))
    common = set(A) & set(B)
    content_diff, meta_diff = [], []
    for rel in sorted(common):
        a, b = A[rel], B[rel]
        if a[0] != b[0]:
            content_diff.append((rel, " ".join(a[:3]), " ".join(b[:3])))
            continue
        kind = a[0]
        keys = IDX.get(kind, {})
        if kind == "file" and a[2] != b[2]:
            content_diff.append((rel, a[2][:16], b[2][:16]))
            continue
        if kind == "link" and a[2] != b[2]:
            content_diff.append((rel, a[2], b[2]))
            continue
        diffs = [f"{k}:{a[i]}!={b[i]}" for k, i in keys.items() if len(a) > i and len(b) > i and a[i] != b[i]]
        if diffs:
            meta_diff.append((rel, ", ".join(diffs)))
    print(f"A={len(A)} B={len(B)} common={len(common)}")
    print(f"CONTENT diffs: {len(content_diff)}")
    for rel, x, y in content_diff[:400]:
        print(f"  C {rel}  [{x}] -> [{y}]")
    print(f"META diffs: {len(meta_diff)}")
    for rel, d in meta_diff[:100]:
        print(f"  M {rel}  {d}")
    if onlyA:
        print(f"ONLY in A ({len(onlyA)}): " + ", ".join(onlyA[:60]))
    if onlyB:
        print(f"ONLY in B ({len(onlyB)}): " + ", ".join(onlyB[:60]))


if __name__ == "__main__":
    main()
