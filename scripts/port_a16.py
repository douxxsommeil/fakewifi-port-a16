#!/usr/bin/env python3
"""Port the fakewifi hooks from A14 patched smali into A16 stock smali trees.

Strategy:
- A16 stock smali trees (a16-wifi, a16-conn) are COPIED into patched trees.
- FakeWifi*.smali files from A14 patched are COPIED VERBATIM into the
  patched wifi tree (no API drift detected: WifiInfo.Builder.setSsid([B) still
  exists in A16, all set* methods on WifiInfo still present, IPackageManager /
  ServiceManager reflection APIs are stable).
- 4 hooks in WifiManager.smali replaced with A14 patched method bodies
  (signatures match: getConnectionInfo / getDhcpInfo / getWifiState / isWifiEnabled;
  field names mService / mContext match).
- 4 hooks in ConnectivityManager.smali replaced with A14 patched bodies
  (getActiveNetworkInfo / getNetworkInfo(I) / getAllNetworkInfo / isActiveNetworkMetered;
  field mService / mContext match).
- 1 hook in NetworkCapabilities.smali replaced (hasTransport(I), calls
  FakeWifi.isHackEnabledForUid + Binder.getCallingUid).
- 4 hooks in NetworkInfo.smali replaced (getType / getTypeName / isConnected / isAvailable,
  calls FakeWifi.isHackEnabledForCurrentUid).

All A14 patched smali bodies were verified compatible with A16 stock by signature
and field-name diff. The .line directives are cosmetic (line numbers from A14) and
do not affect runtime.
"""
import os
import shutil
import sys

WORK = "/home/z/my-project/work/a16"
# apktool d output is the WHOLE dir (apktool.yml + smali/ + original/ + unknown/)
A14_WIFI_DIR = f"{WORK}/smali/a14-wifi-patched"
A14_CONN_DIR = f"{WORK}/smali/a14-conn-patched"
A16_WIFI_DIR = f"{WORK}/smali/a16-wifi"
A16_CONN_DIR = f"{WORK}/smali/a16-conn"
OUT_WIFI_DIR = f"{WORK}/smali/a16-wifi-patched"
OUT_CONN_DIR = f"{WORK}/smali/a16-conn-patched"
# smali subdir is inside each (apktool convention)
A14_WIFI = f"{A14_WIFI_DIR}/smali"
A14_CONN = f"{A14_CONN_DIR}/smali"
A16_WIFI = f"{A16_WIFI_DIR}/smali"
A16_CONN = f"{A16_CONN_DIR}/smali"
OUT_WIFI = f"{OUT_WIFI_DIR}/smali"
OUT_CONN = f"{OUT_CONN_DIR}/smali"


def fresh(dst, src):
    """Copy a stock smali tree to the patched output, replacing any prior."""
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def extract_method(path, method_sig):
    """Return the lines of the .method ... .end method block whose .method line
    contains method_sig, e.g. 'getConnectionInfo()Landroid/net/wifi/WifiInfo;'."""
    with open(path) as f:
        lines = f.readlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(".method") and method_sig in ln:
            start = i
            break
    if start is None:
        sys.exit(f"FATAL: method not found: {method_sig} in {path}")
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(".end method"):
            end = j
            break
    if end is None:
        sys.exit(f"FATAL: .end method not found for {method_sig} in {path}")
    return lines[start:end + 1]


def replace_method(path, method_sig, new_body):
    """Replace the whole .method ... .end method block whose .method line
    contains method_sig with new_body (a list of lines)."""
    with open(path) as f:
        lines = f.readlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(".method") and method_sig in ln:
            start = i
            break
    if start is None:
        sys.exit(f"FATAL: method not found: {method_sig} in {path}")
    end = None
    for j in range(start + 1, len(lines)):
        if lines[j].startswith(".end method"):
            end = j
            break
    if end is None:
        sys.exit(f"FATAL: .end method not found for {method_sig} in {path}")
    lines[start:end + 1] = new_body
    with open(path, "w") as f:
        f.writelines(lines)
    print(f"  patched {os.path.basename(path)} :: {method_sig.split('(')[0]}")


# ---------------------------------------------------------------------------
# 1. Copy stock smali trees to patched output trees
# ---------------------------------------------------------------------------
print("== Copying stock A16 apktool dirs to patched output ==")
fresh(OUT_WIFI_DIR, A16_WIFI_DIR)
fresh(OUT_CONN_DIR, A16_CONN_DIR)

# ---------------------------------------------------------------------------
# 2. framework-wifi: copy FakeWifi* classes from A14 patched into A16 patched
# ---------------------------------------------------------------------------
print("\n== framework-wifi: copying FakeWifi* classes from A14 patched ==")
src_dir = f"{A14_WIFI}/android/net/wifi"
dst_dir = f"{OUT_WIFI}/android/net/wifi"
n = 0
for f_ in os.listdir(src_dir):
    if f_.startswith("FakeWifi"):
        shutil.copy(f"{src_dir}/{f_}", f"{dst_dir}/{f_}")
        n += 1
        print(f"  copied {f_}")
print(f"  total: {n} FakeWifi*.smali classes copied verbatim")

# ---------------------------------------------------------------------------
# 3. WifiManager: 4 hooks (getConnectionInfo / getDhcpInfo / getWifiState / isWifiEnabled)
# ---------------------------------------------------------------------------
print("\n== framework-wifi: WifiManager hooks (4) ==")
wm_a14 = f"{A14_WIFI}/android/net/wifi/WifiManager.smali"
wm_a16 = f"{OUT_WIFI}/android/net/wifi/WifiManager.smali"

hooks_wm = [
    ("getConnectionInfo()Landroid/net/wifi/WifiInfo;",),
    ("getDhcpInfo()Landroid/net/DhcpInfo;",),
    ("getWifiState()I",),
    ("isWifiEnabled()Z",),
]
for (sig,) in hooks_wm:
    body = extract_method(wm_a14, sig)
    replace_method(wm_a16, sig, body)

# ---------------------------------------------------------------------------
# 4. framework-connectivity: ConnectivityManager, NetworkCapabilities, NetworkInfo
# ---------------------------------------------------------------------------
print("\n== framework-connectivity: ConnectivityManager hooks (4) ==")
cm_a14 = f"{A14_CONN}/android/net/ConnectivityManager.smali"
cm_a16 = f"{OUT_CONN}/android/net/ConnectivityManager.smali"

hooks_cm = [
    ("getActiveNetworkInfo()Landroid/net/NetworkInfo;",),
    ("getNetworkInfo(I)Landroid/net/NetworkInfo;",),
    ("getAllNetworkInfo()[Landroid/net/NetworkInfo;",),
    ("isActiveNetworkMetered()Z",),
]
for (sig,) in hooks_cm:
    body = extract_method(cm_a14, sig)
    replace_method(cm_a16, sig, body)

print("\n== framework-connectivity: NetworkCapabilities.hasTransport(I) ==")
nc_a14 = f"{A14_CONN}/android/net/NetworkCapabilities.smali"
nc_a16 = f"{OUT_CONN}/android/net/NetworkCapabilities.smali"
body = extract_method(nc_a14, "hasTransport(I)Z")
replace_method(nc_a16, "hasTransport(I)Z", body)

print("\n== framework-connectivity: NetworkInfo hooks (4) ==")
ni_a14 = f"{A14_CONN}/android/net/NetworkInfo.smali"
ni_a16 = f"{OUT_CONN}/android/net/NetworkInfo.smali"
hooks_ni = [
    ("getType()I",),
    ("getTypeName()Ljava/lang/String;",),
    ("isConnected()Z",),
    ("isAvailable()Z",),
]
for (sig,) in hooks_ni:
    body = extract_method(ni_a14, sig)
    replace_method(ni_a16, sig, body)

print("\nAll ports applied successfully.")
print(f"  patched wifi tree:      {OUT_WIFI}")
print(f"  patched connectivity:   {OUT_CONN}")
