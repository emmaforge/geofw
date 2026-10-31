# geofw · 双栈地域防火墙面板

独立的 Linux 防火墙管理项目，**不修改 hopctl 的代码**。输入 `geofw` 打开中文交互面板，可按 IPv4 / IPv6 / 双栈或指定域名/IP、入站 / 出站 / 双向、TCP / UDP / 两者、单端口 / 多端口 / 端口范围，以及国内 / 国外 / 全部地址创建放行或阻断规则。支持按编号删除、重启恢复和重复运行核对。主要适配 Debian，也支持使用 systemd 的 Ubuntu、Fedora、RHEL 系、Arch、openSUSE，以及使用 OpenRC 的 Alpine。

## 一键安装

在服务器终端选择一种方式执行；非 root 用户会自动请求 sudo 权限。

使用 `curl`：

```bash
curl -fsSLo /tmp/install-geofw.sh https://raw.githubusercontent.com/zzx327317-creator/geofw/main/install.sh && bash /tmp/install-geofw.sh
```

使用 `wget`：

```bash
wget -qO /tmp/install-geofw.sh https://raw.githubusercontent.com/zzx327317-creator/geofw/main/install.sh && bash /tmp/install-geofw.sh
```

安装脚本会安装缺失的 Python 3 / nftables，下载程序和 APNIC 中国网段，建立开机服务，随后打开面板。也可下载仓库后在目录内运行 `bash install.sh`。安装失败时不会启用开机服务。

网段优先从 APNIC 官方源站获取，最多等待 **30 秒**；源站超时或失败时，自动通过 `gh-proxy.com`、`gh-proxy.org` 读取本仓库带日期标签的 **APNIC CN 网段快照**。镜像文件使用程序内的 SHA-256 校验，不接受被修改的内容。仓库的 GitHub Actions **每小时检查一次** APNIC，发现当天数据后更新快照、校验值和日期标签；它不是逐秒实时的，任务延迟或 APNIC 当天数据尚未发布时，仓库可能仍是上一日期。重新运行安装脚本会取得最新程序及其快照日期。

若源站失败且程序只能下载到旧日期的已校验快照，交互运行时会显示日期并询问是否回退；输入 `yes` 才使用。非交互运行默认拒绝旧快照，可通过 `geofw geo-update --allow-stale-mirror`，或安装时设置 `GEOFW_ALLOW_STALE_MIRROR=1`，明确允许回退。镜像不可用时会报错，不会用空网段覆盖规则。

以后输入 `geofw` 即可打开面板；`geofw list` 查看配置，`geofw apply` 手动核对并修复，`geofw geo-update` 手动更新国内网段。systemd 系统每 10 分钟核对一次当前规则；**不会自动更新地域网段或自动删除规则**。OpenRC 系统在开机时核对，防火墙服务重载后可手动运行 `geofw apply`。

## 规则含义

- **入站**：匹配访问服务器的来源 IP 与目标端口；**出站**：匹配服务器访问的目标 IP 与目标端口。双向分别在两个方向生成规则。
- **放行国内**：允许 APNIC 中国网段访问所选端口，同时阻断其他公网来源的新连接；**放行国外**则相反。`放行全部` 对所有地址放行。
- **阻断国内**：阻断 APNIC 中国网段，其余地址交给现有防火墙处理；**阻断国外**则阻断非中国公网地址。`阻断全部` 阻断所有地址。
- 常见内网、环回、链路本地和保留地址不参与国内/国外匹配，继续交给已有防火墙；选择“全部地址”时不做这项排除。
- 规则只作用于指定的地址族、方向、协议和目标端口，不改变系统默认策略。已经建立的连接不被新规则中断。对于相同地址族、方向、协议和重叠端口，面板拒绝创建互相遮蔽的规则。
- `放行` 仅表示 geofw 自己的规则允许通过；若云安全组或其他防火墙仍在阻断，该连接仍可能失败。

### 指定域名或 IP

添加规则时，在第一个“地址族”菜单选 `[4] 指定域名/IP`，再填写完整域名、单个 IPv4/IPv6 地址或 CIDR 网段。IP/CIDR 自动识别地址族；域名使用当前 DNS 解析出的 A/AAAA 地址。此时无需再选国内/国外。

- 入站按**连接来源地址**匹配，出站按**连接目的地址**匹配；两者都可与目标端口、TCP/UDP 组合。
- `阻断`只拦截该地址或域名当前解析出的地址。`放行`允许这些地址，同时阻断该端口的其他公网新连接；内网及保留地址仍交给已有防火墙。若域名只有 A 记录，选择放行时该端口的其他公网 IPv6 来源/目的地址也会被阻断。
- 域名在每次 `geofw apply` 时重新解析。systemd 系统每 10 分钟自动核对并更新变动的解析结果；OpenRC 系统在开机时核对，也可手动运行 `geofw apply`。DNS 解析失败时保留已有规则，不用空结果覆盖。
- 匹配的是 DNS 解析后的 **IP 地址**，不是网页域名、HTTP Host 或 TLS SNI。CDN 共享 IP、DNS 轮询和解析位置差异可能影响匹配范围。

例如只允许国内 IPv4 访问服务器 TCP 80：在面板依次选择 `IPv4 → 入站 → TCP → 80 → 仅国内 → 放行`。非国内来源的新连接会被 geofw 阻断。多端口输入示例：`80,443,2000-2999`。

命令行也可添加或清理：

```bash
geofw add --family ipv4 --direction in --protocol tcp --ports 80 --geo cn --action allow
geofw add --family dual --direction out --protocol both --ports 80,443,2000-2999 --geo foreign --action block
geofw add --family ipv4 --direction in --protocol tcp --ports 22 --geo target --target 203.0.113.7 --action allow
geofw add --family dual --direction out --protocol tcp --ports 443 --geo target --target example.com --action block
geofw list
geofw delete 2
geofw apply
geofw geo-update
```

## 持久化与网段来源

规则保存在 `/etc/geofw/rules.json`，国内 IPv4 / IPv6 网段和来源时间保存在 `/var/lib/geofw/cn.json`，均仅允许 root 访问。程序只维护独立的 `inet geofw_managed` nftables 表；每次应用先检查版本标记和规则，正确时不会重复添加，有变化才原子替换本项目的表。删除某条规则时按列表编号选择，不清空其他防火墙规则。

这里的“国内”明确指**中国大陆**：APNIC 官方 `delegated-apnic-latest` 中标记为 `CN` 的 IPv4 / IPv6 分配记录。港澳台在地址数据中使用独立代码，当前不计入 `CN`。“国外”指**不在这份 CN 分配列表中的公网地址**，不等于实时物理位置；代理、CDN、跨境分配和更新滞后可能造成差异。安装时会获取一次，之后由管理员用 `geofw geo-update` 手动刷新。源站不可用时使用仓库中的已校验快照，无法取得有效的 IPv4 与 IPv6 数据时仍拒绝应用地域规则。

## 使用前核对

1. 首次操作建议先从非 SSH 端口测试。若限制当前 SSH 端口，可能阻断新的远程登录；添加前面板会提示确认。保留当前会话并准备云厂商控制台作为回退方式。
2. 与 `hopctl` 同用时，入站端口跳跃会先在 NAT 阶段转换目标端口。因此 geofw 的**入站目标端口应填写转发后的服务端口**，例如公网 `2000-2999 → 6502` 时填写 `6502`。
3. `geofw` 不会自动开放云厂商安全组，也不会修改 UFW / firewalld 的既有设置。不同防火墙的允许规则不能互相覆盖阻断规则。
4. 建议在较空闲的时段更新 APNIC 网段；更新会改变地域分类，但不会删除你创建的规则。运行 `geofw list` 可查看最近一次数据更新时间。

## 排查

```bash
geofw list
nft list table inet geofw_managed
systemctl status geofw.service geofw.timer --no-pager
journalctl -u geofw.service -n 50 --no-pager
```

OpenRC 系统将后两条换成 `rc-service geofw status`。若规则显示“缺失或有变动”，运行 `geofw apply`；若 APNIC 下载失败，稍后运行 `geofw geo-update`。项目使用 nftables，不需要 ipset，也不执行全局 `nft flush ruleset`。
