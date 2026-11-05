"""Small failure-oriented checks; never imports a financial runner or reads secrets."""
import importlib
import json
import socket
import sys
import unittest

import guard
import standalone_adapter as adapter


class GuardChecks(unittest.TestCase):
    def test_private_and_external_paths_are_rejected_before_read(self):
        for name in ("user_data/deployment/.env.mainnet", ".env", "/tmp/unregistered.json", "../../outside.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                guard.local_path(name)

    def test_tampered_hash_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed registered input"):
            guard.checked({"path": "research/crypto/rd_program_20261105/plan.json", "sha256": "0" * 64})

    def test_four_dataset_paths_are_portable_and_native_risk_is_explicit(self):
        for dataset in ("spot_L", "spot_S", "futures_L", "futures_S"):
            with self.subTest(dataset=dataset):
                plan = adapter.load_prepared_plan(dataset)
                self.assertFalse("CZ-Agent" in json.dumps(plan), "Legacy absolute provenance leaked into executable plan")
                self.assertEqual(plan["engine_source"], str(guard.ROOT))
                self.assertEqual(plan["risk_profile"], "native_baseline")
                self.assertFalse(plan["RD1_applied"])
                self.assertEqual(plan["research_support"], {})

    def test_modified_economic_contract_is_rejected_before_runner_load(self):
        plan = adapter.load_prepared_plan("spot_S")
        plan["initial_equity_usdt"] = 2000
        with self.assertRaisesRegex(ValueError, "contract changed"):
            adapter.verify_prepared_plan(plan, plan["candidates"][0])

    def test_missing_5m_mark_is_not_reclassified(self):
        for dataset in ("futures_L", "futures_S"):
            self.assertFalse(adapter.dataset_record(dataset)["five_minute_mark_available"])

    def test_legacy_namespace_and_sockets_fail_closed(self):
        old_path = sys.meta_path[:]
        old_bytecode = sys.dont_write_bytecode
        connect, connect_ex, create = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
        try:
            guard.install_runtime_guards()
            with self.assertRaises(ImportError):
                importlib.import_module("freqtrade.research")
            with self.assertRaises(RuntimeError):
                socket.create_connection(("127.0.0.1", 1))
        finally:
            sys.meta_path[:] = old_path
            sys.dont_write_bytecode = old_bytecode
            socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create


if __name__ == "__main__":
    unittest.main(verbosity=2)
