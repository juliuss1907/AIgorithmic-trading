#!/usr/bin/env bash
set -euo pipefail

# Install from the checkout the timer should run; the unit pins WorkingDirectory to it.
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
service_name="setup2-prospective.service"
timer_name="setup2-prospective.timer"

case "${1:-status}" in
  install)
    uv_path="$(command -v uv)"
    mkdir -p "${unit_dir}"
    sed -e "s|@PROJECT_ROOT@|${project_root}|g" -e "s|@UV_PATH@|${uv_path}|g" \
      "${project_root}/deploy/systemd/setup2-prospective.service.in" \
      > "${unit_dir}/${service_name}"
    install -m 0644 "${project_root}/deploy/systemd/setup2-prospective.timer" \
      "${unit_dir}/${timer_name}"
    systemctl --user daemon-reload
    systemctl --user enable --now "${timer_name}"
    systemctl --user list-timers "${timer_name}" --no-pager
    ;;
  status)
    systemctl --user status "${timer_name}" --no-pager || true
    systemctl --user list-timers "${timer_name}" --no-pager
    journalctl --user -u "${service_name}" -n 20 --no-pager
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
