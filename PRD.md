# PRD — fakewifi-port-a16

**Producto**: Parche binario fake-wifi para la imagen WayDroid-ATV 20260717 GAPPS
(Android 16 QPR2 / LineageOS 23.2).
**Estado**: Entregado, verificación estática completa, runtime pendiente.
**Fecha de cierre**: 2026-10-15.
**Sandbox de build**: z.ai (no Colab, no host del usuario).

## 1. Problema

El usuario venía usando `fakewifi-waydroid-a14` (parche binario sobre la imagen A14
20260125) porque la imagen A16 de WayDroid-ATV (liberada 2026-07-17), aunque más
moderna (security patch jul-2026, Material 3 Expressive, GPU crocus estable en su
Sandy Bridge), **no trae los parches `base-patches-33` de rikka** que sí traen las
imágenes oficiales A13 de waydroid upstream. Por eso `waydroid prop set
persist.waydroid.fake_wifi` no hacía nada — las apps que exigen WiFi (caso típico:
backup de WhatsApp "solo con WiFi") fallaban en A16.

El usuario probó A16 antes y se bajó a A14 por esta razón. Ahora quiere volver a
A16 y llevarse el fakewifi.

## 2. Requisitos

### Funcionales

- **F1**: La imagen A16 GAPPS (build 20260717) debe reportar `TYPE_WIFI` /
  `TRANSPORT_WIFI` a las apps que consultan `ConnectivityManager` o `WifiManager`
  cuando `persist.waydroid.fake_wifi` está seteado.
- **F2**: La activación debe ser por prop persistente
  (`persist.waydroid.fake_wifi`), soportando el valor `*` (todas las apps) y
  patrones tipo `com.whatsapp,com.google.*`.
- **F3**: El parche debe sobrevivir `waydroid init -f` y wipes de overlay (es
  decir, debe vivir DENTRO de la imagen, no en `/var/lib/waydroid/overlay*`).
- **F4**: El parche debe coexistir con el spoof de fingerprint "Pixel Tablet" que
  el fork WayDroid-ATV ya hornea en sus builds A16. No se debe re-spoofear desde
  este repo (sin `install-spoof.sh`).
- **F5**: Instalación limpia: el usuario puede `rm -rf ~/.local/share/waydroid
  /var/lib/waydroid/overlay*` + `sudo waydroid init -f` + instalar este system.img
  y tener fakewifi funcionando (con el setprop post-install).

### No funcionales

- **NF1**: No requiere recompilar la imagen Android desde fuente.
- **NF2**: No requiere Magisk/LSPosed/Xposed.
- **NF3**: No requiere sudo para construir en el sandbox (extracción manual de
  `.deb` para filesystem tools).
- **NF4**: Reproducible: scripts persistidos, claves incluidas, procedimiento
  documentado.
- **NF5**: Reversible: script `--restore` que devuelve la imagen previa.

## 3. Solución

### Arquitectura

1. **Portear los hooks de A14 patched smali a A16 stock smali** (no reuso directo
   de APEX A14 — verificación de compatibilidad de API hecha, todo OK, pero se
   eligió portear para tener un APEX con smali de A16 puro y evitar riesgos
   menores de API drift futuros).
2. **Reempaquetar los APEX**:
   - Inyectar el framework jar parchado en el `apex_payload.img` (ext4) con
     `debugfs -w -R "rm" + write + sif + ea_set` (patrón A14 build.sh).
   - Re-firmar AVB con `avbtool add_hashtree_footer --algorithm SHA256_RSA4096`
     usando la clave del repo A14 (`build/keys/fakewifi_key.pem`).
   - Re-armar el APEX zip con el nuevo payload + nuestra `apex_pubkey` + los
     archivos originales (manifest, build_info, etc).
   - `zipalign -f -p 4096` + `apksigner sign --v3-signing-enabled true` con el
     keystore `fakewifi.jks` (pass: `fakewifi`).
3. **Repackear la imagen EROFS**:
   - `fsck.erofs --extract` extrae la system.img A16 a un árbol.
   - Reemplazar los 2 APEX en `extract/system/apex/`.
   - `mkfs.erofs -zlz4 --all-root -T1721244507
     --file-contexts=plat_file_contexts` rebuild la imagen con SELinux labels
     re-aplicados desde el file_contexts.
4. **Entregar**:
   - `system.img` (1.95 GB) con los 2 APEX parchados DENTRO (sobrevive wipes).
   - `install.sh` que solo copia el system.img + setprop (no cirugía runtime).
   - Los 2 APEX sueltos en `apexes-new/` para inspección.
   - Las claves y scripts para reproducibilidad.

### Componentes

| Componente | Detalle |
|---|---|
| `system.img` | EROFS, LZ4 con 0padding, 4096 block size, 7508 inodes (igual que stock). 1.95 GB (vs stock 1.80 GB — diferencia por compresión LZ4 menos agresiva en erofs-utils 1.5 vs el 1.9.4 que usó el fork). |
| `apexes-new/com.android.wifi.apex` | 11.13 MB (vs stock 11.09). AVB SHA256_RSA4096 + APK v3. `apex_pubkey` = nuestra. |
| `apexes-new/com.android.tethering.apex` | 35.03 MB (vs stock 34.98). Igual firma. |
| `install.sh` | Image mode: copia el system.img a `images_path/system.img`. NO toca overlay. |
| `build/keys/` | Reusadas del repo A14. RSA 4096 (AVB), JKS pass: fakewifi (APK). |

## 4. Decisiones de diseño

1. **Image mode vs Overlay mode**: se eligió image mode porque el usuario explícitamente
   quiere poder wipear config sin perder el parche. El overlay muere con `rm -rf
   /var/lib/waydroid/overlay*` (troubleshooting estándar), así que no es robusto
   para su caso de uso.
2. **Port direct vs Reuse APEX A14**: se eligió port direct (option C del
   brainstorm) porque, aunque la verificación estática mostró que el APEX A14
   cargaría en A16 (sin NoSuchFieldError), el código smali referenciaría
   versiones A14 de las APIs del framework, con riesgo de comportamiento
   sutilmente distinto. Portear a smali A16 garantiza coherencia.
3. **Reusar claves A14 vs Rotar**: se eligió reusar porque:
   - La confianza de un APEX preinstalado viene de estar en la imagen, no de la
     clave (per README del A14 repo).
   - Rotar requeriría re-firmar APEX + repack EROFS + actualizar `sha256sum`.
   - El usuario ya tenía las claves del A14 funcionando.
4. **No incluir spoof**: el fork WayDroid-ATV ya lo hornea en sus builds A16 (ver
   `vault/evidencia/spoof-a16-buildprops-20261006.txt` del repo A14). Re-spoofear
   sería sobreescribir con el mismo valor.
5. **EROFS repack con `--file-contexts`**: preserva SELinux labels vía el
   file_contexts AOSP estándar. Regla catch-all `/system(/.*)? u:object_r:system_file:s0`
   aplica a los APEX (que están en `/system/apex/`), EXCEPTO `com.android.art`
   que tiene override `art_apex_dir:s0` (no tocado).

## 5. Verificación estática realizada

- ✅ `fsck.erofs system-new.img` pasa sin errores
- ✅ `dump.erofs -s` muestra magic 0xE0F5E1E2, LZ4_0PADDING, 7508 inodes (igual
  que stock)
- ✅ `dump.erofs --path=/system/apex/com.android.wifi.apex system-new.img`
  muestra el archivo in-place
- ✅ Hash del APEX dentro del system.img = hash del APEX en `apexes-new/` (es el
  mismo archivo)
- ✅ `avbtool verify_image --image apex_payload.img --key fakewifi_key.pem`
  pasa para ambos APEX
- ✅ `apksigner verify --verbose` pasa para ambos APEX (v3 scheme)
- ✅ `sha256sum` de `apex_pubkey` en ambos APEX = `5bac3ecb6fa6e063d4bc955060e0bf1304e15f8f351c17642a2ebf2a25582606`
  (matches nuestra `build/keys/apex_pubkey`)
- ✅ Tamño del jar parchado verificado post-injection en el apex_payload.img
  (1434550 bytes para wifi, 2052314 para connectivity — ambos son los patched sizes)
- ✅ `e2fsck -fn apex_payload.work` pasa tras cirugía (rc=0)

## 6. Verificación de runtime pendiente

Solo el usuario puede hacerla (en su host Sandy Bridge). Pasos:

```bash
# Tras install.sh + boot:
waydroid shell getprop persist.waydroid.fake_wifi   # → '*'
waydroid shell dumpsys wifi | grep -i ssid          # → "SSID: FakeWifi"
waydroid shell logcat -d | grep -i FakeWifi         # → "Faking wifi for X"
# Caso de uso final: WhatsApp → Copia de seguridad → debería pasar sin pedir WiFi
```

Si runtime falla, el diagnóstico es:

```bash
sudo waydroid shell logcat -d | grep -iE 'apexd|avb|FakeWifi' | tail -50
dmesg | grep -i avb
```

## 7. Out of scope (no entregado en esta sesión)

- **Soporte para VANILLA A16**: el system.img entregado es la variante GAPPS.
  Si el usuario quiere VANILLA, el pipeline es idéntico pero hay que bajarse la
  VANILLA zip y correr todo de nuevo.
- **Soporte para A14/A15**: fuera de scope. El repo A14 original ya lo cubre.
- **Rotación de claves**: posible pero fuera de scope de esta sesión.
- **Tests offline de install.sh**: el A14 tenía `build/test-offline.sh` con 21
  tests. Para A16 no se construyó suite de tests offline porque la entrega es
  la imagen entera (no una cirugía), entonces los tests son sobre el system.img
  final (que ya están hechos: fsck.erofs, dump.erofs, avbtool verify_image,
  apksigner verify, hash compare).

## 8. Riesgos residuales

- **R1**: Si Android 16 QPR2 cambia el campo `mIsAvailable` de `NetworkInfo`
  (campo privado que FakeWifi modifica por reflexión), runtime fallará con
  `NoSuchFieldException`. Verificado estáticamente que el campo existe en A16
  (smali de NetworkInfo en `a16-conn/smali/android/net/NetworkInfo.smali`
  declara `mIsAvailable:Z`).
- **R2**: Si apexd en A16 añade verificación de "key whitelist" (que la pubkey
  del APEX esté en una lista permitida), rechazará nuestro APEX firmado con
  clave propia. Verosímilmente no lo hace (A14 ya lo permitía), pero solo
  runtime lo confirmará.
- **R3**: Si el EROFS rebuildeado con erofs-utils 1.5 tiene algún feature
  incompat con el `erofs.ko` del kernel del host del usuario (6.12, Debian
  trixie), el montaje fallará. erofs-utils 1.5 es de 2023, soporta todas las
  features que usa Android 16 QPR2.
