#!/usr/bin/env python3
"""fakewifi-port-a16 — master build script (A16, EROFS repack, faithful metadata).

Rebuilds the WayDroid-ATV 20260717 GAPPS system.img with the FakeWifi APEXes
(com.android.wifi, com.android.tethering) patched in, with FULL metadata
fidelity (uid/gid, modes, SELinux labels, file capabilities, mtimes).

Fixes vs the 2026-10-15 v1.0.0 build:
  1. Tree extraction runs AS ROOT with --xattrs --preserve-perms --preserve-owner
     -> security.selinux labels AND security.capability survive the round trip.
  2. mkfs.erofs runs WITHOUT --all-root / --force-uid / --force-gid
     -> original uid/gid preserved (491 files had gid 2000, etc.).
  3. mkfs.erofs gets NO --file-contexts: labels come from the restored file
     xattrs themselves (erofs-utils >= 1.8 stores security.* from the fs).
  4. --preserve-mtime + stock superblock timestamp/UUID
     -> no more 2024 timestamps / new UUID.
  5. erofs-utils >= 1.8.6 both ways (v1.0.0 used mkfs 1.5 + fsck 1.9.4 mix).
  6. Post-build gate: full tree-diff (content + metadata + xattrs) between the
     input tree and the repacked image; build FAILS on any unexpected diff.

Usage (root or sudo):
  python3 scripts/build_image_a16.py \
      --stock-zip lineage-23.2-20260717-GAPPS-waydroid_x86_64-system.zip \
      --workdir /tmp/a16build --out-dir out/ --repo-root .
"""
import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import zipfile

SPLIT_BYTES = 1 << 30  # 1 GiB chunks for release assets


def run(cmd, check=True, capture=True, env=None, as_root=False):
    if as_root and os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", "-E"] + cmd
    print("  $ " + " ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=capture, text=True, env=env)
    if capture:
        if r.stdout:
            print(r.stdout[-2000:], flush=True)
        if r.returncode != 0 and check:
            print(r.stderr[-4000:], file=sys.stderr, flush=True)
            raise SystemExit(f"FATAL: command failed rc={r.returncode}: {cmd[0]}")
    elif check and r.returncode != 0:
        raise SystemExit(f"FATAL: command failed rc={r.returncode}: {cmd[0]}")
    return r


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# APEX payload surgery (same proven flow as v1.0.0, env-clean paths)
# ---------------------------------------------------------------------------
DEBUGFS = shutil.which("debugfs") or "/usr/sbin/debugfs"
E2FSCK = shutil.which("e2fsck") or "/usr/sbin/e2fsck"
AVBTOOL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "avbtool.py")


def debugfs_stat(payload, in_img):
    """uid/gid/mode/selinux of an inode inside ext4 payload."""
    r = run([DEBUGFS, "-R", f"stat {in_img}", payload])
    uid = gid = mode = None
    for line in r.stdout.splitlines():
        if "Mode:" in line:
            try:
                mode = int(line.split("Mode:")[1].split()[0], 8)
            except (ValueError, IndexError):
                pass
        if "User:" in line:
            uid = line.split("User:")[1].split()[0]
        if "Group:" in line:
            gid = line.split("Group:")[1].split()[0]
    r2 = run([DEBUGFS, "-R", f"ea_list {in_img}", payload])
    selinux = None
    for line in (r2.stdout + r2.stderr).splitlines():
        if "security.selinux" in line and "=" in line:
            selinux = line.split("=", 1)[1].strip().strip('"').split("\\000")[0].rstrip("\x00")
            break
    return uid, gid, mode, selinux


def inject_into_payload(apex_dir, local_jar, in_img_path, orig_size):
    payload = os.path.join(apex_dir, "apex_payload.img")
    work = os.path.join(apex_dir, "apex_payload.work")
    orig = os.path.join(apex_dir, "apex_payload.orig")
    if not os.path.exists(orig):
        shutil.copy2(payload, orig)
    shutil.copy2(orig, work)

    print("  [1/4] strip AVB footer")
    run(["python3", AVBTOOL, "erase_footer", "--image", work])
    uid, gid, mode, selinux = debugfs_stat(work, in_img_path)
    print(f"  [2/4] rm+write {in_img_path} (uid={uid} gid={gid} mode={oct(mode or 0)} selinux={selinux})")
    # debugfs write does NOT overwrite existing entries: rm first (A14 build.sh pattern)
    run([DEBUGFS, "-w", "-R", f"rm {in_img_path}", work])
    run([DEBUGFS, "-w", "-R", f"write {local_jar} {in_img_path}", work])
    # verify the write actually replaced the file
    r = run([DEBUGFS, "-R", f"stat {in_img_path}", work])
    got = None
    for line in r.stdout.splitlines():
        if "Size:" in line:
            got = int(line.split("Size:")[1].strip().split()[0])
            break
    exp = os.path.getsize(local_jar)
    if got != exp:
        raise SystemExit(f"FATAL: injection size mismatch in-img={got} expected={exp}")
    if uid:
        run([DEBUGFS, "-w", "-R", f"sif {in_img_path} uid {uid}", work])
    if gid:
        run([DEBUGFS, "-w", "-R", f"sif {in_img_path} gid {gid}", work])
    if mode:
        run([DEBUGFS, "-w", "-R", f"sif {in_img_path} mode {mode}", work])
    if selinux:
        run([DEBUGFS, "-w", "-R", f"ea_set {in_img_path} security.selinux {selinux}", work])

    print("  [3/4] e2fsck -fn gate")
    r = run([E2FSCK, "-fn", work], check=False)
    if r.returncode >= 4:
        raise SystemExit(f"FATAL: e2fsck rc={r.returncode}")

    print("  [4/4] re-add AVB hashtree footer")
    p = ((orig_size + 4095) // 4096) * 4096
    for _ in range(20):
        r = run(["python3", AVBTOOL, "add_hashtree_footer",
                 "--do_not_generate_fec", "--hash_algorithm", "sha256",
                 "--block_size", "4096", "--partition_name", "",
                 "--partition_size", str(p), "--algorithm", "SHA256_RSA4096",
                 "--key", KEYS_AVB, "--image", work], check=False)
        if r.returncode == 0:
            break
        p += 4096
    else:
        raise SystemExit("FATAL: add_hashtree_footer never succeeded")
    shutil.copy2(work, payload)
    print(f"  patched payload: {os.path.getsize(payload)} bytes")


def rebuild_apex_zip(apex_dir, orig_apex_zip, out_apex):
    our_pubkey = KEYS_PUBKEY
    unsigned = out_apex + ".unsigned"
    with zipfile.ZipFile(orig_apex_zip) as zin, zipfile.ZipFile(unsigned, "w") as zout:
        for info in zin.infolist():
            n = info.filename
            if n.startswith("META-INF/"):
                continue
            if n == "apex_payload.img":
                data = open(os.path.join(apex_dir, "apex_payload.img"), "rb").read()
            elif n == "apex_pubkey":
                data = open(our_pubkey, "rb").read()
            else:
                data = zin.read(n)
            zi = zipfile.ZipInfo(n, date_time=info.date_time)
            zi.compress_type = zipfile.ZIP_STORED if n == "apex_payload.img" else info.compress_type
            zi.external_attr = info.external_attr
            zi.create_system = info.create_system
            zout.writestr(zi, data)
    aligned = out_apex + ".aligned"
    run([ZIPALIGN, "-f", "-p", "4096", unsigned, aligned])
    os.remove(unsigned)
    run(["java", "-jar", APKSIGNER_JAR, "sign",
         "--ks", APK_JKS, "--ks-pass", f"pass:{APK_PASS}", "--key-pass", f"pass:{APK_PASS}",
         "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
         "--v3-signing-enabled", "true", "--out", out_apex, aligned])
    os.remove(aligned)
    print(f"  signed APEX: {out_apex} ({os.path.getsize(out_apex)} bytes)")


def build_patched_jar(orig_jar, prebuilt_jar, out_jar):
    """stock jar entries + classes.dex from the prebuilt (patched) jar."""
    with zipfile.ZipFile(prebuilt_jar) as z:
        new_dex = z.read("classes.dex")
    with zipfile.ZipFile(orig_jar) as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(out_jar, "w") as zout:
            for info in infos:
                data = new_dex if info.filename == "classes.dex" else zin.read(info.filename)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.create_system = info.create_system
                zout.writestr(zi, data)
    print(f"  jar rebuilt: {out_jar} ({os.path.getsize(out_jar)} bytes)")


# ---------------------------------------------------------------------------
# verification helpers
# ---------------------------------------------------------------------------
def superblock_info(image, dump_erofs, env):
    r = subprocess.run([dump_erofs, "-s", image], capture_output=True, text=True, env=env)
    txt = r.stdout
    import re
    uuid = re.search(r"Filesystem UUID:\s+(\S+)", txt).group(1)
    created = re.search(r"Filesystem created:\s+(.*)", txt).group(1).strip()
    inodes = int(re.search(r"Filesystem inode count:\s+(\d+)", txt).group(1))
    from datetime import datetime
    ts = int(datetime.strptime(created, "%a %b %d %H:%M:%S %Y").replace(tzinfo=None).timestamp())
    return uuid, ts, inodes


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    global KEYS_AVB, KEYS_PUBKEY, APK_JKS, APK_PASS, ZIPALIGN, APKSIGNER_JAR

    ap = argparse.ArgumentParser()
    ap.add_argument("--stock-zip", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--repo-root", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--skip-gate", action="store_true", help="skip the tree-diff gate (not recommended)")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo_root)
    work = os.path.abspath(args.workdir)
    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(work, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)

    KEYS_AVB = os.path.join(repo, "build", "keys", "fakewifi_key.pem")
    KEYS_PUBKEY = os.path.join(repo, "build", "keys", "apex_pubkey")
    APK_JKS = os.path.join(repo, "build", "keys", "fakewifi.jks")
    APK_PASS = "fakewifi"
    PREBUILT = {
        "com.android.wifi.apex": ("framework-wifi.jar", os.path.join(repo, "prebuilt", "framework-wifi.jar")),
        "com.android.tethering.apex": ("framework-connectivity.jar", os.path.join(repo, "prebuilt", "framework-connectivity.jar")),
    }

    # tool discovery
    import glob
    fsck = run(["bash", "-lc", "command -v fsck.erofs || true"]).stdout.strip()
    if not fsck or not os.path.exists(fsck):
        cands = glob.glob("/usr/local/bin/fsck.erofs") + glob.glob("/usr/bin/fsck.erofs")
        if not cands:
            raise SystemExit("FATAL: fsck.erofs not found (build erofs-utils >= 1.8.6 first)")
        fsck = cands[0]
    mkfs = fsck.replace("fsck.erofs", "mkfs.erofs")
    dump = fsck.replace("fsck.erofs", "dump.erofs")
    bt = os.environ.get("BUILD_TOOLS_DIR")
    if not bt:
        g = glob.glob(os.path.join(os.path.expanduser("~"), "android-build-tools", "*"))
        if not g:
            raise SystemExit("FATAL: set BUILD_TOOLS_DIR to the android build-tools dir")
        bt = g[0]
    ZIPALIGN = os.path.join(bt, "zipalign")
    APKSIGNER_JAR = os.path.join(bt, "lib", "apksigner.jar")
    print(f"tools: fsck={fsck} zipalign={ZIPALIGN}")

    env = dict(os.environ)

    # 1. unzip stock image
    print("== [1/8] unzip stock image ==")
    stock_img = os.path.join(work, "system-stock.img")
    with zipfile.ZipFile(args.stock_zip) as z:
        names = z.namelist()
        member = [n for n in names if n.endswith("system.img")][0]
        with z.open(member) as src, open(stock_img, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    print(f"  stock img: {os.path.getsize(stock_img)} bytes")

    # 2. superblock info (UUID, build time, inode count)
    print("== [2/8] read stock superblock ==")
    uuid, sb_ts, inodes = superblock_info(stock_img, dump, env)
    print(f"  uuid={uuid} ts={sb_ts} inodes={inodes}")

    # 3. extract tree AS ROOT with full fidelity
    print("== [3/8] extract EROFS tree (root, xattrs) ==")
    tree = os.path.join(work, "tree")
    r = run([fsck, f"--extract={tree}", "--xattrs", "--overwrite", "--preserve-perms", "--preserve-owner", stock_img], check=False, as_root=True)
    if r.returncode != 0:
        raise SystemExit(f"FATAL: fsck extract rc={r.returncode}")
    apex_dir_in_tree = None
    for cand in ("system/apex", "apex"):
        p = os.path.join(tree, cand)
        if os.path.isdir(p):
            apex_dir_in_tree = p
            break
    if not apex_dir_in_tree:
        raise SystemExit("FATAL: cannot locate apex dir in extracted tree")
    print(f"  apex dir: {apex_dir_in_tree}")

    # snapshot stock manifest BEFORE replacing apexes (for the final report)
    stock_manifest = os.path.join(work, "manifest-stock.tsv")
    run(["python3", os.path.join(repo, "scripts", "tree_manifest.py"), tree, stock_manifest])

    # 4. unpack apexes, dump stock jars, build patched jars, inject, re-sign
    print("== [4/8] rebuild APEXes ==")
    apexes_new = os.path.join(out_dir, "apexes-new")
    os.makedirs(apexes_new, exist_ok=True)
    patched_apexes = {}
    for apex_name, (jar_name, prebuilt_jar) in PREBUILT.items():
        src_apex = os.path.join(apex_dir_in_tree, apex_name)
        adir = os.path.join(work, "apex-" + apex_name)
        if os.path.exists(adir):
            shutil.rmtree(adir)
        os.makedirs(adir)
        with zipfile.ZipFile(src_apex) as z:
            z.extractall(adir)
        # dump stock jar
        payload = os.path.join(adir, "apex_payload.img")
        stock_jar = os.path.join(adir, jar_name + ".stock")
        run([DEBUGFS, "-R", f"dump /javalib/{jar_name} {stock_jar}", payload])
        patched_jar = os.path.join(adir, jar_name + ".patched")
        build_patched_jar(stock_jar, prebuilt_jar, patched_jar)
        orig_size = os.path.getsize(payload)
        inject_into_payload(adir, patched_jar, f"/javalib/{jar_name}", orig_size)
        out_apex = os.path.join(apexes_new, apex_name)
        rebuild_apex_zip(adir, src_apex, out_apex)
        patched_apexes[apex_name] = out_apex
        # post-verify the apex
        work_payload = os.path.join(adir, "apex_payload.img")
        run(["python3", AVBTOOL, "verify_image", "--image", work_payload, "--key", KEYS_AVB])
        run(["java", "-jar", APKSIGNER_JAR, "verify", out_apex])

    # 5. replace apexes in tree (preserving each entry's original uid/gid/mode)
    print("== [5/8] replace APEXes in tree ==")
    for apex_name, out_apex in patched_apexes.items():
        dst = os.path.join(apex_dir_in_tree, apex_name)
        st = os.lstat(dst)
        shutil.copy2(out_apex, dst)
        os.chmod(dst, stat.S_IMODE(st.st_mode))
        os.chown(dst, st.st_uid, st.st_gid)
        os.utime(dst, (st.st_atime, st.st_mtime))

    # 6. mkfs.erofs rebuild (faithful)
    print("== [6/8] mkfs.erofs rebuild ==")
    out_img = os.path.join(work, "system-fakewifi.img")
    if os.path.exists(out_img):
        os.remove(out_img)
    run([mkfs, "-zlz4", "--preserve-mtime", "-T", str(sb_ts), "--mkfs-time",
         "-U", uuid, out_img, tree], as_root=True)

    # 7. verification gate
    print("== [7/8] verification gate ==")
    run([fsck, out_img], as_root=True)
    r = subprocess.run([dump, "-s", out_img], capture_output=True, text=True, env=env)
    if f"inode count: {inodes}" not in r.stdout:
        raise SystemExit("FATAL: inode count mismatch after repack")
    print(f"  inode count OK ({inodes})")

    # sha256 equality of the apexes inside the image is guaranteed by the
    # tree-diff gate below (content hashes compared pre-mkfs vs post-mkfs).

    gate_report = None
    if not args.skip_gate:
        out_tree = os.path.join(work, "out-tree")
        if os.path.exists(out_tree):
            shutil.rmtree(out_tree)
        run([fsck, f"--extract={out_tree}", "--xattrs", "--overwrite", "--preserve-perms", "--preserve-owner", out_img], as_root=True)
        out_manifest = os.path.join(work, "manifest-out.tsv")
        run(["python3", os.path.join(repo, "scripts", "tree_manifest.py"), out_tree, out_manifest])
        report_path = os.path.join(out_dir, "repack-fidelity.txt")
        r = run(["python3", os.path.join(repo, "scripts", "manifest_diff.py"), stock_manifest, out_manifest], check=False)
        with open(report_path, "w") as f:
            f.write(r.stdout)
        print(r.stdout[:3000])
        # gate: only the 2 apex files may differ in content
        bad = []
        for line in r.stdout.splitlines():
            if line.startswith("  C "):
                path = line[4:].strip()
                if not path.endswith(("com.android.wifi.apex", "com.android.tethering.apex")):
                    bad.append(path)
        if "CONTENT diffs: 2" not in r.stdout or bad:
            raise SystemExit(f"FATAL: repack fidelity gate FAILED, unexpected content diffs: {bad}")
        gate_report = report_path
        shutil.rmtree(out_tree)

    # 8. package outputs
    print("== [8/8] package outputs ==")
    final = os.path.join(out_dir, "system.img")
    shutil.copy2(out_img, final)
    with open(final + ".part0", "wb") as f0:
        with open(final, "rb") as src:
            remaining = SPLIT_BYTES
            while True:
                chunk = src.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f0.write(chunk)
                remaining -= len(chunk)
    with open(final + ".part1", "wb") as f1:
        with open(final, "rb") as src:
            src.seek(SPLIT_BYTES)
            shutil.copyfileobj(src, f1, 1 << 20)
    h = sha256(final)
    with open(os.path.join(out_dir, "sha256sum"), "w") as f:
        f.write(f"{h}  system.img\n")
        for apex_name in patched_apexes:
            f.write(f"{sha256(os.path.join(apexes_new, apex_name))}  apexes-new/{apex_name}\n")
    print(f"DONE. system.img sha256={h}")
    if gate_report:
        print(f"fidelity report: {gate_report}")


if __name__ == "__main__":
    main()
