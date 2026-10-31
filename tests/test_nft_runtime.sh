#!/bin/sh
set -eu

# No base hooks: this checks nft JSON and atomic replacement without affecting traffic.
table="geofw_test_$$"
trap 'nft delete table inet "$table" >/dev/null 2>&1 || true' EXIT

nft -f - <<EOF
table inet $table {
  set cn4 { type ipv4_addr; flags interval; elements = { 1.0.1.0/24 } }
  set cn6 { type ipv6_addr; flags interval; elements = { 2001:db8::/32 } }
  chain input { ip saddr @cn4 counter accept comment "geofw:test:input"; }
  chain output { ip6 daddr != @cn6 counter drop comment "geofw:test:output"; }
}
EOF

nft -j list table inet "$table" | python3 -c '
import json, sys
entries = json.load(sys.stdin)["nftables"]
sets = {item["set"]["name"]: item["set"].get("elem") for item in entries if "set" in item}
comments = [(item["rule"]["chain"], item["rule"].get("comment")) for item in entries if "rule" in item]
assert sets.get("cn4") and sets.get("cn6"), sets
assert comments == [("input", "geofw:test:input"), ("output", "geofw:test:output")], comments
'

nft -c -f - <<EOF
delete table inet $table
table inet $table {
  chain input { counter comment "geofw:test:replacement"; }
}
EOF

nft -f - <<EOF
delete table inet $table
table inet $table {
  chain input { counter comment "geofw:test:replacement"; }
}
EOF

nft -j list table inet "$table" | python3 -c '
import json, sys
entries = json.load(sys.stdin)["nftables"]
comments = [item["rule"].get("comment") for item in entries if "rule" in item]
assert comments == ["geofw:test:replacement"], comments
'

printf 'PASS: nft JSON set/rule inspection and atomic table replacement\n'
