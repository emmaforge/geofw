#!/usr/bin/env bash
set -Eeuo pipefail

REPO_RAW=https://raw.githubusercontent.com/emmaforge/geofw/main
INSTALL_BIN=/usr/local/bin/geofw

die() { printf '安装失败：%s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || die '仅支持 Linux。'
if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null 2>&1 || die '请切换到 root，或安装 sudo。'
  exec sudo -- bash "${BASH_SOURCE[0]}" "$@"
fi

if ! command -v python3 >/dev/null 2>&1 || ! command -v nft >/dev/null 2>&1; then
  printf '正在安装 Python 3 和 nftables...\n'
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 nftables
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 nftables
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 nftables
  elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install python3 nftables
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm python nftables
  elif command -v apk >/dev/null 2>&1; then
    apk add python3 nftables
  else
    die '不支持的包管理器；请先安装 Python 3 与 nftables。'
  fi
fi

source_file=''
if [[ -f ${BASH_SOURCE[0]} ]]; then
  source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  [[ -f $source_dir/geofw ]] && source_file="$source_dir/geofw"
fi
temp_file=$(mktemp)
trap 'rm -f "$temp_file"' EXIT
if [[ -n $source_file ]]; then
  cp -- "$source_file" "$temp_file"
elif command -v curl >/dev/null 2>&1; then
  curl -fsSL "$REPO_RAW/geofw" -o "$temp_file"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$temp_file" "$REPO_RAW/geofw"
else
  die '需要 curl 或 wget 下载 geofw 主程序。'
fi
python3 -m py_compile "$temp_file"
install -D -m 755 "$temp_file" "$INSTALL_BIN"
install -d -m 700 /etc/geofw /var/lib/geofw
if [[ ! -f /etc/geofw/rules.json ]]; then
  printf '[]\n' > /etc/geofw/rules.json
  chmod 600 /etc/geofw/rules.json
fi

# Initial country data is required for safe geographic matching.
"$INSTALL_BIN" geo-update

if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
  cat > /etc/systemd/system/geofw.service <<'EOF'
[Unit]
Description=Verify and restore geofw firewall rules
Wants=network-online.target
After=network-online.target nftables.service firewalld.service ufw.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/geofw apply

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/geofw.timer <<'EOF'
[Unit]
Description=Recheck geofw firewall rules

[Timer]
OnBootSec=1min
OnUnitInactiveSec=10min
Unit=geofw.service

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable geofw.service geofw.timer
  systemctl start geofw.service geofw.timer
  printf '已启用 systemd 开机恢复及每 10 分钟规则核对。\n'
elif command -v rc-update >/dev/null 2>&1 && command -v rc-service >/dev/null 2>&1; then
  cat > /etc/init.d/geofw <<'EOF'
#!/sbin/openrc-run
description="Verify and restore geofw firewall rules"
command="/usr/local/bin/geofw"
command_args="apply"
depend() {
    need net
    after nftables firewall
}
EOF
  chmod 755 /etc/init.d/geofw
  rc-update add geofw default
  rc-service geofw start
  printf '已启用 OpenRC 开机规则核对。\n'
else
  die '找不到 systemd 或 OpenRC；规则已安装，但无法配置开机自启。'
fi

printf '安装完成。以后输入 geofw 即可打开防火墙面板。\n'
if [[ ${1:-} != --no-menu && -t 0 && -t 1 ]]; then
  "$INSTALL_BIN"
fi
