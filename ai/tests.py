"""
ai/tests.py — Full test suite for the AI chat engine.
Run with: python manage.py test ai
"""

import json
from django.test import TestCase, Client
from django.urls import reverse
from unittest.mock import patch, MagicMock
from devices.models import Router, Port, Card, SFP
from backhaul.models import BackhaulLink


# ═══════════════════════════════════════════════════════════════
#  BASE: shared test data factory
# ═══════════════════════════════════════════════════════════════
class AITestBase(TestCase):

    def setUp(self):
        self.client = Client()
        self.chat_url = "/api/ai/chat/"   # ← adjust if your url is different
        self.stats_url = "/api/ai/stats/" # ← adjust if your url is different
        self._seed_database()

    def _seed_database(self):
        """Create minimal but realistic test data mirroring your real network."""

        # ── Routers ─────────────────────────────────────────────
        self.router_ari = Router.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            ne_type="NE8000-M8",
            ip_address="10.51.2.143",
            vendor="Huawei",
        )
        self.router_tun = Router.objects.create(
            ne_name="TUN_0005_PE_X8_0001",
            ne_type="NE40E-X8",
            ip_address="10.51.2.1",
            vendor="Huawei",
        )

        # ── Ports ────────────────────────────────────────────────
        Port.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            port_name="GigabitEthernet0/5/9",
            oper_status="Down",
            admin_status="Active",
        )
        Port.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            port_name="GigabitEthernet0/6/0",
            oper_status="Down",
            admin_status="Active",
        )
        Port.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            port_name="GigabitEthernet0/6/1",
            oper_status="Up",
            admin_status="Active",
        )
        Port.objects.create(
            ne_name="TUN_0005_PE_X8_0001",
            port_name="GigabitEthernet1/0/0",
            oper_status="Up",
            admin_status="Active",
        )

        # ── Cards ─────────────────────────────────────────────────
        self.card_critical = Card.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            board_type="EA-X4U10GE-M",
            board_name="EA-X4U10GE-M",
            description="10GE Line Processing Board",
            slot_id="3",
        )
        Card.objects.create(
            ne_name="TUN_0005_PE_X8_0001",
            board_type="EA-X4U10GE-M",   # same type → reallocation candidate
            board_name="EA-X4U10GE-M",
            description="10GE Line Processing Board",
            slot_id="5",
        )

        # ── SFPs ──────────────────────────────────────────────────
        SFP.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            port_name="GigabitEthernet0/5/9",
            rx_power=-40.0,   # Critical Alert
            tx_power=-6.03,
            rx_status="Critical Alert",
            tx_status="Normal",
        )
        SFP.objects.create(
            ne_name="ARI_0010_NE8000_M8_0143",
            port_name="GigabitEthernet0/6/0",
            rx_power=-40.0,   # Critical Alert
            tx_power=-40.0,   # Critical Alert both ways
            rx_status="Critical Alert",
            tx_status="Critical Alert",
        )
        SFP.objects.create(
            ne_name="TUN_0005_PE_X8_0001",
            port_name="GigabitEthernet1/0/0",
            rx_power=-5.2,    # Normal
            tx_power=-5.5,
            rx_status="Normal",
            tx_status="Normal",
        )

        # ── Backhaul Links ────────────────────────────────────────
        BackhaulLink.objects.create(
            link_name="GAB_0012_PE_X8_0019-MED_0001_PE_X8_0018",
            source_ne="GAB_0012_PE_X8_0019",
            sink_ne="MED_0001_PE_X8_0018",
            source_ip="10.51.2.19",
            sink_ip="10.51.2.18",
            alarm_severity="Critical",
        )
        BackhaulLink.objects.create(
            link_name="TUN_0005-ARI_0010",
            source_ne="TUN_0005_PE_X8_0001",
            sink_ne="ARI_0010_NE8000_M8_0143",
            source_ip="10.51.2.1",
            sink_ip="10.51.2.143",
            alarm_severity="Normal",
        )

    def _post_chat(self, message):
        """Helper: POST a message to the chat endpoint."""
        return self.client.post(
            self.chat_url,
            data=json.dumps({"messages": [{"role": "user", "content": message}]}),
            content_type="application/json",
        )


# ═══════════════════════════════════════════════════════════════
#  1. ENDPOINT AVAILABILITY
# ═══════════════════════════════════════════════════════════════
class TestEndpointAvailability(AITestBase):

    def test_chat_endpoint_exists(self):
        res = self._post_chat("network health")
        self.assertNotEqual(res.status_code, 404, "Chat endpoint not found — check urls.py")

    def test_stats_endpoint_exists(self):
        res = self.client.get(self.stats_url)
        self.assertNotEqual(res.status_code, 404, "Stats endpoint not found — check urls.py")

    def test_chat_returns_json(self):
        res = self._post_chat("network health")
        self.assertEqual(res["Content-Type"], "application/json")

    def test_chat_rejects_get(self):
        res = self.client.get(self.chat_url)
        self.assertEqual(res.status_code, 405)

    def test_empty_message_returns_400(self):
        res = self.client.post(
            self.chat_url,
            data=json.dumps({"messages": []}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)


# ═══════════════════════════════════════════════════════════════
#  2. CAPABILITY A — NETWORK SUMMARY (English + French)
# ═══════════════════════════════════════════════════════════════
class TestNetworkSummary(AITestBase):

    def test_network_health_english(self):
        res = self._post_chat("show network health status")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("DOWN", data["response"])

    def test_network_health_french(self):
        res = self._post_chat("état du réseau")
        data = res.json()
        self.assertIn("response", data)
        # Should respond in French
        self.assertTrue(
            "DOWN" in data["response"] or "état" in data["response"].lower(),
            "French query should trigger network summary"
        )

    def test_summary_counts_critical_links(self):
        res = self._post_chat("how many critical alarms on the network?")
        data = res.json()
        # We seeded 1 critical link — response should mention it
        self.assertIn("1", data["response"])

    def test_summary_counts_down_ports(self):
        res = self._post_chat("network status summary")
        data = res.json()
        # We seeded 2 down ports on ARI_0010
        self.assertIn("2", data["response"])


# ═══════════════════════════════════════════════════════════════
#  3. CAPABILITY B — SFP OPTICAL DIAGNOSTICS
# ═══════════════════════════════════════════════════════════════
class TestSFPDiagnostics(AITestBase):

    def test_sfp_check_english(self):
        res = self._post_chat("check sfp optical health")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("CRITICAL", data["response"].upper())

    def test_sfp_check_french(self):
        res = self._post_chat("vérifier la santé des sfp")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("CRITIQUE", data["response"].upper())

    def test_sfp_detects_critical_nodes(self):
        res = self._post_chat("check sfp lasers")
        data = res.json()
        # ARI_0010 has 2 critical SFPs — should be mentioned
        self.assertIn("ARI_0010", data["response"])

    def test_sfp_detects_rx_power_value(self):
        res = self._post_chat("optical power diagnostics")
        data = res.json()
        # Should report -40 dBm
        self.assertIn("-40", data["response"])

    def test_sfp_typo_optics(self):
        """spf instead of sfp — should still trigger optical diagnostics"""
        res = self._post_chat("check spf health")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("error", data)

    def test_sfp_all_normal_message(self):
        """When no critical SFPs exist, should say all normal."""
        SFP.objects.all().update(rx_power=-5.0, rx_status="Normal", tx_status="Normal")
        res = self._post_chat("check sfp optical health")
        data = res.json()
        self.assertTrue(
            "normal" in data["response"].lower() or "operational" in data["response"].lower()
        )


# ═══════════════════════════════════════════════════════════════
#  4. CAPABILITY C — SPARE PARTS / BOARD FAILURE
# ═══════════════════════════════════════════════════════════════
class TestSpareParts(AITestBase):

    def test_board_failure_english(self):
        res = self._post_chat("card failure on slot 3 of ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("EA-X4U10GE-M", data["response"])

    def test_board_failure_french(self):
        res = self._post_chat("panne carte slot 3 sur ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("EA-X4U10GE-M", data["response"])

    def test_reallocation_candidate_found(self):
        """TUN_0005 has same board type — should appear as reallocation candidate."""
        res = self._post_chat("board failure slot 3 ARI_0010")
        data = res.json()
        self.assertIn("TUN_0005", data["response"])

    def test_unknown_slot_returns_message(self):
        res = self._post_chat("card failure on slot 99 of ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        # Should say no card found on that slot
        self.assertTrue(
            "slot 99" in data["response"].lower() or "not found" in data["response"].lower()
        )

    def test_unknown_node_returns_message(self):
        res = self._post_chat("card failure on slot 3 of TOTALLY_FAKE_NODE_9999")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("error", data)

    def test_pane_typo_triggers_spare_parts(self):
        """'pane' instead of 'panne' — fuzzy matching should still work"""
        res = self._post_chat("pane carte slot 3 sur ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("error", data)


# ═══════════════════════════════════════════════════════════════
#  5. CAPABILITY D — PORT STATUS PER NODE
# ═══════════════════════════════════════════════════════════════
class TestPortStatus(AITestBase):

    def test_port_status_english(self):
        res = self._post_chat("show port status on ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertIn("ARI_0010", data["response"])

    def test_port_status_french(self):
        res = self._post_chat("statut des ports sur ARI_0010")
        data = res.json()
        self.assertIn("response", data)

    def test_port_counts_correct(self):
        """ARI_0010 has 2 down, 1 up — verify counts."""
        res = self._post_chat("ports down on ARI_0010")
        data = res.json()
        self.assertIn("2", data["response"])  # 2 down
        self.assertIn("1", data["response"])  # 1 up

    def test_port_typo_por(self):
        """'por' typo should still trigger port check"""
        res = self._post_chat("por status on ARI_0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("error", data)

    def test_port_unknown_node(self):
        res = self._post_chat("port status on UNKNOWN_NODE_XYZ")
        data = res.json()
        self.assertIn("response", data)
        # Should return a helpful message, not crash
        self.assertNotIn("error", data)


# ═══════════════════════════════════════════════════════════════
#  6. FUZZY MATCHING ENGINE
# ═══════════════════════════════════════════════════════════════
class TestFuzzyMatching(AITestBase):

    def test_fuzzy_partial_name(self):
        """ARI0010 instead of ARI_0010 — should still resolve"""
        res = self._post_chat("ports down on ARI0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("No active interface", data["response"])

    def test_fuzzy_lowercase(self):
        res = self._post_chat("port status on ari_0010_ne8000")
        data = res.json()
        self.assertIn("response", data)

    def test_fuzzy_card_resolution(self):
        """ARI-0010 with dash instead of underscore"""
        res = self._post_chat("card failure slot 3 on ARI-0010")
        data = res.json()
        self.assertIn("response", data)
        self.assertNotIn("could not be found", data["response"].lower())


# ═══════════════════════════════════════════════════════════════
#  7. LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════
class TestLanguageDetection(AITestBase):

    def test_english_response_has_english_keywords(self):
        res = self._post_chat("show network status")
        data = res.json()
        # English responses use "DOWN", "UP", "Total"
        self.assertTrue(
            any(word in data["response"] for word in ["DOWN", "Total", "Status", "Network"])
        )

    def test_french_response_has_french_keywords(self):
        res = self._post_chat("état du réseau")
        data = res.json()
        self.assertTrue(
            any(word in data["response"] for word in ["DOWN", "Total", "Réseau", "état", "DÉTECTÉ"])
        )

    def test_french_sfp_uses_french_labels(self):
        res = self._post_chat("vérifier les sfp sur le réseau")
        data = res.json()
        self.assertTrue(
            "CRITIQUE" in data["response"].upper() or "Remplacement" in data["response"]
        )


# ═══════════════════════════════════════════════════════════════
#  8. STATS ENDPOINT
# ═══════════════════════════════════════════════════════════════
class TestStatsEndpoint(AITestBase):

    def test_stats_returns_all_fields(self):
        res = self.client.get(self.stats_url)
        data = res.json()
        self.assertIn("critical_sfps", data)
        self.assertIn("down_ports", data)
        self.assertIn("critical_links", data)
        self.assertIn("total_routers", data)

    def test_stats_critical_sfps_count(self):
        res = self.client.get(self.stats_url)
        data = res.json()
        # We seeded 2 SFPs with rx_power = -40 (below -38 threshold)
        self.assertEqual(data["critical_sfps"], 2)

    def test_stats_down_ports_count(self):
        res = self.client.get(self.stats_url)
        data = res.json()
        # We seeded 2 down ports
        self.assertEqual(data["down_ports"], 2)

    def test_stats_critical_links_count(self):
        res = self.client.get(self.stats_url)
        data = res.json()
        # We seeded 1 critical backhaul link
        self.assertEqual(data["critical_links"], 1)

    def test_stats_total_routers(self):
        res = self.client.get(self.stats_url)
        data = res.json()
        # We seeded 2 routers
        self.assertEqual(data["total_routers"], 2)


# ═══════════════════════════════════════════════════════════════
#  9. EDGE CASES & ROBUSTNESS
# ═══════════════════════════════════════════════════════════════
class TestEdgeCases(AITestBase):

    def test_empty_string_message(self):
        res = self.client.post(
            self.chat_url,
            data=json.dumps({"messages": [{"role": "user", "content": ""}]}),
            content_type="application/json",
        )
        # Should not crash — either 200 with fallback or 400
        self.assertIn(res.status_code, [200, 400])

    def test_malformed_json_returns_error(self):
        res = self.client.post(
            self.chat_url,
            data="not json at all",
            content_type="application/json",
        )
        self.assertIn(res.status_code, [400, 500])

    def test_unrecognized_query_returns_help_message(self):
        res = self._post_chat("what is the weather today in Tunis?")
        data = res.json()
        self.assertIn("response", data)
        # Should return the help/menu message
        self.assertTrue(
            "iris" in data["response"].lower() or
            "1." in data["response"] or
            "sfp" in data["response"].lower()
        )

    def test_very_long_message(self):
        long_msg = "show port status on ARI_0010 " * 50
        res = self._post_chat(long_msg)
        self.assertIn(res.status_code, [200, 400])
        self.assertNotEqual(res.status_code, 500)

    def test_sfp_with_null_rx_power(self):
        """SFPs with NULL rx_power should not crash the engine."""
        SFP.objects.create(
            ne_name="TEST_NULL_SFP",
            port_name="GE0/0/0",
            rx_power=None,
            tx_power=None,
            rx_status="Unknown",
            tx_status="Unknown",
        )
        res = self._post_chat("check sfp health")
        self.assertEqual(res.status_code, 200)

    def test_no_routers_in_db(self):
        """Stats endpoint should handle empty tables gracefully."""
        Router.objects.all().delete()
        res = self.client.get(self.stats_url)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_routers"], 0)
