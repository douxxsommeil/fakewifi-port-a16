# fakewifi-port-a16 — Fake WiFi para WayDroid-ATV A16 (20260717, GAPPS, Android 16 QPR2)

> ¿Sos un asistente/agent retomando este proyecto? Empezá por [`AGENTS.md`](AGENTS.md).
> ¿Sos un humano y querés instalarlo? Seguí [`install.sh`](install.sh) (con sudo).

Parche binario que **añade el fake wifi de Waydroid** (`persist.waydroid.fake_wifi`)
a la imagen **WayDroid-ATV 20260717 GAPPS (LineageOS 23.2 / Android 16 QPR2)**, sin
recompilar la imagen y sin Magisk/LSPosed.

Es la migración del repo `douxxsommeil/fakewifi-waydroid-a14` a A16. Todo el pipeline
de build A14 (port.py, build.sh, stage_exact.py) fue reemplazado por scripts A16
(`scripts/port_a16.py`, `scripts/rebuild_jars_a16.py`, `scripts/rebuild_apex_a16.py`).

**Nov 2026 — Qué cambió respecto al A14**:

- La imagen system de A16 es **EROFS** (no ext4). El instalador A14 hacía cirugía
  `debugfs` in-place sobre `system.img` ext4; en A16 ese método no aplica. En su
  lugar, este repo entrega **un system.img ya repackeado con EROFS** y los 2 APEX
  parchados dentro, listo para copiar a `/etc/waydroid-extra/images/system.img`.
- El spoof de fingerprint **ya viene de fábrica en A16** (lo hace el fork
  WayDroid-ATV), por eso este repo NO incluye `install-spoof.sh`. Si venías del
  A14 y dependías del spoof, ya no lo necesitás.
- La versión de AVB signature y el APK v3 signing son idénticos a A14. Las claves
  AVB y APK se reusan del repo A14 (`build/keys/`).
- El framework-wifi A16 tiene `WifiInfo.Builder` que aún soporta `setSsid([B)` (la
  API `setSsid(WifiSsid)` fue añadida pero la `byte[]` se mantiene por compat);
  la clase `FakeWifi` del A14 se copió verbatim. Todos los hooks de WifiManager /
  ConnectivityManager / NetworkCapabilities / NetworkInfo aplican directo (firmas
  y nombres de campos `mService`/`mContext` iguales en A14 y A16).

## Qué hacen los APEX parchados

| APEX | Jar | Cambios |
|---|---|---|
| `com.android.wifi.apex` | `framework-wifi.jar` | clase `android.net.wifi.FakeWifi` + hooks en `WifiManager` (`getConnectionInfo`, `getDhcpInfo`, `getWifiState`, `isWifiEnabled`) |
| `com.android.tethering.apex` | `framework-connectivity.jar` | hooks en `ConnectivityManager` (`getActiveNetworkInfo`, `getNetworkInfo`, `getAllNetworkInfo`, `isActiveNetworkMetered`), `NetworkCapabilities.hasTransport(WIFI)` y `NetworkInfo` (`getType`, `getTypeName`, `isConnected`, `isAvailable`) |

Es exactamente el comportamiento de las imágenes oficiales A13 de waydroid (la clase
FakeWifi y los hooks se portaron del smali compilado de la imagen A13 oficial, que
sí trae estos parches, al A14 primero y ahora al A16).

## Instalación rápida

> **Primera vez** (o si venís del A14 con fakewifi y querés empezar limpio):
> leé [`WIPE_INSTALL.md`](WIPE_INSTALL.md) — guía paso a paso con el wipe
> completo, instalación en formato custom (imágenes en
> `/etc/waydroid-extra/images/`), y verificación runtime.

```bash
# 1) Descargar el system.img parchado (2 chunks) + vendor.img A16 stock
#    (ver WIPE_INSTALL.md § "0. Requisitos previos" para URLs exactas)
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part0
wget https://github.com/douxxsommeil/fakewifi-port-a16/releases/download/v1.0.0/system.img.part1
cat system.img.part0 system.img.part1 > system.img
sha256sum system.img   # → 025ccb7fd784d3f312c56b331e49646b0ebf5b36324c4191eeb0adba8bf81474

# 2) Wipe + install (cheat sheet; ver WIPE_INSTALL.md para detalle completo)
waydroid session stop
sudo systemctl stop waydroid-container
rm -rf ~/.local/share/waydroid
sudo rm -rf /var/lib/waydroid/overlay*
sudo mkdir -p /etc/waydroid-extra/images/
sudo cp system.img    /etc/waydroid-extra/images/system.img
sudo cp vendor.img   /etc/waydroid-extra/images/vendor.img   # del release upstream A16
sudo waydroid init -f
sudo systemctl start waydroid-container
waydroid session start &
sleep 90 && sudo waydroid shell setprop persist.waydroid.fake_wifi '*'

# Si ya tenés waydroid A16 instalado y solo querés intercambiar el system.img
# por el parchado (sin wipe, preservando tu /data):
sudo bash install.sh

# Para revertir (copia el backup pre-parche):
sudo bash install.sh --restore
```

**Prerrequisito**: tenés que tener WayDroid-ATV A16 (20260717 GAPPS) ya
configurado con `sudo waydroid init` y waydroid host ≥ 1.6.3 (release
A16 lo exige explícitamente: *"requires Waydroid version 1.6.3 or
later"*). La imagen `system.img` que entrega este repo reemplaza el
`system.img` stock — por eso solo funciona con la build 20260717
GAPPS. No uses este system.img sobre A14/A15 ni sobre VANILLA A16.

## Post-install: activar la prop

La prop `persist.waydroid.fake_wifi` vive en `/data/property/persistent_properties`.
Sobrevive reboots, **NO sobrevive factory-reset**. Por eso:

- Tras `sudo bash install.sh`: el script ya hace el `setprop` (default `*`).
- Tras cualquier wipe que afecte `/data` (incluyendo `rm -rf
  ~/.local/share/waydroid`): re-ejecutá el setprop:
  ```bash
  sudo waydroid shell setprop persist.waydroid.fake_wifi '*'
  ```

## Verificar que funciona (runtime)

```bash
waydroid shell getprop persist.waydroid.fake_wifi   # → el valor que seteaste
waydroid shell dumpsys wifi | grep -i ssid          # → "SSID: FakeWifi"
waydroid shell logcat -d | grep -i FakeWifi         # → logs del hook "Faking wifi for X"
```

Caso de uso típico: WhatsApp → Ajustes → Chats → Copia de seguridad (antes pedía
WiFi; ahora pasa sin más).

## Rollback

```bash
sudo bash install.sh --restore
```

Restaura el `system.img` previo desde `/var/lib/waydroid/fakewifi-a16-backup/system.img`.

## Limitaciones / notas

- Si **re-extraés `system.img` del zip original** de WayDroid-ATV 20260717 GAPPS,
  el parche se pierde: re-copiá el system.img de este repo.
- Un `waydroid init -f` **no** borra el parche de imagen (los APEX están dentro
  de la imagen) — pero sí borra `/data`, así que después re-ejecutá el setprop.
- Solo para la build 20260717 GAPPS. Los hashes stock A16 (de la imagen original,
  no de este parchado) están en `sha256sum` como gate potencial (no implementado
  en install.sh porque la entrega es la imagen entera, no una cirugía).
- El reporta de red cambia: la imagen A14 reportaba la red como CELLULAR a nivel
  connectivity service; con el hook, las apps que consultan
  `ConnectivityManager`/`WifiManager` ven WIFI. La nota del changelog A16
  ("Fixed network connection, no more workaround required") parece referirse al
  bridge waydroid0, no a la percepción WiFi de las apps — el fakewifi sigue siendo
  necesario para apps que exigen específicamente WiFi.
- La prop soporta patrones (`com.google.*`) separados por comas, límite ~91 chars
  (límite de props de Android).

## Contenido del paquete

```
system.img                                  EROFS A16 GAPPS con los 2 APEX parchados (1.95 GB)
install.sh                                  instalador + restore + setprop (image mode)
sha256sum                                   hashes de system.img + APEX parchados
apexes-new/                                 los 2 APEX parchados sueltos, para inspección
  com.android.wifi.apex                    (firmado APK v3 + AVB SHA256_RSA4096 con clave propia)
  com.android.tethering.apex               (idem)
build/keys/                                 claves AVB+APK (reproducible)
  fakewifi_key.pem                         RSA 4096 (AVB)
  fakewifi_cert.pem                        cert PEM
  apex_pubkey                               pubkey AVB (embebida en APEX)
  fakewifi.jks                             keystore APK (pass: fakewifi)
  fakewifi_key.der                         PKCS8 DER
scripts/                                    pipeline de build (reproducible)
  port_a16.py                              porta los hooks A14 patched al smali A16
  rebuild_jars_a16.py                      reemplaza classes.dex en framework jars
  rebuild_apex_a16.py                      inyecta jars en APEX, re-firma AVB+APK, rebuild zip
AGENTS.md                                   guía para asistentes que retoman
PRD.md                                      product requirements document
vault/                                      notas de sesión para el humano
  notes.md
```

## Agradecimientos / procedencia

- Parche original: rikka (upstream waydroid `base-patches-33`).
- Clase FakeWifi compilada extraída de la imagen oficial A13 de waydroid.
- Pipeline de build (port + apex rebuild) derivado del repo
  `douxxsommeil/fakewifi-waydroid-a14` (sesión 2026-10-06).
- Claves de firma AVB/APK reusadas del repo A14 (no se rotaron).
