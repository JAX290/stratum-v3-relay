#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY_URL="https://github.com/JAX290/stratum-v3-relay.git"
REPOSITORY_DIR="${STRATUM_REPOSITORY_DIR:-/root/stratum-v3}"
MODE="auto"
RELAY_PORT=""
PUBLIC_DOMAIN=""
SKIP_TAILSCALE=0
SKIP_PUBLIC=0
ORIGINAL_ARGS=("$@")
CURRENT_STEP="启动"

usage() {
  cat <<EOF
木林森中转一键部署与升级

用法：
  ./deploy.sh                     自动判断首次安装或升级
  ./deploy.sh --install           强制按首次安装执行
  ./deploy.sh --upgrade           强制按升级执行

可选参数：
  --relay-port PORT               首次安装使用的 TLS 端口，默认 452
  --public-domain DOMAIN          同时配置 HTTPS 只读值守面板
  --skip-tailscale                不安装或配置 Tailscale
  --skip-public-status            不配置 HTTPS 只读值守面板
  -h, --help                      显示本说明

升级会保留线路、证书、客户端密钥、面板账号、值守账号和告警设置。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install) MODE="install"; shift ;;
    --upgrade) MODE="upgrade"; shift ;;
    --relay-port)
      [[ $# -ge 2 ]] || { echo "--relay-port 后面缺少端口。" >&2; exit 2; }
      RELAY_PORT="$2"; shift 2 ;;
    --public-domain)
      [[ $# -ge 2 ]] || { echo "--public-domain 后面缺少域名。" >&2; exit 2; }
      PUBLIC_DOMAIN="$2"; shift 2 ;;
    --skip-tailscale) SKIP_TAILSCALE=1; shift ;;
    --skip-public-status) SKIP_PUBLIC=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "无法识别的参数：$1" >&2; usage; exit 2 ;;
  esac
done

on_error() {
  local code=$?
  echo >&2
  echo "============================================================" >&2
  echo "部署没有完成，停止在：$CURRENT_STEP" >&2
  echo "错误代码：$code" >&2
  if [[ -f /root/stratum-v3-last-backup ]]; then
    echo "最近备份：$(cat /root/stratum-v3-last-backup 2>/dev/null || true)" >&2
  fi
  echo "已经完成的配置不会被脚本主动删除。修正上面的错误后，可重新运行同一条命令。" >&2
  echo "============================================================" >&2
  exit "$code"
}
trap on_error ERR

if [[ $(id -u) -ne 0 ]]; then
  echo "请使用 root 用户运行一键部署脚本。" >&2
  exit 1
fi

confirm() {
  local prompt=$1 default=${2:-yes} answer
  if [[ ! -t 0 ]]; then
    [[ "$default" == "yes" ]]
    return
  fi
  if [[ "$default" == "yes" ]]; then
    read -r -p "$prompt [Y/n] " answer || true
    [[ ! "$answer" =~ ^[Nn]$ ]]
  else
    read -r -p "$prompt [y/N] " answer || true
    [[ "$answer" =~ ^[Yy]$ ]]
  fi
}

echo "============================================================"
echo "  木林森中转一键部署与升级"
echo "============================================================"
echo "脚本会自动备份现有配置、执行安装或升级，并在结束时检查服务。"
echo

CURRENT_STEP="准备 Git 和基础下载工具"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates

if [[ ! -d "$REPOSITORY_DIR/.git" ]]; then
  if [[ -e "$REPOSITORY_DIR" ]]; then
    echo "$REPOSITORY_DIR 已存在但不是 Git 仓库，请先移动或删除该目录。" >&2
    exit 1
  fi
  CURRENT_STEP="从 GitHub 下载项目"
  git clone --branch main --single-branch "$REPOSITORY_URL" "$REPOSITORY_DIR"
  chmod +x "$REPOSITORY_DIR/deploy.sh"
  exec env STRATUM_DEPLOY_CODE_READY=1 "$REPOSITORY_DIR/deploy.sh" "${ORIGINAL_ARGS[@]}"
fi

cd "$REPOSITORY_DIR"
if [[ "${STRATUM_DEPLOY_CODE_READY:-0}" != "1" ]]; then
  CURRENT_STEP="检查本地代码是否可以安全升级"
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "仓库中存在尚未提交的代码修改，为避免覆盖，脚本已停止。" >&2
    echo "请先保存这些修改，或使用一个干净的 /root/stratum-v3 仓库。" >&2
    exit 1
  fi
  CURRENT_STEP="更新到 GitHub main 最新版本"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
  chmod +x ./deploy.sh
  exec env STRATUM_DEPLOY_CODE_READY=1 ./deploy.sh "${ORIGINAL_ARGS[@]}"
fi

if [[ "$MODE" == "auto" ]]; then
  if [[ -f /etc/stratum-v3.json || -f /etc/systemd/system/stratum-admin.service ]]; then
    MODE="upgrade"
  else
    MODE="install"
  fi
fi

if [[ "$MODE" == "install" && -f /etc/stratum-v3.json ]]; then
  echo "检测到这台 VPS 已有配置，不能强制执行首次安装。请改用 --upgrade。" >&2
  exit 2
fi
if [[ "$MODE" == "upgrade" && ! -f /etc/stratum-v3.json ]]; then
  echo "没有检测到已安装的 V3 配置，不能执行升级。请改用 --install。" >&2
  exit 2
fi

echo "检测结果：$( [[ "$MODE" == "install" ]] && echo '新 VPS，执行首次安装' || echo '已有 VPS，执行保留配置升级' )"
echo "代码版本：$(git rev-parse --short HEAD)"
echo

if [[ "$MODE" == "install" ]]; then
  if [[ -z "$RELAY_PORT" ]]; then
    if [[ -t 0 ]]; then
      read -r -p "TLS 加密入口端口 [452]：" RELAY_PORT || true
    fi
    RELAY_PORT=${RELAY_PORT:-452}
  fi
  [[ "$RELAY_PORT" =~ ^[0-9]+$ ]] && ((RELAY_PORT >= 1 && RELAY_PORT <= 65535)) || {
    echo "TLS 端口必须是 1-65535 的数字。" >&2; exit 2;
  }

  CURRENT_STEP="安装 V3 转发、监控和管理面板"
  chmod +x monitor-panel/bootstrap-vps.sh
  monitor-panel/bootstrap-vps.sh

  CURRENT_STEP="安装 TLS 加密入口"
  chmod +x secure-relay/server/install-secure-relay.sh
  secure-relay/server/install-secure-relay.sh --port "$RELAY_PORT"
else
  CURRENT_STEP="备份并升级 V3 面板和监控服务"
  chmod +x monitor-panel/upgrade-v3-panel.sh
  monitor-panel/upgrade-v3-panel.sh

  CURRENT_STEP="升级 TLS 加密入口并保留端口、证书和密钥"
  chmod +x secure-relay/server/install-secure-relay.sh
  secure-relay/server/install-secure-relay.sh
fi

if [[ $SKIP_TAILSCALE -eq 0 ]]; then
  if command -v tailscale >/dev/null 2>&1; then
    echo "Tailscale 已安装。"
  elif confirm "是否安装 Tailscale 管理通道？" yes; then
    CURRENT_STEP="安装 Tailscale"
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  if command -v tailscale >/dev/null 2>&1; then
    if ! tailscale status >/dev/null 2>&1; then
      CURRENT_STEP="登录 Tailscale"
      echo "请按照下面显示的网址登录 Tailscale。"
      tailscale up
    fi
    if tailscale status >/dev/null 2>&1; then
      CURRENT_STEP="发布 Tailscale 管理面板"
      tailscale serve --bg http://127.0.0.1:8789
    fi
  fi
fi

if [[ $SKIP_PUBLIC -eq 0 ]]; then
  if [[ -n "$PUBLIC_DOMAIN" ]]; then
    CURRENT_STEP="配置 HTTPS 只读值守面板"
    chmod +x monitor-panel/install-public-status.sh
    public_args=(--domain "$PUBLIC_DOMAIN")
    [[ "$MODE" == "upgrade" ]] && public_args+=(--skip-dns-check)
    [[ ! -f /etc/stratum-public-status.env ]] && public_args+=(--reset-login)
    monitor-panel/install-public-status.sh "${public_args[@]}"
  elif [[ "$MODE" == "install" ]]; then
    if confirm "是否已经准备好 status 域名，并立即配置 HTTPS 只读值守面板？" no; then
      CURRENT_STEP="配置 HTTPS 只读值守面板"
      chmod +x monitor-panel/install-public-status.sh
      monitor-panel/install-public-status.sh
    fi
  elif [[ ! -f /etc/stratum-public-status.env ]]; then
    echo
    echo "旧版公网面板尚未设置独立值守账号。"
    if confirm "是否现在设置账号并升级公网只读值守面板？" yes; then
      CURRENT_STEP="识别现有公网面板域名"
      detected_domain=$(awk '$1 == "server_name" {gsub(/;/, "", $2); print $2; exit}' /etc/nginx/sites-available/stratum-public-status 2>/dev/null || true)
      if [[ -n "$detected_domain" ]]; then
        CURRENT_STEP="设置公网值守账号并更新 HTTPS 面板"
        monitor-panel/install-public-status.sh --domain "$detected_domain" --skip-dns-check --reset-login
      else
        monitor-panel/install-public-status.sh
      fi
    fi
  fi
fi

CURRENT_STEP="执行安装后验收"
required_services=(haproxy stratum-inspector-v3 stratum-endpoint-monitor stratum-route-switch-monitor
  stratum-security-monitor stratum-admin stratum-public-status stratum-secure-relay stratum-secure-monitor)
if [[ -f /etc/nginx/sites-available/stratum-public-status ]]; then
  required_services+=(nginx)
fi
failed=()
echo
echo "核心服务检查："
for service in "${required_services[@]}"; do
  state=$(systemctl is-active "$service" 2>/dev/null || true)
  if [[ "$state" == "active" ]]; then
    printf '  [正常] %s\n' "$service"
  else
    printf '  [异常] %s（%s）\n' "$service" "${state:-未找到}"
    failed+=("$service")
  fi
done
timer_state=$(systemctl is-active stratum-vps-watchdog.timer 2>/dev/null || true)
printf '  [%s] %s\n' "$( [[ "$timer_state" == "active" ]] && echo '正常' || echo '异常' )" "stratum-vps-watchdog.timer"
[[ "$timer_state" == "active" ]] || failed+=("stratum-vps-watchdog.timer")

if (( ${#failed[@]} > 0 )); then
  echo >&2
  echo "以下服务未通过验收：${failed[*]}" >&2
  echo "请运行：systemctl status ${failed[*]} --no-pager" >&2
  exit 1
fi

trap - ERR
echo
echo "============================================================"
echo "  $( [[ "$MODE" == "install" ]] && echo '部署完成' || echo '升级完成' )"
echo "============================================================"
echo "所有核心服务均已通过检查。"
if command -v tailscale >/dev/null 2>&1; then
  tailscale serve status 2>/dev/null || true
fi
if [[ -f /etc/nginx/sites-available/stratum-public-status ]]; then
  domain=$(awk '$1 == "server_name" {gsub(/;/, "", $2); print $2; exit}' /etc/nginx/sites-available/stratum-public-status 2>/dev/null || true)
  [[ -n "$domain" ]] && echo "公网只读值守面板：https://$domain/"
fi
echo "配置、证书和密钥已保留在 VPS；请按 README 的部署验收清单完成真实矿机测试。"
