# DIAGNOSTICO.md — Cómo capturar logs del bootloop (y descartar causas de entorno)

> Si la imagen parcheada se queda en la pantalla de carga de LineageOS,
> capturá esto y pegalo en el issue / mandaselo al asistente. Con estos
> logs se identifica en qué etapa murió el boot (apexd / zygote /
> system_server) sin adivinar.

## 0. Antes de loguear: descartar causas de entorno (2 minutos)

Estas 3 cosas producen EXACTAMENTE el mismo síntoma (bootloop en splash)
con una imagen perfecta, y son las más comunes:

### 0.1 Vendor.img del MISMO release

El vendor.img difiere entre releases de WayDroid-ATV. Mezclar
`system.img 20260717` con un `vendor.img` de `20260302` (o al revés)
da bootloop seguro. Verificá:

```bash
# el vendor debe ser el de 20260717:
# https://github.com/WayDroid-ATV/waydroid-builds/releases/download/20260717/lineage-23.2-20260717-MAINLINE-waydroid_x86_64-vendor.zip
sha256sum /etc/waydroid-extra/images/vendor.img
# el sha256 del zip vendor 20260717 es: 28dfbd0e423dff6d5c7d2e34e15011fbaf4865da769d9923b84656d87e9f4d3c (del ZIP; el vendor.img interno debe extraerse de ese zip)
```

Si tenés duda: re-descargá el vendor 20260717 y copialo de nuevo.

### 0.2 Overlay viejo de A14

```bash
sudo systemctl stop waydroid-container; waydroid session stop
sudo rm -rf /var/lib/waydroid/overlay*
sudo systemctl start waydroid-container
```

Un overlay de la era A14 (props, build.prop viejo, apex viejo) pisando
paths de la imagen A16 tira el boot.

### 0.3 `waydroid init -f` DESPUÉS de copiar la imagen

`waydroid init -f` con flags OTA puede re-descargar y PISAR el
system.img parcheado con el stock. Orden correcto: init primero,
copia de la imagen DESPUÉS. Verificá que la imagen en uso es la
parcheada:

```bash
sudo sha256sum /etc/waydroid-extra/images/system.img
# debe coincidir con el sha256sum publicado en el release que instalaste
```

## 1. Captura de logs del bootloop

Con la imagen parcheada instalada y el boot colgado en la pantalla de
carga (dejalo 3-5 minutos colgado antes de capturar):

```bash
# A) logcat del container (si waydroid shell responde)
sudo waydroid shell logcat -d > /tmp/logcat-boot.txt 2>&1 || true

# B) logs del host relevantes
sudo journalctl -u waydroid-container --no-pager | tail -100 > /tmp/container.log
sudo dmesg | grep -iE 'erofs|loop|dm-|avb|ext4' | tail -50 > /tmp/dmesg-fs.log

# C) estado del container
sudo waydroid status > /tmp/status.txt 2>&1 || true
sudo waydroid shell getprop > /tmp/getprop.txt 2>&1 || true
```

Si `waydroid shell` NO responde (boot muy temprano), capturá el logcat
por red o directamente del log del container LXC:

```bash
# el PID del init del container:
sudo ls /proc | while read p; do
  grep -l "u:r:init:s0" /proc/$p/attr/current 2>/dev/null; done 2>/dev/null

# y el log de la sesión (el .ini window / logcat en vivo):
waydroid logcat 2>/dev/null | head -200
# o:
sudo tail -100 /var/lib/waydroid/waydroid.log
```

## 2. Qué mirar en los logs (mapeo síntoma → causa)

| Lo que aparece en logcat | Causa | Fix |
|---|---|---|
| `apexd: Failed to verify ... com.android.wifi` o `...tethering` | rechazo del APEX re-firmado | reportar (no debería pasar: verificación estática OK) |
| `apexd: ... activation failed` + `system_server` repetido con `ClassNotFoundException` (android.net.connectivity / wifi) | APEX no activado → system_server crash-loop | el APEX no montó; mirar `dmesg` loop/dm |
| `UnsupportedClassVersionError` / `VerifyError` en framework-wifi o connectivity | dex parcheado roto | reportar con el log completo |
| zygote muere antes de `SystemServer` | problema de imagen base | tree-diff del release debe estar limpio; verificar sha256 |
| No llega ni a zygote (surfaceflinger OK, nada después) | problema de entorno (vendor/overlay/data) | sección 0 |

## 3. Verificación post-boot (cuando bootee)

```bash
waydroid shell getprop persist.waydroid.fake_wifi   # → el valor seteado
waydroid shell dumpsys wifi | grep -i ssid          # → "SSID: FakeWifi"
waydroid shell logcat -d | grep -i FakeWifi         # → "Faking wifi for X"
waydroid shell dumpsys connectivity | grep -iE 'type|transport' | head -10
```

Caso de uso final: WhatsApp → Ajustes → Chats → Copia de seguridad
(no debe pedir WiFi).

## 4. Comparación 20260302 vs 20260717 (por qué importa el vendor)

| Release | system GAPPS (sha256 zip) | vendor MAINLINE (sha256 zip) |
|---|---|---|
| 20260302 | 397bc8f4... | 2a4d9594... |
| 20260717 | f64a746f... | 28dfbd0e... |

El parche fakewifi-port-a16 está construido SOLO sobre la 20260717.
El vendor debe ser el 20260717 MAINLINE.
