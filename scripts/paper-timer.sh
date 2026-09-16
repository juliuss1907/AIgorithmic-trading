#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
alert_dir="${XDG_CONFIG_HOME:-${HOME}/.config}/system-trading"
alert_file="${alert_dir}/paper-alerts.env"
service_name="system-trading-paper.service"
timer_name="system-trading-paper.timer"

case "${1:-status}" in
  install)
    uv_path="$(command -v uv)"
    mkdir -p "${unit_dir}"
    install -d -m 0700 "${alert_dir}"
    sed -e "s|@PROJECT_ROOT@|${project_root}|g" -e "s|@UV_PATH@|${uv_path}|g" \
      "${project_root}/deploy/systemd/system-trading-paper.service.in" \
      > "${unit_dir}/${service_name}"
    install -m 0644 "${project_root}/deploy/systemd/system-trading-paper.timer" \
      "${unit_dir}/${timer_name}"
    systemctl --user daemon-reload
    systemctl --user enable --now "${timer_name}"
    systemctl --user list-timers "${timer_name}" --no-pager
    ;;
  alert-test)
    if [[ ! -f "${alert_file}" ]]; then
      echo "Missing Telegram config: ${alert_file}" >&2
      exit 1
    fi
    if [[ "$(stat -c '%a' "${alert_file}")" != "600" ]]; then
      echo "Telegram config must have mode 600: ${alert_file}" >&2
      exit 1
    fi
    exec uv run --frozen python -m lab paper-alert-test --env-file "${alert_file}"
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
    echo "Usage: $0 {install|status|alert-test|uninstall}" >&2
    exit 2
    ;;
esac
