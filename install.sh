#!/bin/bash
# =============================================================================
# install.sh — Instalador de fakewifi-port-a16 (image mode, sin cirugía runtime)
#
# Copia el system.img parchado a la ruta de imágenes de waydroid, reemplazando
# la imagen A16 stock. El parche fakewifi ya está DENTRO de la imagen (en los
# APEX com.android.wifi y com.android.tethering), así que sobrevive a
# `waydroid init -f` y a wipes de overlay.
#
# Uso:
#   sudo bash install.sh                       # imagen + setprop fake_wifi='*'
#   sudo bash install.sh --prop='com.whatsapp' # valor custom de la prop
#   sudo bash install.sh --restore             # revierte a la imagen previa (backup)
#   sudo bash install.sh --help
#
# Requisitos host: waydroid ≥1.6.3, sudo. No requiere debugfs/e2fsck.
# =============================================================================
set -euo pipefail

LOG_TAG="[fakewifi-a16]"
info() { echo "$LOG_TAG $*"; }
warn() { echo "$LOG_TAG ADVERTENCIA: $*" >&2; }
die()  { echo "$LOG_TAG ERROR: $*" >&2; exit 1; }

# --- Defaults ---
PROP_VALUE='*'
MODE=install
HELP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --restore)    MODE=restore; shift ;;
    --prop=*)     PROP_VALUE="${1#*=}"; shift ;;
    -h|--help)    HELP=1; shift ;;
    -*)           die "opción desconocida: $1 (usa --help)" ;;
    *)            die "argumento de más: $1 (usa --help)" ;;
  esac
done

if [ $HELP -eq 1 ]; then
  sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

[ "$(id -u)" -eq 0 ] || die "ejecuta con sudo"

# --- Paths ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYSIMG_SRC="$SCRIPT_DIR/system.img"
[ -f "$SYSIMG_SRC" ] || die "no encuentro $SYSIMG_SRC"

WORK="${WAYDROID_WORK:-/var/lib/waydroid}"
CFG="$WORK/waydroid.cfg"
BACKUP_DIR="$WORK/fakewifi-a16-backup"
STATE_FILE="$WORK/fakewifi-a16-state"

# Detectar images_path desde waydroid.cfg (default /etc/waydroid-extra/images)
images_path() {
  local p
  p=$(grep -E '^images_path' "$CFG" 2>/dev/null | head -1 | cut -d'=' -f2 | tr -d ' ')
  echo "${p:-/etc/waydroid-extra/images}"
}

SYSIMG_DST="$(images_path)/system.img"

# --- waydroid lifecycle ---
stop_waydroid() {
  info "deteniendo waydroid..."
  waydroid session stop >/dev/null 2>&1 || true
  systemctl stop waydroid-container >/dev/null 2>&1 || true
  sleep 2
  # desmontar rootfs si quedó montado
  local i
  for i in 1 2 3; do
    mountpoint -q "$WORK/rootfs" 2>/dev/null || break
    umount "$WORK/rootfs/vendor" 2>/dev/null || true
    umount "$WORK/rootfs" 2>/dev/null || true
    sleep 1
  done
}

start_waydroid() {
  info "arrancando waydroid..."
  systemctl start waydroid-container >/dev/null 2>&1 || true
  # fix del bridge waydroid0 si no se recreó
  local i netsh
  for i in 1 2 3 4 5; do
    ip link show waydroid0 >/dev/null 2>&1 && break
    sleep 2
  done
  if ! ip link show waydroid0 >/dev/null 2>&1; then
    for netsh in /usr/lib/waydroid/data/scripts/waydroid-net.sh \
                /usr/share/waydroid/data/scripts/waydroid-net.sh; do
      [ -x "$netsh" ] && break
    done
    if [ -x "$netsh" ]; then
      info "waydroid0 ausente; ejecutando $netsh start"
      "$netsh" start >/dev/null 2>&1 || true
      sleep 2
    fi
  fi
  # sesión como el usuario que invocó sudo
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ] && command -v runuser >/dev/null; then
    (setsid runuser -u "$SUDO_USER" -- waydroid session start >/dev/null 2>&1 &)
  else
    (setsid waydroid session start >/dev/null 2>&1 &)
  fi
}

wait_boot() {
  info "esperando boot (hasta 240s)..."
  local i b
  for i in $(seq 1 120); do
    sleep 2
    b=$(waydroid shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
    [ "$b" = "1" ] && { info "boot OK"; return 0; }
  done
  return 1
}

set_prop() {
  local v
  waydroid shell "setprop persist.waydroid.fake_wifi '$1'" >/dev/null 2>&1 || true
  v=$(waydroid shell getprop persist.waydroid.fake_wifi 2>/dev/null | tr -d '\r' || true)
  if [ "$v" = "$1" ]; then
    info "prop persistente activa: persist.waydroid.fake_wifi=$v"
    return 0
  fi
  warn "la prop no se confirmó vía getprop ('$v'). Actívala a mano:"
  warn "  sudo waydroid shell setprop persist.waydroid.fake_wifi '$1'"
  return 1
}

# =============================================================================
# RESTORE
# =============================================================================
if [ "$MODE" = "restore" ]; then
  stop_waydroid
  if [ ! -f "$BACKUP_DIR/system.img" ]; then
    die "no hay respaldo en $BACKUP_DIR/system.img. Si querés volver al stock, re-extraé system.img del zip original de WayDroid-ATV 20260717."
  fi
  info "revirtiendo a la imagen previa desde $BACKUP_DIR..."
  cp "$BACKUP_DIR/system.img" "$SYSIMG_DST"
  rm -f "$STATE_FILE"
  info "imagen restaurada ✓"
  start_waydroid || warn "no pude arrancar waydroid; hacelo a mano: sudo systemctl start waydroid-container"
  exit 0
fi

# =============================================================================
# INSTALL (image mode)
# =============================================================================
stop_waydroid

# 1. Backup de la imagen actual
if [ ! -f "$BACKUP_DIR/system.img" ]; then
  if [ -f "$SYSIMG_DST" ]; then
    info "respaldando imagen actual en $BACKUP_DIR..."
    mkdir -p "$BACKUP_DIR"
    cp "$SYSIMG_DST" "$BACKUP_DIR/system.img"
    info "backup OK"
  else
    warn "no hay $SYSIMG_DST previo (instalación limpia); no se hace backup"
  fi
fi

# 2. Copia de la imagen parchada
info "copiando system.img parchado a $SYSIMG_DST..."
mkdir -p "$(dirname "$SYSIMG_DST")"
cp "$SYSIMG_SRC" "$SYSIMG_DST"

# 3. Estado
mkdir -p "$WORK"
printf 'mode=image\ndate=%s\nprop=%s\nsysimg_sha256=%s\n' \
  "$(date -Iseconds)" "$PROP_VALUE" \
  "$(sha256sum "$SYSIMG_SRC" | awk '{print $1}')" > "$STATE_FILE"

# 4. Arranque + verificación
if ! start_waydroid; then
  warn "no pude arrancar waydroid. Arrancalo a mano y verificá:"
  warn "  sudo systemctl start waydroid-container"
  warn "  waydroid session start"
  exit 1
fi

if ! wait_boot; then
  warn "boot no completó en 240s. Diagnóstico:"
  warn "  waydroid shell logcat -d | grep -iE 'apexd|avb|FakeWifi' | tail -50"
  warn "  dmesg | grep -i avb"
  exit 1
fi

# 5. Activación de la prop (vive en /data; sobrevive reboot, NO factory-reset)
set_prop "$PROP_VALUE" || true

# 6. Verificación runtime (informativa, no aborta)
SSID=$(waydroid shell dumpsys wifi 2>/dev/null | grep -iE 'SSID' | head -1 || true)
info "verificación dumpsys wifi: ${SSID:-(vacío, correr de nuevo)}"

echo
echo "============================================================"
echo "  INSTALACIÓN COMPLETADA (modo imagen, prop=$PROP_VALUE)"
echo "============================================================"
echo "  Verificar hook activo:"
echo "    waydroid shell dumpsys wifi | grep -i ssid"
echo "      → debe aparecer 'SSID: FakeWifi'"
echo "    waydroid shell logcat -d | grep -i FakeWifi"
echo "    waydroid shell getprop persist.waydroid.fake_wifi"
echo ""
echo "  Rollback:  sudo bash $0 --restore"
echo "  Estado:    $STATE_FILE"
echo "  Backup:    $BACKUP_DIR/system.img"
echo "============================================================"
