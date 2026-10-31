#!/usr/bin/env python3
"""Refresh the repository's dated APNIC CN snapshot on GitHub Actions."""

import hashlib
import importlib.machinery
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
GEOFW_PATH = ROOT / "geofw"
SNAPSHOT_PATH = ROOT / "data" / "apnic-cn.txt"
geofw = importlib.machinery.SourceFileLoader("geofw", str(GEOFW_PATH)).load_module()


def update_from_text(raw, program_path=GEOFW_PATH, snapshot_path=SNAPSHOT_PATH):
    geofw.parse_full_apnic(raw)
    header = next(line.split("|") for line in raw.splitlines() if line.startswith("2|apnic|"))
    source_date = header[2]
    if not re.fullmatch(r"\d{8}", source_date):
        raise ValueError("APNIC 数据日期无效")
    if source_date != geofw.today_cn_date():
        print(f"APNIC 数据日期为 {source_date}，今天是 {geofw.today_cn_date()}；等待下一次检查。")
        return False

    program = program_path.read_text(encoding="utf-8")
    if re.search(r'APNIC_MIRROR_DATE = "(\d{8})"', program).group(1) == source_date:
        print(f"仓库已保存 {source_date} 的网段快照。")
        return False

    rows = []
    for line in raw.splitlines():
        fields = line.split("|")
        if (len(fields) >= 7 and fields[0:2] == ["apnic", "CN"] and
                fields[2] in ("ipv4", "ipv6") and
                fields[6] in ("allocated", "assigned", "legacy")):
            rows.append(line)
    snapshot = (f"# APNIC delegated-apnic-latest CN IPv4/IPv6 snapshot {source_date}\n" +
                "\n".join(rows) + "\n")
    geofw.parse_apnic(snapshot)
    digest = hashlib.sha256(snapshot.encode("ascii")).hexdigest()
    program, count_date = re.subn(r'APNIC_MIRROR_DATE = "\d{8}"',
                                  f'APNIC_MIRROR_DATE = "{source_date}"', program)
    program, count_hash = re.subn(r'APNIC_MIRROR_SHA256 = "[0-9a-f]{64}"',
                                  f'APNIC_MIRROR_SHA256 = "{digest}"', program)
    if count_date != 1 or count_hash != 1:
        raise ValueError("程序内镜像日期或校验值位置异常")
    snapshot_path.write_text(snapshot, encoding="ascii", newline="\n")
    program_path.write_text(program, encoding="utf-8", newline="\n")
    print(f"已更新 {source_date} 的 APNIC CN 快照：{len(rows)} 条，SHA-256 {digest}")
    return True


def main():
    payload = geofw.download_data(geofw.APNIC_URL, 90, 20_000_000)
    update_from_text(payload.decode("ascii"))


if __name__ == "__main__":
    main()
