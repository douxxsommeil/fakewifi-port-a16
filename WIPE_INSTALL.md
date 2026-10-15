# WIPE_INSTALL.md — Cómo wipear Waydroid e instalar fakewifi-port-a16

> Guía paso a paso para tu escenario: venís del A14 con fakewifi,
> querés wipear todo, instalar la imagen A16 GAPPS y tener fakewifi
> funcionando. Host target: Sandy Bridge i5-2430M, Debian trixie,
> waydroid 1.6.3.

## 0. Requisitos previos

Verificá que tenés waydroid 1.6.3 o mayor (la release A16 20260717 lo
exige explícitamente — *"Waydroid integration is now fully functional
(requires Waydroid version 1.6.3 or later)"*):

```bash
waydroid --version
# debe imprimir 1.6.3 (o mayor)
```

Si tenés menos de 1.6.3, actualizá waydroid primero:

```bash
sudo apt update && sudo apt install waydroid
# o, si lo instalaste via script:
sudo /usr/bin/waydroid-updater  # o bajá el .deb de https://docs.waydro.id/
```

### Descargar el system.img parchado + vendor.img

El system.img se entrega en 2 chunks (por limitación de tamaño de
asset de GitHub — un solo archivo >1.5 GB da OOM en el sandbox al
subir). Bajá los 2 chunks desde el release v1.0.0:

```bash
# 2 chunks del system.img parchado (cat = system.img de 1.95 GB)
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part0
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part1

# Reensamblar
cat system.img.part0 system.img.part1 > system.img

# Verificar integridad (debe dar 025ccb7fd784d3f312c56b331e49646b0ebf5b36324c4191eeb0adba8bf81474)
sha256sum system.img
# → 025ccb7fd784d3f312c56b331e49646b0ebf5b36324c4191eeb0adba8bf81474  system.img

# Opcional: borrar los chunks
rm system.img.part0 system.img.part1
```

Y descargá el **vendor.img A16** desde la release upstream 20260717
(fakewifi no toca vendor, así que se usa el stock):

```bash
# El vendor es el MAINLINE (no GAPPS) — siempre el mismo para A16
wget https://github.com/WayDroid-ATV/waydroid-builds/releases/download/20260717/lineage-23.2-20260717-MAINLINE-waydroid_x86_64-vendor.zip
unzip lineage-23.2-20260717-MAINLINE-waydroid_x86_64-vendor.zip   # produce vendor.img
```

## 1. Backup (opcional pero recomendado)

Si tenés data en el A14 que querés preservar (apps instaladas, cuentas,
configuraciones de Android), backupeala ahora. El wipe es factory-reset.

```bash
# Lista de apps instaladas (para reinstalar luego)
waydroid shell pm list packages -3 > a14-user-apps.txt

# Backup de APKs de apps de usuario (opcional, lento)
# waydroid shell pm list packages -3 | cut -d: -f2 | while read pkg; do
#   waydroid shell pm path "$pkg"
# done | grep -oE '/data/.*\.apk' | head -20
# (más fácil: usá "Backup/Restore apps and data" de la doc de waydroid)
```

## 2. STOP waydroid (sesión + container)

Antes de tocar cualquier cosa, pará waydroid por completo:

```bash
# Stop session (user-level)
waydroid session stop

# Stop container (system-level — necesita sudo)
sudo systemctl stop waydroid-container

# Verificá que no quede nada corriendo
ps -ef | grep -i waydroid | grep -v grep   # debe dar vacío
```

## 3. WIPE completo (factory-reset)

Esto es lo que la release A16 recomienda explícitamente ("Clean install
is recommended") y lo que borrará tu configuración previa:

```bash
# 1) Data de usuario (apps instaladas, /data del Android, props persistentes)
rm -rf ~/.local/share/waydroid

# 2) Overlays del container (APEX overrides, cfg patches, etc.)
sudo rm -rf /var/lib/waydroid/overlay*

# 3) Estado del container (config generada por waydroid init)
sudo rm -rf /var/lib/waydroid/waydroid.cfg
sudo rm -rf /var/lib/waydroid/host-permissions*
sudo rm -rf /var/lib/waydroid/images   # si existía, se va a recrear

# 4) Imágenes previas (A14 stock o A14 parcheado — se van a pisar igual)
sudo rm -f /etc/waydroid-extra/images/system.img
sudo rm -f /etc/waydroid-extra/images/vendor.img

# Verificá que quedó limpio:
ls -la ~/.local/share/waydroid 2>&1           # debe decir "No such file"
sudo ls /var/lib/waydroid/ 2>&1 | head         # debe dar casi vacío
```

> [!WARNING]
> El wipe borra TODA la data de Android (apps, cuentas, /data del
> container). Es lo que el usuario pidió. Si querés preservar algo
> volvé al paso 1 (backup) antes de seguir.

## 4. COPIAR las imágenes (custom-format)

Este es el "formato custom" que menciona la release A16 (Method 2:
"Download and install manually"). Copiás las imágenes a
`/etc/waydroid-extra/images/` y luego `waydroid init -f` las detecta
automáticamente (waydroid docs § "Using custom Waydroid images").

```bash
# Asegurate de que el dir existe
sudo mkdir -p /etc/waydroid-extra/images/

# Copiá el system.img PARCHEADO de este repo (con fakewifi dentro)
sudo cp /path/a/fakewifi-port-a16/system.img \
        /etc/waydroid-extra/images/system.img

# Copiá el vendor.img A16 MAINLINE (stock, sin parchear)
sudo cp /path/a/vendor.img \
        /etc/waydroid-extra/images/vendor.img

# Verificá tamaños y que existan
ls -la /etc/waydroid-extra/images/
# system.img  debe ser ~1.95 GB (EROFS)
# vendor.img debe ser ~200 MB (ext4)

# Verificá integridad del system.img parcheado (opcional pero recomendado)
sha256sum /etc/waydroid-extra/images/system.img
# debe dar: 025ccb7fd784d3f312c56b331e49646b0ebf5b36324c4191eeb0adba8bf81474
# (este hash es el del system.img PARCHEADO de este repo)
```

## 5. INIT waydroid (detecta las imágenes custom)

`waydroid init -f` sin flags de OTA detecta las imágenes en
`/etc/waydroid-extra/images/` y las usa (esto es lo que waydroid docs
llama "Using custom Waydroid images"):

```bash
sudo waydroid init -f
```

> [!NOTE]
> NO uses los flags `-c`, `-v`, `-r`, `-s` de la Method 1 de la
> release — esos descargan del OTA y pisarían nuestras imágenes
> parchadas con la stock A16. Usamos la Method 2 (imágenes locales).

Salida esperada (algo así):

```
[13:25:34] This isn't an A/B device ... skipping A/B updates
[13:25:34] Overlay configuration: ...
[13:25:34] Found image system.img ... mounting
[13:25:34] Found image vendor.img ... mounting
[13:25:34] Registering Android service entry ...
```

Si waydroid init falla con "no images found", verificá que las rutas
sean exactamente `/etc/waydroid-extra/images/system.img` y
`/etc/waydroid-extra/images/vendor.img` (sin symlinks rotos).

## 6. START waydroid

```bash
sudo systemctl start waydroid-container

# Sesión como tu usuario (no como root — preserva tu data dir)
waydroid session start &
```

Esperá el boot (en Sandy Bridge con 3.7 GB RAM: 40-90s). Monitoreá:

```bash
waydroid shell getprop sys.boot_completed
# debe imprimir "1" cuando Android booteó
```

## 7. ACTIVAR el fakewifi (setprop)

La prop `persist.waydroid.fake_wifi` vive en `/data/property/persistent_properties`.
Sobrevive reboots, **NO sobrevive factory-reset** — por eso se setea
DESPUÉS del primer boot post-wipe:

```bash
# Default: '*' = falsifica WiFi para TODAS las apps
sudo waydroid shell setprop persist.waydroid.fake_wifi '*'

# Verificá que quedó persistente
waydroid shell getprop persist.waydroid.fake_wifi
# debe imprimir: *
```

Alternativas de setprop (elegí UNA según tu caso de uso):

```bash
# Solo WhatsApp (caso típico: backup "solo con WiFi")
sudo waydroid shell setprop persist.waydroid.fake_wifi 'com.whatsapp'

# Whitelist con wildcards (separadas por coma, límite ~91 chars)
sudo waydroid shell setprop persist.waydroid.fake_wifi 'com.whatsapp,com.google.*'

# Quitar el fakewifi (sin desinstalar el parche)
sudo waydroid shell setprop persist.waydroid.fake_wifi ''
```

## 8. VERIFICAR runtime

```bash
# 1) Prop persistente
waydroid shell getprop persist.waydroid.fake_wifi
# → debe imprimir el valor que seteaste (default: *)

# 2) Hook activo en dumpsys
waydroid shell dumpsys wifi | grep -i ssid
# → debe aparecer "SSID: FakeWifi"

# 3) Logs del hook
waydroid shell logcat -d | grep -i FakeWifi
# → "Faking wifi for <package>" cada vez que una app consulta WifiManager

# 4) ConnectivityManager reporta WIFI (no CELLULAR)
waydroid shell dumpsys connectivity | grep -iE 'type|state' | head -10
# → debe ver TYPE_WIFI / TRANSPORT_WIFI
```

Caso de uso real: abrí WhatsApp → Ajustes → Chats → Copia de
seguridad. Antes pedía WiFi; ahora debería pasar directo.

## 9. Si algo falla

### Boot no completa en 240s

```bash
# Verificá que las imágenes están bien
sudo waydroid shell logcat -d 2>/dev/null | tail -50
# si waydroid shell no responde, el boot está trabado

# Diagnóstico de APEX (lo más común si algo se rompe)
sudo waydroid shell logcat -d | grep -iE 'apexd|avb' | tail -50
dmesg | grep -i avb
```

### apexd rechaza un APEX (improbable, verificado estáticamente)

Síntoma: `apexd: Failed to verify ... com.android.wifi.apex`
Causa posible: apex_pubkey no coincide con la firma AVB del payload.
Verificación estática en el repo lo confirma correcto, pero si runtime
discrepa, capturá el log y reportalo (ver `vault/notes.md` § "Lo que
falta").

### NoSuchFieldError en runtime (improbable)

Síntoma: `java.lang.NoSuchFieldError: mIsAvailable` en logcat al
consultar connectivity.
Causa posible: el campo privado `mIsAvailable` de `NetworkInfo` fue
removido o renombrado en algún A16 QPR2 menor release posterior.
Verificación estática: el smali A16 confirma que el campo existe, pero
runtime podría diferir si el framework en runtime tiene otra versión.

### Bridge waydroid0 ausente tras boot

```bash
# Recrear el bridge a mano
sudo /usr/lib/waydroid/data/scripts/waydroid-net.sh start
sudo systemctl restart waydroid-container
```

## 10. Rollback (si te querés volver al A14)

Si runtime no funciona y querés volver al A14:

```bash
sudo systemctl stop waydroid-container
waydroid session stop

# Re-instalá la imagen A14 (necesitás el system.img del repo A14)
sudo cp /path/a/fakewifi-waydroid-a14/system-fakewifi-a14-20260125.img \
        /etc/waydroid-extra/images/system.img

# Vendor del A14 (no se parchea, usá el stock A14)
sudo cp /path/a/vendor-a14.img \
        /etc/waydroid-extra/images/vendor.img

# Re-init
sudo waydroid init -f
sudo systemctl start waydroid-container
waydroid session start &
sudo waydroid shell setprop persist.waydroid.fake_wifi '*'
```

## Resumen rápido (cheat sheet)

```bash
# === DESCARGA (one-time) ===
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part0
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part1
cat system.img.part0 system.img.part1 > system.img
sha256sum system.img   # → 025ccb7fd784d3f312c56b331e49646b0ebf5b36324c4191eeb0adba8bf81474

wget https://github.com/WayDroid-ATV/waydroid-builds/releases/download/20260717/lineage-23.2-20260717-MAINLINE-waydroid_x86_64-vendor.zip
unzip lineage-23.2-20260717-MAINLINE-waydroid_x86_64-vendor.zip

# === WIPE + INSTALL ===
waydroid session stop
sudo systemctl stop waydroid-container
rm -rf ~/.local/share/waydroid
sudo rm -rf /var/lib/waydroid/overlay*
sudo mkdir -p /etc/waydroid-extra/images/
sudo cp system.img    /etc/waydroid-extra/images/system.img
sudo cp vendor.img   /etc/waydroid-extra/images/vendor.img
sudo waydroid init -f
sudo systemctl start waydroid-container
waydroid session start &
# esperá a que bootee (40-90s)
sleep 90 && sudo waydroid shell setprop persist.waydroid.fake_wifi '*'
waydroid shell dumpsys wifi | grep -i ssid   # → "SSID: FakeWifi"
```

## Referencias

- **WayDroid-ATV release 20260717**:
  https://github.com/WayDroid-ATV/waydroid-builds/releases/tag/20260717
- **Waydroid docs § "Using custom Waydroid images"**:
  https://docs.waydro.id/faq/using-custom-waydroid-images
- **Waydroid docs § "Install Instructions"**:
  https://docs.waydro.id/usage/install-on-pc
- **Requisito waydroid ≥ 1.6.3** (release A16):
  changelog bug fixes — "Waydroid integration is now fully functional
  (requires Waydroid version 1.6.3 or later)"
