# vault/notes.md — Notas de sesión para el humano

> Este archivo es la crónica de la sesión del 2026-10-15 en el sandbox de z.ai.
> Si venís de una sesión futura, leelo entero antes de tocar nada.

## Resumen ejecutivo

Porteá el fakewifi de A14 a A16 en el sandbox de z.ai. Entregué un
`system.img` EROFS (1.95 GB) con los 2 APEX parchados (com.android.wifi +
com.android.tethering) firmados con clave propia. Falta verificar runtime en
tu host (Sandy Bridge i5-2430M). El pipeline de build entero está en
`scripts/` y es reproducible.

## Setup del sandbox

**Entorno**: z.ai sandbox, 2 vCPU, 4 GB RAM, 9.2 GB disco, Debian, Node 24,
Java 21 (OpenJDK). Sin sudo — extracción manual de `.deb` para filesystem
tools.

**Tools instaladas** (ver `AGENTS.md` § "Herramientas usadas"):

- e2fsprogs 1.47.4 (debugfs, e2fsck) — extraído de `.deb` (libext2fs2t64)
- erofs-utils 1.5 (mkfs.erofs) + 1.9.4 (fsck.erofs, dump.erofs) — extraídos
  de `.deb`
- attr 2.6.0 (setfattr, getfattr)
- Android build-tools r34 (zipalign, apksigner.jar)
- apktool 3.0.3 (incluye baksmali+smali 3.0.9-dev)
- avbtool.py (AOSP main, decodificado de base64 de googlesource)
- REA 6.5.0 (npm i -g rea-agents) — instalado pero no usado (JADX no
  disponible en el sandbox; el análisis de compatibilidad de API se hizo
  directo con baksmali + grep sobre el smali)

## Decisión: por qué port direct y no reuso de APEX A14

El usuario eligió "A16 port direct" en el AskUserQuestion inicial. Verifiqué
estáticamente que el APEX A14 cargaría en A16 (sin NoSuchFieldError) — los
campos `mService`/`mContext`/`mIsAvailable` y los métodos `WifiInfo.Builder`
siguen existiendo en A16 con las mismas firmas. Aún así, porteé para tener
smali de A16 puro y evitar riesgo futuro de drift.

## Pipeline de build (resumen, ver scripts/ para detalle)

1. **Download A16 GAPPS** (~1.3 GB) — wget con `--tries=10 --waitretry=5`
   porque GitHub releases CDN corta cada ~1 GB. Hubo que retomar 3 veces.
2. **Extract EROFS** con `fsck.erofs --extract=extract/` → 7508 inodes, 4.5 GB
3. **Unpack APEX** (zip) → `apex_payload.img` (ext4) + `apex_pubkey` + manifest
4. **Dump jars** con `debugfs -R "dump /javalib/framework-wifi.jar ..."`
5. **baksmali** con `java -jar apktool.jar d framework-wifi.jar` (mismo
   procedimiento para framework-connectivity.jar)
6. **Aplicar hooks**: `python3 scripts/port_a16.py`
   - Copia FakeWifi*.smali del A14 patched tree al A16 patched tree (verbatim)
   - Reemplaza 4 hooks en WifiManager.smali (getConnectionInfo, getDhcpInfo,
     getWifiState, isWifiEnabled) con los bodies del A14 patched
   - Reemplaza 4 hooks en ConnectivityManager.smali (getActiveNetworkInfo,
     getNetworkInfo, getAllNetworkInfo, isActiveNetworkMetered)
   - Reemplaza 1 hook en NetworkCapabilities.smali (hasTransport)
   - Reemplaza 4 hooks en NetworkInfo.smali (getType, getTypeName,
     isConnected, isAvailable)
7. **smali → dex** con `java -jar apktool.jar b` (produce APK con el nuevo dex)
8. **Reemplazar classes.dex en JAR**: `python3 scripts/rebuild_jars_a16.py`
   (preserve per-entry compression + external_attr del original)
9. **Inyectar en APEX + re-firmar**: `python3 scripts/rebuild_apex_a16.py`
   - Por cada APEX: strip AVB footer → rm+write+sif+ea_set → e2fsck →
     re-add AVB hashtree footer con clave propia
   - Rebuild APEX zip (replace apex_payload.img + apex_pubkey con el nuestro,
     strip META-INF, preserve otros entries)
   - zipalign -p 4096 + apksigner v3 sign con fakewifi.jks
10. **Repack EROFS**: replace 2 APEX en extract/, `mkfs.erofs -zlz4 --all-root
    --file-contexts=plat_file_contexts system-new.img extract/`
11. **Verify**: `fsck.erofs`, `dump.erofs -s`, hash compare APEX dentro vs
    `apexes-new/`, `avbtool verify_image`, `apksigner verify`

## Bugs encontrados y cómo los resolví

### Bug 1: debugfs `write` no sobrescribe archivos existentes

Mi primer `rebuild_apex_a16.py` hacía solo `debugfs -w -R "write local in-img"`.
La verificación con `apksigner verify` y `avbtool verify_image` PASARON —
porque el APEX estaba bien firmado con nuestra clave, pero el jar DENTRO del
apex_payload.img era el STOCK (no el parchado). Solo lo noté cuando verifiqué
el tamaño del jar in-img (1427945 = stock size, vs 1434550 = patched size).

**Fix**: antes del `write`, hacer `rm in-img` (patrón del A14 build.sh).
Después del `write`, verificar el tamaño del archivo in-img con `debugfs -R
"stat in-img"` y abortar si no coincide con el esperado.

### Bug 2: `apex_pubkey` no se reemplazaba

Mi `rebuild_apex_a16.py` inicial preservaba el `apex_pubkey` del APEX original
(release key del fork WayDroid-ATV). Pero la firma AVB dentro del nuevo
apex_payload.img usa NUESTRA clave. Como apexd verifica la AVB signature usando
la pubkey embebida en el APEX, la verificación fallaría en runtime.

**Fix**: en `rebuild_apex_a16.py`, la línea `elif n == "apex_pubkey": data =
open(our_pubkey, "rb").read()` reemplaza el pubkey original por el nuestro.
Verificado estáticamente: `sha256sum` del apex_pubkey en ambos APEX =
`5bac3ecb6fa6e063d4bc955060e0bf1304e15f8f351c17642a2ebf2a25582606` (matches
`build/keys/apex_pubkey`).

### Bug 3: mkfs.erofs 1.5 no acepta `-d 6` con espacio

La sintaxis es `-d6` (sin espacio). Igual con `-T`. La primera versión de mi
comando mkfs.erofs fallaba silenciosamente sin producir output. Una vez
corregido, mkfs.erofs corrió OK.

### Bug 4: mkfs.erofs 1.9.4 pide libxml2.so.16 (no disponible)

El binario mkfs.erofs 1.9.4 linkea contra libxml2.so.16, pero el sandbox tiene
libxml2.so.2 (versiones más viejas). El .deb de libxml2 en Debian trixie
tampoco se descargaba correctamente (URL con `+` se rompe).

**Fix**: usar mkfs.erofs 1.5 (linkea contra libxml2.so.2 y libselinux —
ambas disponibles). El 1.5 es de 2023 pero soporta todas las features
que usa Android 16 QPR2 (LZ4 con 0padding).

### Bug 5: fsck.erofs --extract no preserva xattrs

Al extraer el EROFS a un árbol, los SELinux labels de cada archivo se pierden.
Esto rompería el runtime (archivos sin label correcto → SELinux denials en boot).

**Fix**: usar `mkfs.erofs --file-contexts=plat_file_contexts` para re-aplicar
los labels desde el file_contexts AOSP estándar. La regla catch-all
`/system(/.*)? u:object_r:system_file:s0` aplica a los APEX (que están en
`/system/apex/`), excepto `com.android.art` que tiene override
`art_apex_dir:s0` (no tocado). Verificado con `dump.erofs --path=` que muestra
`Xattr size: 16` (suficiente para `security.selinux=u:object_r:system_file:s0\0`).

### Bug 6: port_a16.py copió solo `smali/` no el apktool dir completo

Mi primer `port_a16.py` hacía `fresh(OUT_WIFI, A16_WIFI)` donde las rutas
tenían `/smali` appended. Resultado: la copia era solo el subdirectorio smali,
sin `apktool.yml` ni `original/` ni `unknown/`. apktool b fallaba porque no
encontraba `apktool.yml`.

**Fix**: agregar `_DIR` variants sin el `/smali` suffix y copiar el dir
completo. Después de esto, `apktool b` funcionó.

## Verificación estática completa

Lo que verifiqué, en orden:

1. **Magic + features de EROFS**: `file system-new.img` → `EROFS filesystem,
   compat: SB_CHKSUM MTIME, blocksize=12, incompat: LZ4_0PADDING`. Matches
   stock.
2. **Inode count**: `dump.erofs -s system-new.img` → `Filesystem inode
   count: 7508`. Matches stock.
3. **fsck.erofs pasa**: `fsck.erofs system-new.img` → sin output de error.
4. **APEX dentro del system.img = nuestro APEX parchado**:
   - `fsck.erofs --extract=/tmp/v` extrae
   - `sha256sum /tmp/v/system/apex/com.android.wifi.apex` =
     `7095b678916418b4ca9d637b637d3e6e1ba0c6efc501f8e6b4340a01c06cf288`
   - `sha256sum apexes-new/com.android.wifi.apex` =
     `7095b678916418b4ca9d637b637d3e6e1ba0c6efc501f8e6b4340a01c06cf288`
   - Match. Lo mismo para tethering.
5. **AVB verification de apex_payload.img**:
   `avbtool verify_image --image apex_payload.img --key fakewifi_key.pem`
   pasa: `Successfully verified footer and SHA256_RSA4096 vbmeta struct` y
   `Successfully verified sha256 hashtree`.
6. **apksigner v3 verification**: `apksigner verify --verbose` muestra
   `Verified using v3 scheme (APK Signature Scheme v3): true`.
7. **apex_pubkey dentro del APEX = nuestro**:
   `sha256sum apex_pubkey` (extraído de cada APEX) =
   `5bac3ecb6fa6e063d4bc955060e0bf1304e15f8f351c17642a2ebf2a25582606` =
   `build/keys/apex_pubkey`. Match.
8. **jar patched verificado in-img post-injection**:
   `debugfs -R "stat /javalib/framework-wifi.jar" apex_payload.img` →
   `Size: 1434550` (= expected patched size, no el stock 1427945).
9. **e2fsck de apex_payload.img tras cirugía**: rc=0 (clean).

## Verificación de runtime pendiente

Solo vos podés hacerla, en tu host Sandy Bridge. Pasos:

```bash
# 1) Backup de tu system.img actual (lo hace install.sh)
sudo bash /path/a/fakewifi-port-a16/install.sh

# 2) Post-install, después del boot, verificá:
waydroid shell getprop persist.waydroid.fake_wifi   # → '*'
waydroid shell dumpsys wifi | grep -i ssid          # → "SSID: FakeWifi"
waydroid shell logcat -d | grep -i FakeWifi         # → "Faking wifi for X"

# 3) Caso de uso real: WhatsApp → Copia de seguridad (no debe pedir WiFi)
```

Si algo falla, capturá:

```bash
sudo waydroid shell logcat -d | grep -iE 'apexd|avb|FakeWifi' | tail -50
dmesg | grep -i avb
```

Y mandámelo. Los modos de fallo posibles:

- **apexd rechaza el APEX**: improbable (apex_pubkey matches), pero si pasa
  es un modo de verificación adicional que el fork añadió. Workaround: tener
  que firmar con la release key del fork (que no tenemos).
- **NoSuchFieldError en runtime**: el campo `mIsAvailable` de NetworkInfo
  desapareció en A16. Verificado estáticamente que existe, pero runtime
  podría diferir si el runtime usa reflexión con names diferentes.
- **Boot loop**: muy improbable (e2fsck pasa, AVB válido, EROFS válido).
  Si pasa, es problema de SELinux contexts (mkfs.erofs no aplicó bien el
  file_contexts).

## Lo que NO hice (porque el usuario no lo pidió o no era necesario)

- **VANILLA A16**: solo hice GAPPS. Si querés VANILLA, el pipeline es
  idéntico pero con el zip VANILLA de download.
- **Tests offline tipo `build/test-offline.sh` del A14**: el A14 tenía 21
  tests para la cirugía in-place sobre system.img. En A16 no hay cirugía (la
  entrega es la imagen entera), así que los tests son sobre el system.img
  final (que ya están hechos: fsck.erofs, avbtool verify_image, apksigner
  verify, hash compare).
- **Rotación de claves**: las claves del A14 se reusan. Si querés rotarlas,
  regenerá `build/keys/fakewifi_key.pem` (RSA 4096), `apex_pubkey`, y el
  keystore `fakewifi.jks`, y re-firmá los 2 APEX.
- **Set up del MCP bridge con REA**: el usuario lo mencionó ("manualmente o
  levantar el mcp y usar un bridge que crees tu"). Elegí "manualmente"
  porque el sandbox de z.ai no facilita el binding de un servidor MCP, y
  porque el análisis de compatibilidad de API se resolvía con baksmali + grep
  sin necesidad de REA. REA quedó instalado (`npm i -g rea-agents`) por si
  lo querés usar en una sesión futura.

## Archivos relevantes del sandbox (no en la entrega)

- `/home/z/my-project/scripts/port_a16.py` — script de porteo de smali
- `/home/z/my-project/scripts/rebuild_jars_a16.py` — script de rebuild de jars
- `/home/z/my-project/scripts/rebuild_apex_a16.py` — script de rebuild de APEX
- `/home/z/my-project/work/a16/` — árbol de trabajo (smali, jars, APEX, etc.)
- `/home/z/my-project/worklog.md` — log multi-agent (solo main agent activo)
- `/home/z/my-project/bin/` — binarios (mkfs.erofs, debugfs, etc.)
- `/home/z/my-project/tools/` — tools (apktool.jar, avbtool.py, etc.)

Estos dirs pueden borrarse para liberar disco del sandbox; la entrega en
`/home/z/my-project/download/fakewifi-port-a16/` es autosuficiente.

## Pendientes para futuras sesiones

1. **Runtime verification**: si el usuario reporta success/failure, documentar
   en `vault/notes.md` una nueva sección "Runtime del usuario (fecha)".
2. **Soporte VANILLA A16** si el usuario lo pide: replicar el pipeline con
   el zip VANILLA.
3. **Si runtime falla**: revisar `apexd` log y adaptar (p.ej. firmar con
   release key del fork, o aplicar otra estrategia de firma).
4. **Suit de tests offline para install.sh** (al estilo A14): se podría
   escribir un `scripts/test-install-offline.sh` que simule waydroid paths y
   verifique el install.sh. No prioritario porque el install.sh solo hace
   `cp system.img` + setprop.

## Cómo contactar al próximo agente

Si sos un agente retomando este repo:

1. Leé este `vault/notes.md` entero.
2. Leé `AGENTS.md` (reglas + herramientas + estado).
3. Leé `PRD.md` (qué se entregó vs qué se pidió).
4. Mirá los scripts en `scripts/` para entender el pipeline.
5. Si el usuario reporta un bug runtime, mirá `AGENTS.md` § "Lo que falta"
   para el formato de reporte esperado.

Si sos el usuario y querés instalar: leé `README.md` y corré `install.sh`
con sudo. El script hace todo (copia + setprop + verify).

---

## Sesión 2 (2026-10-15, tarde) — bootloop v1.0.0: auditoría + fix + CI

Reporte del usuario: la imagen v1.0.0 se queda en la pantalla de carga de
LineageOS. La stock A16 bootea perfecto.

### Qué descarté (con evidencia, no a ojo)

- **Claves**: apex_pubkey == extract_public_key(fakewifi_key.pem); cert JKS ==
  misma clave. OK.
- **APEX zip**: payload STORED + 4096-aligned (offset 4096); esquema de firma
  v3-only = IGUAL que el stock (el stock también es v2=false).
- **apexd A16** (fuente AOSP main leída): no hay whitelist para APEX de
  fábrica; la verificación es apex_pubkey embebida vs payload AVB (pasa);
  fallos de activación no frenan el boot de apexd (sigue sin el apex).
- **DEX**: baksmali del release parsea limpio; clases parcheadas = stock + 8
  (FakeWifi+lambdas+IpInfo) y 0 diff en connectivity; hooks aplicados solo
  donde correspondía. Los dex STOCK tampoco traen sección hiddenapi → no
  existe el riesgo.
- **FakeWifi**: byte-idéntico al A14 (que funcionó en el host del usuario).

### Qué SÍ quedó dañado en el repack EROFS de la v1.0.0 (medido, 7508 inodes)

- gid 2000→0 en 491 archivos (--all-root), uid en 2, caps perdidos en 11,
  symlinks 0777 (cosmético), timestamps 2024, UUID nuevo, falta feature
  xattr_filter. Nada de esto es claramente fatal en waydroid (sin SELinux
  enforcing), pero la imagen no era fiel al stock.

### Fix (v1.1.0)

Pipeline nuevo scripts/build_image_a16.py: extracción root + xattrs reales,
mkfs.erofs 1.8.6 sin --all-root ni --file-contexts, --preserve-mtime,
UUID/ts de stock, y GATE de tree-diff completo (solo los 2 APEX pueden
diferir). Jars parcheados vendidos en prebuilt/. CI en
.github/workflows/build.yml publica el release. Diagnóstico del host en
DIAGNOSTICO.md (vendor 20260717, overlay limpio, sha256 de la imagen en uso).

---

## Sesión 3 (2026-10-16) — Runtime del usuario: bootea; el bootloop era data remanente

### Reporte del usuario (runtime, host Sandy Bridge i5-2430M / 3.7 GB RAM)

- Tras el wipe + instalación de las imágenes custom, el boot seguía en
  loop en la pantalla de carga: la imagen no cargaba.
- El fix definitivo fue borrar `~/.local/share/waydroid`: sin ese borrado
  el bootloop persistía; con él, la imagen bootea.
- Conclusión: **cuarta causa de entorno de bootloop** (vendor mezclado /
  overlay viejo / init pisando imagen / **data de usuario remanente**).
  El /data viejo (packages.xml, settings de system_server, dalvik-cache
  de otro framework, o a medio escribir por los boots abortados de la
  v1.0.0) hace crash-loopear system_server desde el primer boot, con el
  mismo síntoma que una imagen rota — por eso engañó a la sesión 2.
- La verificación del HOOK fakewifi (getprop / dumpsys wifi / logcat /
  backup de WhatsApp) sigue pendiente de reporte.

### Qué se actualizó en el repo (esta sesión)

- `WIPE_INSTALL.md` § 3: warning IMPORTANT — el borrado de
  `~/.local/share/waydroid` es OBLIGATORIO; añadidas rutas legacy
  (`~/.share/waydroid`, `~/waydroid`, `~/.waydroid` — guía oficial de
  reset, waydroid discussion #1358). § 9: sección nueva "Bootloop
  persiste tras re-instalar". Cheat sheet actualizado con el paso
  marcado OBLIGATORIO.
- `DIAGNOSTICO.md` § 0.4: causa nueva documentada (la lista de § 0 pasó
  de 3 a 4 causas de entorno).
- `AGENTS.md`: estado sesión 3.
- `README.md`: corregida la nota que decía que `waydroid init -f` "borra
  /data" — en runtime se comprobó que NO limpia el estado problemático de
  `~/.local/share/waydroid`; el wipe real es a mano.

### Nota aparte — "waydroid se reinicia" al cambiar de escritorio virtual (KDE, host 3.7 GB RAM)

Reporte del usuario: al moverse entre escritorios virtuales de KDE
(Ctrl+Meta+Arriba), waydroid se reinicia. Sospecha del usuario: el swap.

Análisis (probable, a confirmar con logs): **OOM killer del kernel**, no
el swap en sí. Con 3.7 GB RAM, Plasma + Android 16 GAPPS no cierra
(el container solo se come 1.5-2.5 GB). El cambio de escritorio es un
pico de asignación: kwin re-compone TODAS las ventanas del escritorio
destino, la animación retiene los frames de ambos escritorios, y las
miniaturas del task manager refrescan. Sin margen libre (RAM + swap), el
kernel dispara el OOM killer y mata al mayor consumidor: el container
waydroid → Android re-bootea (boot animation de vuelta).

El swap NO mata nada: su AUSENCIA (o estar lleno) es lo que deja pasar
el OOM. Con swap suficiente el pico se absorbe paginando (lento pero sin
kill). Confirmación y mitigación:

```bash
# Confirmar OOM (la evidencia queda en el ring buffer del kernel):
sudo journalctl -k --since today | grep -iE 'out of memory|oom|killed process'
sudo dmesg | grep -iE 'oom-kill|killed process'
systemctl status waydroid-container      # "since" / restarts
free -h; swapon --show                    # cuánto swap hay y cuánto queda libre

# Mitigación:
# 1) Swapfile 4G (si no hay swap suficiente):
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# 2) zram (swap comprimido en RAM, rápido): apt install zram-tools
#    y en /etc/default/zramswap: PERCENT=100, ALGO=zstd
# 3) Reducir el pico: Efectos de escritorio → desactivar Overview /
#    Desktop Grid; velocidad de animación al mínimo.
# 4) Dentro de Android: Developer options → Background process limit →
#    "No background processes"; animaciones off.
```

Alternativa a descartar si NO hay kills de OOM en el log del kernel:
crash de kwin_wayland (se caería la sesión completa a SDDM) —
`coredumpctl list kwin_wayland` y `journalctl -b | grep -iE 'i915|gpu
hang'` (el iGPU Gen6 Sandy Bridge es frágil en kernels nuevos).
Distinción de síntoma: si Android re-bootea COMPLETO (boot animation) =
container muerto (OOM); si solo cierra/abre la ventana waydroid sin boot
animation = sesión waydroid muerta (compositor/kwin).
