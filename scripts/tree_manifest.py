#!/usr/bin/env python3
"""Snapshot an extracted EROFS tree into a manifest TSV (content+meta+xattrs).

Usage: tree_manifest.py <tree> <out.tsv>
Line format (TAB separated):
  <relpath>  file   <sha256> <mode> <uid> <gid> <size> <selinux|-> <caps|->
  <relpath>  link   <target> <uid> <gid>
  <relpath>  dir    <mode> <uid> <gid> <selinux|->
  <relpath>  special <mode>
  <relpath>  ERR    <reason>
"""
import hashlib
import os
import stat
import sys


def get_xattr(p, name):
    try:
        v = os.getxattr(p, name)
        return v.hex()
    except (OSError, AttributeError):
        return "-"


def main():
    root, out = sys.argv[1], sys.argv[2]
    lines = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            try:
                st = os.lstat(p)
            except OSError as e:
                lines.append(f"{rel}\tERR\t{e}")
                continue
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                lines.append(f"{rel}\tlink\t{os.readlink(p)}\t{st.st_uid}\t{st.st_gid}")
            elif stat.S_ISREG(st.st_mode):
                h = hashlib.sha256()
                try:
                    with open(p, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                except OSError as e:
                    lines.append(f"{rel}\tERR\topen:{e}")
                    continue
                sel = get_xattr(p, "security.selinux")
                caps = get_xattr(p, "security.capability")
                lines.append(f"{rel}\tfile\t{h.hexdigest()}\t{mode:o}\t{st.st_uid}\t{st.st_gid}\t{st.st_size}\t{sel}\t{caps}")
            elif stat.S_ISDIR(st.st_mode):
                sel = get_xattr(p, "security.selinux")
                lines.append(f"{rel}\tdir\t{mode:o}\t{st.st_uid}\t{st.st_gid}\t{sel}")
            else:
                lines.append(f"{rel}\tspecial\t{mode:o}")
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
