"""Tests for quadletman/services/agent.py — /proc/net/tcp parsing and connection collection."""

import textwrap

from quadletman.services.agent import _get_connections
from quadletman.services.metrics import parse_hex_addr, parse_proc_net_tcp


class TestParseHexAddr:
    def test_localhost_port_80(self):
        ip, port = parse_hex_addr("0100007F:0050")
        assert ip == "127.0.0.1"
        assert port == 80

    def test_all_zeros(self):
        ip, port = parse_hex_addr("00000000:0000")
        assert ip == "0.0.0.0"
        assert port == 0

    def test_real_ip(self):
        # 192.168.1.100 = C0.A8.01.64 → little-endian: 6401A8C0
        ip, port = parse_hex_addr("6401A8C0:01BB")
        assert ip == "192.168.1.100"
        assert port == 443

    def test_high_port(self):
        ip, port = parse_hex_addr("0100007F:FFFF")
        assert ip == "127.0.0.1"
        assert port == 65535

    def test_class_a_address(self):
        # 10.0.0.1 = 0A.00.00.01 → little-endian: 0100000A
        ip, port = parse_hex_addr("0100000A:1F90")
        assert ip == "10.0.0.1"
        assert port == 8080


class TestParseProcNetTcp:
    def test_parses_established_connections(self, tmp_path):
        content = textwrap.dedent("""\
            sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
             0: 0100007F:0050 6401A8C0:D431 01 00000000:00000000 00:00000000 00000000  1000        0 12345 1 00000000 100 0 0 10 0
             1: 0100007F:0051 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 12346 1 00000000 100 0 0 10 0
        """)
        tcp_file = tmp_path / "tcp"
        tcp_file.write_text(content)

        established, listen_ports = parse_proc_net_tcp(str(tcp_file))
        # ESTABLISHED (state 01) in connections, LISTEN (state 0A) in listen_ports
        assert len(established) == 1
        local_ip, local_port, remote_ip, remote_port = established[0]
        assert local_ip == "127.0.0.1"
        assert local_port == 80
        assert remote_ip == "192.168.1.100"
        assert remote_port == 54321
        assert listen_ports == {81}  # port 0x0051 = 81

    def test_empty_file(self, tmp_path):
        tcp_file = tmp_path / "tcp"
        tcp_file.write_text("  sl  local_address rem_address   st\n")
        established, listen_ports = parse_proc_net_tcp(str(tcp_file))
        assert established == []
        assert listen_ports == set()

    def test_missing_file(self):
        established, listen_ports = parse_proc_net_tcp("/nonexistent/path/tcp")
        assert established == []
        assert listen_ports == set()

    def test_multiple_established(self, tmp_path):
        content = textwrap.dedent("""\
            sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode
             0: 0500580A:A1B2 08080808:01BB 01 00000000:00000000 00:00000000 00000000  1000        0 11111 1 00000000 100 0 0 10 0
             1: 0500580A:A1B3 01010101:0050 01 00000000:00000000 00:00000000 00000000  1000        0 22222 1 00000000 100 0 0 10 0
        """)
        tcp_file = tmp_path / "tcp"
        tcp_file.write_text(content)

        established, listen_ports = parse_proc_net_tcp(str(tcp_file))
        assert len(established) == 2
        # First: 10.88.0.5:41394 → 8.8.8.8:443
        assert established[0][2] == "8.8.8.8"
        assert established[0][3] == 443
        # Second: 10.88.0.5:41395 → 1.1.1.1:80
        assert established[1][2] == "1.1.1.1"
        assert established[1][3] == 80
        assert listen_ports == set()  # no LISTEN entries


class TestGetConnections:
    def test_returns_empty_when_no_containers(self, mocker):
        mocker.patch(
            "quadletman.services.agent._get_container_pids",
            return_value={},
        )
        assert _get_connections() == []

    def test_classifies_outbound_via_listen_ports(self, mocker):
        mocker.patch(
            "quadletman.services.agent._get_container_pids",
            return_value={"nginx": 100},
        )
        # Outbound: local port 8080 is NOT a listening port → outbound
        mocker.patch(
            "quadletman.services.agent.parse_proc_net_tcp",
            side_effect=lambda path, **_kw: (
                ([("10.88.0.5", 54321, "8.8.8.8", 443)], {80})
                if "tcp6" not in path
                else ([], set())
            ),
        )

        conns = _get_connections()
        assert len(conns) == 1
        assert conns[0]["container_name"] == "nginx"
        assert conns[0]["dst_ip"] == "8.8.8.8"
        assert conns[0]["dst_port"] == 443
        assert conns[0]["direction"] == "outbound"

    def test_classifies_inbound_via_listen_ports(self, mocker):
        mocker.patch(
            "quadletman.services.agent._get_container_pids",
            return_value={"nginx": 100},
        )
        # Inbound: local port 80 IS a listening port → inbound
        mocker.patch(
            "quadletman.services.agent.parse_proc_net_tcp",
            side_effect=lambda path, **_kw: (
                ([("10.88.0.5", 80, "192.168.1.100", 54321)], {80})
                if "tcp6" not in path
                else ([], set())
            ),
        )

        conns = _get_connections()
        assert len(conns) == 1
        assert conns[0]["direction"] == "inbound"
        assert conns[0]["dst_ip"] == "192.168.1.100"

    def test_skips_loopback(self, mocker):
        mocker.patch(
            "quadletman.services.agent._get_container_pids",
            return_value={"app": 200},
        )
        mocker.patch(
            "quadletman.services.agent.parse_proc_net_tcp",
            side_effect=lambda path, **_kw: (
                ([("10.88.0.6", 8080, "127.0.0.1", 3306)], set())
                if "tcp6" not in path
                else ([], set())
            ),
        )

        assert _get_connections() == []

    def test_deduplicates(self, mocker):
        mocker.patch(
            "quadletman.services.agent._get_container_pids",
            return_value={"app": 300},
        )
        # Same connection in both tcp and tcp6
        mocker.patch(
            "quadletman.services.agent.parse_proc_net_tcp",
            return_value=([("10.88.0.7", 54321, "1.2.3.4", 443)], set()),
        )

        conns = _get_connections()
        assert len(conns) == 1
