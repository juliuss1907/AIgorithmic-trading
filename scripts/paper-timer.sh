#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
service_name="system-trading-paper.service"
timer_name="system-trading-paper.timer"

case "${1:-status}" in
  install)
    uv_path="$(command -v uv)"
    mkdir -p "${unit_dir}"
    sed -e "s|@PROJECT_ROOT@|${project_root}|g" -e "s|@UV_PATH@|${uv_path}|g" \
      "${project_root}/deploy/systemd/system-trading-paper.service.in" \
      > "${unit_dir}/${service_name}"
    install -m 0644 "${project_root}/deploy/systemd/system-trading-paper.timer" \
      "${unit_dir}/${timer_name}"
    systemctl --user daemon-reload
    systemctl --user enable --now "${timer_name}"
    systemctl --user list-timers "${timer_name}" --no-pager
    ;;
  status)
    systemctl --user status "${timer_name}" --no-pager
    systemctl --user list-timers "${timer_name}" --no-pager
    ;;
  uninstall)
    systemctl --user disable --now "${timer_name}" || true
    rm -f "${unit_dir}/${timer_name}" "${unit_dir}/${service_name}"
    systemctl --user daemon-reload
    ;;
  *)
    echo "Usage: $0 {install|status|uninstall}" >&2
    exit 2
    ;;
esac
