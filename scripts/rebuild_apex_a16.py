#!/usr/bin/env python3
"""Inject patched framework jars into A16 APEX payloads + rebuild APEX zips.

Workflow per APEX (wifi, tethering):
  1. Copy original apex_payload.img (with AVB footer) to a working path
  2. avbtool erase_footer — strip the AVB footer (the ext4 image now ends at
     its original ext4 boundary; bytes outside are dropped)
  3. debugfs -w -R "write <local.jar> <in-img-path>" — overwrite the jar
     in the ext4 image. Then sif (uid/gid/mode) + ea_set (selinux label)
     to preserve the original entry's metadata.
  4. e2fsck -fn — gate: ensure the ext4 structure is intact after surgery
  5. avbtool add_hashtree_footer — re-add the AVB hashtree + RSA signature
     with our fakewifi_key.pem (SHA256_RSA4096). The apex_pubkey bundled
     in the APEX matches this key.
  6. Rebuild the APEX zip:
     - Replace apex_payload.img with the patched one
     - Keep apex_pubkey, AndroidManifest.xml, apex_build_info.pb,
       apex_manifest.pb, resources.arsc, assets/ (preserve per-entry
       compression + external_attr from the original)
     - Strip META-INF/ (will be re-signed fresh)
  7. zipalign to 4096 bytes (apksigner v3 wants alignment)
  8. apksigner sign with fakewifi.jks (v2+v3, --v1-signing-enabled false)
"""
import os
import shutil
import subprocess
import sys
import zipfile

WORK = "/home/z/my-project/work/a16"
DEBUGFS = "/home/z/my-project/bin/debugfs"
E2FSCK = "/home/z/my-project/bin/e2fsck"
AVBTOOL = "/home/z/my-project/tools/android/avbtool.py"
APKSIGNER_JAR = "/home/z/my-project/bin/apksigner.jar"
ZIPALIGN = "/home/z/my-project/bin/zipalign"
KEYS = "/home/z/my-project/scratch/fakewifi-waydroid-a14/build/keys"
AVB_KEY = f"{KEYS}/fakewifi_key.pem"
APK_JKS = f"{KEYS}/fakewifi.jks"
APK_PASS = "fakewifi"

LD_LIBRARY_PATH = (
    "/home/z/my-project/tools/android/build-tools/android-14/lib64:"
    "/home/z/my-project/tools/extract/usr/lib/x86_64-linux-gnu:"
    "/home/z/my-project/tools/extract/lib/x86_64-linux-gnu"
)


def run(cmd, check=True, capture=False):
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = LD_LIBRARY_PATH + ":" + env.get("LD_LIBRARY_PATH", "")
    if capture:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if check and r.returncode != 0:
            print(f"FAIL: {' '.join(cmd)}")
            print("STDOUT:", r.stdout[-1000:])
            print("STDERR:", r.stderr[-1000:])
            sys.exit(1)
        return r
    else:
        return subprocess.run(cmd, env=env)


def get_metadata(payload, in_img):
    """Use debugfs stat + ea_list to extract uid/gid/mode/selinux label.
    debugfs output format (relevant lines):
        Inode: 36   Type: regular    Mode:  0644   Flags: 0x80000
        User:  1000   Group:  1000   Project:     0   Size: 1427945
    """
    r = run([DEBUGFS, "-R", f"stat {in_img}", payload], check=False, capture=True)
    out = r.stdout + r.stderr
    uid = gid = mode = None
    file_type = "regular"
    for line in out.splitlines():
        # Mode appears inline on the Inode line: "Inode: X   Type: regular    Mode:  0644   Flags: 0x"
        if "Mode:" in line:
            # Extract using regex-ish
            after_mode = line.split("Mode:")[1].strip()
            # Take first whitespace-separated token (the mode value)
            mode_str = after_mode.split()[0]
            try:
                mode = int(mode_str, 8)
            except ValueError:
                pass
        if "Type:" in line:
            after_type = line.split("Type:")[1].strip()
            file_type = after_type.split()[0]
        if "User:" in line:
            after_user = line.split("User:")[1].strip()
            uid = after_user.split()[0]
        if "Group:" in line:
            after_group = line.split("Group:")[1].strip()
            gid = after_group.split()[0]
    # Get SELinux label via ea_list (separate debugfs command)
    selinux = None
    r2 = run([DEBUGFS, "-R", f"ea_list {in_img}", payload], check=False, capture=True)
    for line in (r2.stdout + r2.stderr).splitlines():
        if "security.selinux" in line:
            # Format: "  security.selinux (26) = \"u:object_r:system_file:s0\\000\""
            parts = line.split("=", 1)
            if len(parts) == 2:
                selinux = parts[1].strip().strip('"').strip()
                # Strip trailing \000 (debugfs prints raw null)
                selinux = selinux.split("\\000")[0]
                selinux = selinux.rstrip("\x00")
            break
    # Convert mode to full st_mode (file type + perms)
    S_IFREG = 0o100000
    S_IFDIR = 0o040000
    if file_type == "directory":
        full_mode = S_IFDIR | (mode & 0o7777) if mode else None
    else:
        full_mode = S_IFREG | (mode & 0o7777) if mode else None
    return uid, gid, full_mode, selinux


def inject_into_payload(name, apex_dir, inject_pairs, orig_size):
    """The full per-APEX workflow: strip footer, inject, e2fsck, re-add footer."""
    payload_path = f"{apex_dir}/apex_payload.img"
    work_payload = f"{apex_dir}/apex_payload.work"
    # Reset from original
    if os.path.exists(f"{apex_dir}/apex_payload.orig"):
        shutil.copy2(f"{apex_dir}/apex_payload.orig", work_payload)
    else:
        shutil.copy2(payload_path, f"{apex_dir}/apex_payload.orig")
        shutil.copy2(f"{apex_dir}/apex_payload.orig", work_payload)
    print(f"  working copy: {work_payload} ({os.path.getsize(work_payload)} bytes)")
    # Step 1: Strip AVB footer
    print(f"  [1/4] stripping AVB footer...")
    run(["python3", AVBTOOL, "erase_footer", "--image", work_payload], capture=True)
    print(f"        after strip: {os.path.getsize(work_payload)} bytes")
    # Step 2: Inject each jar (rm first then write, like A14 build.sh)
    print(f"  [2/4] injecting jars...")
    for local, in_img in inject_pairs:
        # Capture metadata from the (still stock) image
        uid, gid, mode, selinux = get_metadata(work_payload, in_img)
        print(f"        {in_img}: uid={uid} gid={gid} mode={oct(mode)} selinux={selinux}")
        # First, rm the existing file (debugfs write FAILS silently if file
        # exists — A14 build.sh pattern: rm then write then sif+ea_set)
        run([DEBUGFS, "-w", "-R", f"rm {in_img}", work_payload], capture=True)
        # Now write the new file (creates a fresh inode)
        run([DEBUGFS, "-w", "-R", f"write {local} {in_img}", work_payload], capture=True)
        # Verify it actually got written (size check)
        r_stat = run([DEBUGFS, "-R", f"stat {in_img}", work_payload], check=False, capture=True)
        stat_out = r_stat.stdout + r_stat.stderr
        for line in stat_out.splitlines():
            if "Size:" in line:
                # Parse size from "Size: N"
                size_str = line.split("Size:")[1].strip().split()[0]
                actual_size = int(size_str)
                expected_size = os.path.getsize(local)
                print(f"        verified size: in-img={actual_size} expected={expected_size}")
                if actual_size != expected_size:
                    print(f"        FAIL: write didn't replace the file (expected {expected_size}, got {actual_size})")
                    sys.exit(1)
                break
        # Set uid/gid/mode
        if uid:
            run([DEBUGFS, "-w", "-R", f"sif {in_img} uid {uid}", work_payload], capture=True)
        if gid:
            run([DEBUGFS, "-w", "-R", f"sif {in_img} gid {gid}", work_payload], capture=True)
        if mode:
            run([DEBUGFS, "-w", "-R", f"sif {in_img} mode {mode}", work_payload], capture=True)
        # Set SELinux label
        if selinux:
            run([DEBUGFS, "-w", "-R", f"ea_set {in_img} security.selinux {selinux}", work_payload], capture=True)
    # Step 3: e2fsck gate
    print(f"  [3/4] e2fsck -fn gate...")
    r = run([E2FSCK, "-fn", work_payload], check=False, capture=True)
    # e2fsck -fn returns 0 if clean, non-zero if errors found (but doesn't fix with -n)
    # We want rc=0 (clean) or the various "no errors" return codes
    # 0: no errors
    # 1: filesystem errors corrected
    # 2: filesystem errors corrected, system should be rebooted
    # 4: filesystem errors left uncorrected (BAD)
    # 8: operational error (BAD)
    # 16: usage error (BAD)
    # 32: canceled
    # 128: shared library error
    if r.returncode >= 4:
        print(f"        FAIL: e2fsck rc={r.returncode}")
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        sys.exit(1)
    print(f"        OK (rc={r.returncode})")
    # Step 4: Re-add AVB hashtree footer with our key
    print(f"  [4/4] re-adding AVB hashtree footer...")
    # Start with the original size; if it doesn't fit, grow by 4K
    p = orig_size
    # Round to 4K
    p = ((p + 4095) // 4096) * 4096
    success = False
    for attempt in range(20):
        r = run([
            "python3", AVBTOOL, "add_hashtree_footer",
            "--do_not_generate_fec",
            "--hash_algorithm", "sha256",
            "--block_size", "4096",
            "--partition_name", "",
            "--partition_size", str(p),
            "--algorithm", "SHA256_RSA4096",
            "--key", AVB_KEY,
            "--image", work_payload,
        ], check=False, capture=True)
        if r.returncode == 0:
            print(f"        OK with partition_size={p} ({os.path.getsize(work_payload)} bytes)")
            success = True
            break
        p += 4096
    if not success:
        print("        FAIL: add_hashtree_footer never succeeded")
        sys.exit(1)
    # Replace the original apex_payload.img with the new patched one
    shutil.copy2(work_payload, payload_path)
    print(f"  patched {name} apex_payload.img: {os.path.getsize(payload_path)} bytes")


def rebuild_apex_zip(name, apex_dir, orig_apex_zip, out_apex):
    """Rebuild the APEX zip:
    - Replace apex_payload.img with the patched one (signed with our AVB key)
    - Replace apex_pubkey with OUR pubkey (matches our AVB signature)
    - Keep AndroidManifest.xml, apex_build_info.pb, apex_manifest.pb,
      resources.arsc, assets/ (preserve per-entry compression + external_attr
      from the original)
    - Strip META-INF/ (will be re-signed fresh)
    """
    payload_path = f"{apex_dir}/apex_payload.img"
    our_pubkey = f"{KEYS}/apex_pubkey"
    if not os.path.exists(our_pubkey):
        sys.exit(f"FATAL: our apex_pubkey not found at {our_pubkey}")
    os.makedirs(os.path.dirname(out_apex), exist_ok=True)
    unsigned = out_apex + ".unsigned"
    with zipfile.ZipFile(orig_apex_zip, "r") as z_in, \
         zipfile.ZipFile(unsigned, "w") as z_out:
        for info in z_in.infolist():
            n = info.filename
            if n.startswith("META-INF/"):
                continue
            if n == "apex_payload.img":
                data = open(payload_path, "rb").read()
            elif n == "apex_pubkey":
                data = open(our_pubkey, "rb").read()
            else:
                data = z_in.read(n)
            zi = zipfile.ZipInfo(n, date_time=info.date_time)
            zi.compress_type = (
                zipfile.ZIP_STORED if n == "apex_payload.img"
                else info.compress_type
            )
            zi.external_attr = info.external_attr
            zi.create_system = info.create_system
            z_out.writestr(zi, data)
    print(f"  built unsigned {unsigned} ({os.path.getsize(unsigned)} bytes), apex_pubkey replaced with ours")
    # zipalign
    aligned = out_apex + ".aligned"
    run([ZIPALIGN, "-f", "-p", "4096", unsigned, aligned], capture=True)
    os.remove(unsigned)
    # apksigner
    run([
        "java", "-jar", APKSIGNER_JAR, "sign",
        "--ks", APK_JKS,
        "--ks-pass", f"pass:{APK_PASS}",
        "--key-pass", f"pass:{APK_PASS}",
        "--v1-signing-enabled", "false",
        "--v2-signing-enabled", "true",
        "--v3-signing-enabled", "true",
        "--out", out_apex,
        aligned,
    ], capture=True)
    os.remove(aligned)
    print(f"  signed APEX: {out_apex} ({os.path.getsize(out_apex)} bytes)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
os.makedirs(f"{WORK}/apexes-new", exist_ok=True)

print("===== WIFI APEX =====")
wifi_inject = [(f"{WORK}/jars-new/framework-wifi.jar", "/javalib/framework-wifi.jar")]
wifi_orig_size = os.path.getsize(f"{WORK}/apex-orig/wifi/apex_payload.img")
inject_into_payload("wifi", f"{WORK}/apex-orig/wifi", wifi_inject, wifi_orig_size)
rebuild_apex_zip(
    "wifi",
    f"{WORK}/apex-orig/wifi",
    f"{WORK}/extract/system/apex/com.android.wifi.apex",
    f"{WORK}/apexes-new/com.android.wifi.apex",
)

print("\n===== TETHERING APEX =====")
teth_inject = [(f"{WORK}/jars-new/framework-connectivity.jar", "/javalib/framework-connectivity.jar")]
teth_orig_size = os.path.getsize(f"{WORK}/apex-orig/tethering/apex_payload.img")
inject_into_payload("tethering", f"{WORK}/apex-orig/tethering", teth_inject, teth_orig_size)
rebuild_apex_zip(
    "tethering",
    f"{WORK}/apex-orig/tethering",
    f"{WORK}/extract/system/apex/com.android.tethering.apex",
    f"{WORK}/apexes-new/com.android.tethering.apex",
)

print("\nDone.")
