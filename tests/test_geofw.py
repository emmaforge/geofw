import importlib.machinery
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import socket
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

    def test_target_ip_validation_and_rendering(self):
        rule = self.rule(geo="target", target="203.0.113.7")
        table, _ = geofw.render_table([rule], self.cn4, self.cn6)
        self.assertIn("set target_1_4", table)
        self.assertIn("203.0.113.7", table)
        self.assertIn("tcp dport 80 ip saddr @target_1_4 counter accept", table)
        self.assertIn("ip saddr != @target_1_4 ip saddr != @local4 counter drop", table)
        for invalid in (self.rule(geo="target"),
                        self.rule(geo="target", target="example.com/24"),
                        self.rule(family="dual", geo="target", target="203.0.113.7"),
                        self.rule(target="example.com")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                geofw.normalize_rule(invalid)

    def test_domain_addresses_change_rule_revision(self):
        rule = self.rule(family="dual", direction="out", geo="target",
                         target="Example.COM.", action="block")
        first_records = [(socket.AF_INET, None, None, None, ("203.0.113.7", 0)),
                         (socket.AF_INET6, None, None, None, ("2001:db8::7", 0, 0, 0))]
        second_records = [(socket.AF_INET, None, None, None, ("203.0.113.8", 0))]
        with mock.patch.object(geofw.socket, "getaddrinfo", return_value=first_records):
            first, first_comments = geofw.render_table([rule], self.cn4, self.cn6)
        with mock.patch.object(geofw.socket, "getaddrinfo", return_value=second_records):
            second, second_comments = geofw.render_table([rule], self.cn4, self.cn6)
        self.assertIn("ip daddr @target_1_4 counter drop", first)
        self.assertIn("ip6 daddr @target_1_6 counter drop", first)
        self.assertNotIn("set target_1_6", second)
        self.assertNotEqual(first_comments[0], second_comments[0])

    def test_allow_domain_without_aaaa_blocks_other_public_ipv6(self):
        rule = self.rule(family="dual", geo="target", target="example.com")
        records = [(socket.AF_INET, None, None, None, ("203.0.113.7", 0))]
        with mock.patch.object(geofw.socket, "getaddrinfo", return_value=records):
            table, _ = geofw.render_table([rule], self.cn4, self.cn6)
        self.assertIn("tcp dport 80 ip6 saddr != @local6 counter drop", table)
        self.assertNotIn("set target_1_6", table)

    def test_domain_resolution_error_keeps_existing_table(self):
        rule = self.rule(geo="target", target="example.com")
        with mock.patch.object(geofw.socket, "getaddrinfo", side_effect=socket.gaierror("DNS down")), \
             mock.patch.object(geofw.shutil, "which", return_value="/usr/sbin/nft"), \
             mock.patch.object(geofw.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "保留当前防火墙规则"):
                geofw.apply_rules([rule], self.cn4, self.cn6)
            run.assert_not_called()

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

    def test_specific_address_option_is_in_family_menu(self):
        output = io.StringIO()
        choices = ["1", "4", "203.0.113.7", "1", "1", "80", "2", "no", "0"]
        with mock.patch("builtins.input", side_effect=choices), redirect_stdout(output):
            geofw.menu()
        self.assertIn("[4] 指定域名/IP", output.getvalue())
        self.assertIn("指定地址：203.0.113.7", output.getvalue())

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
