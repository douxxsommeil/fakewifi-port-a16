# AGENTS.md — Guía para asistentes (IA) que retoman este proyecto

Si sos un agente que llega a este repo sin la conversación previa: **este archivo es tu
punto de entrada.** Todo el contexto de la sesión de porteo vive en `vault/notes.md`.

## Estado actual (cierre de sesión 2026-10-15)

- **fakewifi-port-a16** entregado y verificado estáticamente en el sandbox de z.ai.
  El `system.img` (1.95 GB, EROFS, LZ4) tiene los 2 APEX parchados dentro (firmados
  con clave propia AVB SHA256_RSA4096 + APK v3) y los APEX contienen los jars con
  los hooks + la clase FakeWifi (sin API drift respecto al A14).
- **Verificación pendiente de runtime** en el host del usuario (Sandy Bridge i5-2430M,
  3.7 GB RAM, Debian trixie): el sandbox de z.ai no corre waydroid ni hardware
  Android. Solo el usuario puede validar boot + apexd + hook activo en runtime.
- **Claves**: se reusaron íntegramente las del repo A14 (`build/keys/`). No se
  rotaron. Si en algún momento el usuario quiere rotarlas, regenerarlas y re-firmar
  ambos APEX + repack EROFS.

## Por dónde empezar (en este orden)

1. `README.md` — el pitch de instalador + contenido del paquete + limitaciones.
2. `vault/notes.md` — la crónica completa de la sesión: entorno, decisiones,
   bugs encontrados, verificaciones estáticas realizadas.
3. `PRD.md` — el product requirements document (qué se pidió, qué se entregó,
   qué se desechó).
4. `scripts/port_a16.py` — el script de porteo de smali A14→A16 (punto de
   entrada al pipeline de build).
5. `scripts/rebuild_apex_a16.py` — el script de inyección en APEX + re-firma
   (incluye la lógica crítica de `rm` + `write` en debugfs, sin la cual el jar
   parchado no se reemplaza).

## Reglas duras del proyecto

- **Runtime = SOLO el host del usuario.** En el sandbox de z.ai no hay waydroid
  ni hardware Android. El análisis es estático y la verificación final la hace el
  usuario, que reporta por escrito (ver `vault/notes.md` § "Verificación de
  runtime pendiente" para el formato de reporte esperado).
- **NO modificar `install.sh` ni los APEX**: sus sha256 están publicados en
  `sha256sum`. Si hay un bug, regenerar el sistema completo (y actualizar hashes).
- **Las claves `build/keys/` son reproducibles y públicas**. La "confianza" de un
  APEX preinstalado en la imagen viene de estar en la imagen, no de la clave.
  Rotar claves requeriría re-firmar APEX + repack EROFS + actualizar `sha256sum`
  y `README.md`.
- **Solo para la build 20260717 GAPPS de WayDroid-ATV.** No usar sobre VANILLA
  A16 (tamaño y metadata distintos) ni sobre A14/A15.
- **El spoof de fingerprint ya viene de fábrica en A16** (lo hace el fork
  WayDroid-ATV en su build). Este repo NO incluye `install-spoof.sh`. Si venías
  del A14 y dependías del spoof, ya no lo necesitás en A16.

## Lo que falta (en orden de prioridad)

1. **Verificación de runtime (USUARIO)**: instalar el `system.img` en su host,
   boot, y reportar:
   - `waydroid shell getprop persist.waydroid.fake_wifi` → debe ser `*`
   - `waydroid shell dumpsys wifi | grep -i ssid` → debe contener "FakeWifi"
   - `waydroid shell logcat -d | grep -i FakeWifi` → debe tener "Faking wifi for X"
   - WhatsApp backup "solo con WiFi" → debe pasar sin pedir WiFi
2. **Si apexd rechaza los APEX en boot**: capturar
   `waydroid shell logcat -d | grep -iE 'apexd|avb'` y reportar. La causa más
   probable sería una discrepancia entre la `apex_pubkey` embebida en el APEX y
   la clave AVB del payload — ya está verificado estáticamente que coinciden,
   pero runtime podría revelar otra cosa.
3. **Si una app específica no funciona**: puede ser que la app consulte interfaces
   a nivel nativo (no vía ConnectivityManager/WifiManager). El hook solo cubre
   el framework Java. Documentar el caso en `vault/notes.md`.

## Notas técnicas críticas (bugs encontrados durante el porteo)

1. **debugfs `write` no sobrescribe archivos existentes** — falla
   silenciosamente. El patrón correcto (usado por el A14 build.sh y replicado
   acá en `rebuild_apex_a16.py`) es `rm <in-img-path>` ANTES de `write`. Sin
   este `rm`, el APEX resultante tiene la firma correcta pero el jar stock (no
   parchado) — pasa verificación estática pero no activa el hook en runtime.
   El script ahora verifica tamaño del jar in-img vs esperado y aborta si no
   coinciden.
2. **`apex_pubkey` debe ser reemplazado en el APEX zip** con el nuestro (no
   preservar el del stock APEX). Si se conserva el pubkey original (release key
   del fork WayDroid-ATV), apexd falla la verificación porque la pubkey no
   puede verificar nuestra nueva AVB signature. El script ahora lo reemplaza.
3. **EROFS repack preserva xattrs vía `--file-contexts`** (no los conserva por
   defecto al extraer con `fsck.erofs --extract`). Se usa
   `extract/system/etc/selinux/plat_file_contexts` como contexts file, que
   asigna los labels correctos vía regla catch-all
   `/system(/.*)? u:object_r:system_file:s0`. Para `/system/apex/com.android.art`
   existe override (`art_apex_dir:s0`) — los demás APEX caen al catch-all.
4. **mkfs.erofs 1.5 funciona sin libxml2.so.16** (la versión 1.9.4 sí la pide).
   Se usó erofs-utils 1.5 para el repack (compatible con el sistema sin
   instalar libxml2 nuevo). El `fsck.erofs` y `dump.erofs` 1.9.4 se usaron
   para verificación porque tienen más features (--extract, --path).
5. **apktool 3.0.3 b includes baksmali+smali 3.0.9-dev** que soporta DEX 039+
   (Android 16). No se necesitan baksmali.jar / smali.jar standalone.

## Herramientas usadas (todas instaladas en el sandbox sin sudo)

- `e2fsprogs 1.47.4` (debugfs, e2fsck) vía extracción de `.deb` (paquete
  `libext2fs2t64`)
- `erofs-utils 1.5` (mkfs.erofs) + `erofs-utils 1.9.4` (fsck.erofs, dump.erofs)
  — ambos extraídos de `.deb` de Debian trixie
- `attr 2.6.0` (setfattr, getfattr) — extraído de `.deb`
- `Android build-tools r34` (zipalign, apksigner.jar) — descargado de
  `dl.google.com/android/repository/build-tools_r34-linux.zip`
- `apktool 3.0.3` — descargado de GitHub releases
- `avbtool.py` (Python, AOSP main) — descargado de googlesource
- `REA 6.5.0` (npm i -g rea-agents) — instalado pero no usado (no había JADX
  prebuilt en el sandbox, y el análisis de compatibilidad de API se hizo
  directo con baksmali + grep). Disponible para futuras sesiones.
- `Java 21` (OpenJDK) — ya en el sandbox

## Reproducibilidad

Para regenerar `system.img` desde cero:

```bash
# 1. Setup sandbox (ver vault/notes.md § "Setup del sandbox" para paths)
# 2. Descargar imagen A16 GAPPS de WayDroid-ATV 20260717
# 3. Extraer EROFS, ubicar 2 APEX stock
# 4. Desempaquetar APEX, dump framework jars
# 5. baksmali con apktool 3.0.3
# 6. Aplicar hooks: python3 scripts/port_a16.py
# 7. Rebuild jars con nuevo dex: python3 scripts/rebuild_jars_a16.py
# 8. Rebuild APEX (injection + AVB + APK sign): python3 scripts/rebuild_apex_a16.py
# 9. Reemplazar 2 APEX en extract tree, repack EROFS con mkfs.erofs
# 10. Verificar con fsck.erofs + dump.erofs + avbtool verify_image + apksigner verify
```

Todos los scripts son idempotentes (re-ejecutables) y persisten en
`/home/z/my-project/scripts/` (sandbox) y en el paquete (`scripts/`).
