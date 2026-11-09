#!/usr/bin/env bash
# OmaPoker uninstaller.
#
#   ./scripts/uninstall.sh            remove the app, keep your profiles
#   ./scripts/uninstall.sh --purge    also delete profiles, bankrolls and keys
#   ./scripts/uninstall.sh --yes      do not ask first
#
# Your bankroll is the thing you cannot get back, so --purge asks for the word
# "purge" rather than a bare "yes".
set -euo pipefail

APP="omapoker"
SHARE_DIR="${OMAPOKER_SHARE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/$APP}"
CONFIG_DIR="${OMAPOKER_CONFIG_DIR:-$HOME/.config/omarchy/$APP}"
STATE_DIR="${OMAPOKER_STATE_DIR:-$HOME/.local/state/$APP}"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

PURGE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

green() { printf '\033[38;2;158;206;106m%s\033[0m\n' "$*"; }
dim()   { printf '\033[38;2;86;95;137m%s\033[0m\n' "$*"; }
warn()  { printf '\033[38;2;247;118;142m%s\033[0m\n' "$*" >&2; }

if [ "$PURGE" = "1" ]; then
  warn "This deletes your profiles, identity keys, bankrolls and stats:"
  warn "  $CONFIG_DIR"
  warn "  $STATE_DIR"
  if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "Type 'purge' to confirm: " answer
    [ "$answer" = "purge" ] || { echo "left alone."; exit 1; }
  fi
fi

rm -f "$BIN_DIR/omapoker"
rm -f "$APPS_DIR/omapoker.desktop"
rm -f "$ICON_DIR/omapoker.svg"
rm -rf "$SHARE_DIR"
dim "removed launcher, desktop entry, icon and $SHARE_DIR"

command -v update-desktop-database >/dev/null 2>&1 &&
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

if [ "$PURGE" = "1" ]; then
  rm -rf "$CONFIG_DIR" "$STATE_DIR"
  dim "removed $CONFIG_DIR"
  dim "removed $STATE_DIR"
else
  dim "kept $CONFIG_DIR (your bankroll survives; --purge deletes it)"
fi

green "OmaPoker uninstalled."
