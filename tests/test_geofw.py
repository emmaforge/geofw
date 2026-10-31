import importlib.machinery
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "geofw"
loader = importlib.machinery.SourceFileLoader("geofw", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
geofw = importlib.util.module_from_spec(spec)
loader.exec_module(geofw)


class GeoFirewallTests(unittest.TestCase):
    def setUp(self):
        self.cn4 = [f"1.0.{i * 2}.0/24" for i in range(120)]
        self.cn6 = [f"240e:0:{i * 2:x}::/48" for i in range(120)]

    def rule(self, **changes):
        result = {"id": 1, "family": "ipv4", "direction": "in", "protocol": "tcp",
                  "ports": "80", "geo": "cn", "action": "allow"}
        result.update(changes)
        return result

    def test_port_normalization_and_bounds(self):
        self.assertEqual(geofw.port_text(geofw.parse_ports("443,80,81,2000-2002")),
                         "80-81,443,2000-2002")
        for bad in ("", "0", "65536", "100-90", "80;443", "80,,443"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                geofw.parse_ports(bad)

    def test_overlapping_family_direction_protocol_rejected(self):
        first = self.rule(family="dual", direction="both", protocol="both", ports="80,443")
        second = self.rule(id=2, family="ipv6", direction="out", protocol="udp", ports="443")
        with self.assertRaisesRegex(ValueError, "重叠"):
            geofw.validate_rules([first, second])
        second["ports"] = "444"
        self.assertEqual(len(geofw.validate_rules([first, second])), 2)

    def test_apnic_parser_requires_both_families(self):
        rows = [f"apnic|CN|ipv4|1.0.{i * 2}.0|256|20261030|allocated"
                for i in range(120)]
        rows += [f"apnic|CN|ipv6|240e:0:{i * 2:x}::|48|20261030|allocated"
                 for i in range(120)]
        rows.append("apnic|US|ipv4|8.8.8.0|256|20261030|allocated")
        cn4, cn6 = geofw.parse_apnic("\n".join(rows))
        self.assertEqual(len(cn4), 120)
        self.assertEqual(len(cn6), 120)
        self.assertNotIn("8.8.8.0/24", cn4)
        with self.assertRaisesRegex(ValueError, "数据不足"):
            geofw.parse_apnic("\n".join(rows[:120]))

    def test_allow_cn_blocks_other_new_ipv4_connections(self):
        table, comments = geofw.render_table([self.rule()], self.cn4, self.cn6)
        self.assertIn("tcp dport 80 ip saddr @cn4 counter accept", table)
        self.assertIn("tcp dport 80 ip saddr != @cn4 ip saddr != @local4 counter drop", table)
        self.assertIn("ct state established,related accept", table)
        self.assertEqual(comments[0][0], "input")
        self.assertEqual(comments[-1][0], "output")

    def test_block_foreign_outbound_ipv6(self):
        rule = self.rule(family="ipv6", direction="out", protocol="udp", ports="443",
                         geo="foreign", action="block")
        table, _ = geofw.render_table([rule], self.cn4, self.cn6)
        self.assertIn("udp dport 443 ip6 daddr != @cn6 ip6 daddr != @local6 counter drop", table)
        self.assertNotIn("udp dport 443 ip6 daddr != @cn6 ip6 daddr != @local6 counter accept", table)

    def test_snapshot_detects_missing_rules_and_sets(self):
        _, comments = geofw.render_table([self.rule()], self.cn4, self.cn6)
        entries = [{"set": {"name": "cn4", "elem": ["1.0.0.0/24"]}},
                   {"set": {"name": "cn6", "elem": ["240e::/48"]}},
                   {"set": {"name": "local4", "elem": ["10.0.0.0/8"]}},
                   {"set": {"name": "local6", "elem": ["fc00::/7"]}}]
        entries += [{"rule": {"chain": chain, "comment": comment}}
                    for chain, comment in comments]
        self.assertTrue(geofw.nft_current({"nftables": entries}, comments))
        self.assertFalse(geofw.nft_current({"nftables": entries[:-1]}, comments))
        self.assertFalse(geofw.nft_current({"nftables": entries[1:]}, comments))

    def test_apply_does_not_recreate_current_table(self):
        _, comments = geofw.render_table([self.rule()], self.cn4, self.cn6)
        entries = [{"set": {"name": "cn4", "elem": ["1.0.0.0/24"]}},
                   {"set": {"name": "cn6", "elem": ["240e::/48"]}},
                   {"set": {"name": "local4", "elem": ["10.0.0.0/8"]}},
                   {"set": {"name": "local6", "elem": ["fc00::/7"]}}]
        entries += [{"rule": {"chain": chain, "comment": comment}}
                    for chain, comment in comments]
        with mock.patch.object(geofw.shutil, "which", return_value="/usr/sbin/nft"), \
             mock.patch.object(geofw, "nft_snapshot", return_value={"nftables": entries}), \
             mock.patch.object(geofw.subprocess, "run") as run:
            self.assertFalse(geofw.apply_rules([self.rule()], self.cn4, self.cn6, quiet=True))
            run.assert_not_called()

    def test_menu_and_rule_cards_are_one_item_per_line(self):
        output = io.StringIO()
        with mock.patch("builtins.input", side_effect=["0"]), redirect_stdout(output):
            geofw.menu()
        lines = output.getvalue().splitlines()
        for item in ("  [1] 添加防火墙规则", "  [2] 查看当前规则",
                     "  [3] 删除指定规则", "  [4] 核对并修复规则",
                     "  [5] 更新国内网段", "  [0] 退出面板"):
            self.assertIn(item, lines)
        card = geofw.format_rule(self.rule())
        self.assertIn("  [#1] IPv4 / 入站 / TCP", card)
        self.assertIn("执行动作：放行（其他地域阻断）", card)

    def test_geo_cache_rejects_corruption(self):
        checksum = hashlib.sha256(("\n".join(self.cn4) + "\n--\n" +
                                   "\n".join(self.cn6)).encode()).hexdigest()
        data = {"meta": {"sha256": checksum}, "cn4": self.cn4, "cn6": self.cn6}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cn.json"
            with mock.patch.object(geofw, "GEO_PATH", path):
                path.write_text(json.dumps(data), encoding="utf-8")
                self.assertEqual(geofw.read_geo()[0], self.cn4)
                data["cn4"][0] = "0.0.0.0/0"
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    geofw.read_geo()


if __name__ == "__main__":
    unittest.main()
