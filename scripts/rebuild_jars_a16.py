#!/usr/bin/env python3
"""Replace classes.dex in the A16 stock framework JARs with the new patched dex
from the apktool-built APKs, preserving the original JAR structure (META-INF/MF,
compat config XMLs, etc)."""
import sys
import zipfile
import shutil
import os

WORK = "/home/z/my-project/work/a16"
ORIG_WIFI = f"{WORK}/jars-orig/wifi/framework-wifi.jar"
ORIG_CONN = f"{WORK}/jars-orig/tethering/framework-connectivity.jar"
NEW_WIFI_APK = f"{WORK}/jars-new/framework-wifi.apk"
NEW_CONN_APK = f"{WORK}/jars-new/framework-connectivity.apk"
OUT_WIFI = f"{WORK}/jars-new/framework-wifi.jar"
OUT_CONN = f"{WORK}/jars-new/framework-connectivity.jar"


def rebuild_jar(orig_jar, new_apk, out_jar):
    """Take entries from orig_jar, replace classes.dex with the one from new_apk,
    preserve per-entry compression type and external_attr. Output to out_jar."""
    # Read new classes.dex
    with zipfile.ZipFile(new_apk, "r") as z_new:
        new_dex = z_new.read("classes.dex")
    # Read orig jar entries
    with zipfile.ZipFile(orig_jar, "r") as z_in:
        infos = z_in.infolist()
        orig_data = {info.filename: (z_in.read(info.filename), info) for info in infos}
    # Write output
    with zipfile.ZipFile(out_jar, "w") as z_out:
        for info in infos:
            name = info.filename
            if name == "classes.dex":
                data = new_dex
            else:
                data, _orig_info = orig_data[name]
            # Replicate entry with original compression type and metadata
            zi = zipfile.ZipInfo(name, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            # Preserve internal attrs from original (these set unix mode bits, etc)
            zi.create_system = info.create_system
            z_out.writestr(zi, data)
    # Stats
    orig_size = os.path.getsize(orig_jar)
    new_size = os.path.getsize(out_jar)
    print(f"  {os.path.basename(out_jar)}: orig={orig_size} new={new_size} delta={new_size - orig_size:+d}")


print("== Rebuilding patched framework JARs ==")
rebuild_jar(ORIG_WIFI, NEW_WIFI_APK, OUT_WIFI)
rebuild_jar(ORIG_CONN, NEW_CONN_APK, OUT_CONN)
print("\nDone.")
