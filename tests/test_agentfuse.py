import unittest
import os
from agentfuse import AgentFirewall

class TestAgentFirewall(unittest.TestCase):
    def setUp(self):
        self.ledger_path = "agentfuse_ledger.json"
        if os.path.exists(self.ledger_path):
            os.remove(self.ledger_path)

    def test_budget_cap(self):
        fw = AgentFirewall(max_budget_usd=0.02)
        fw.pre_flight_check(0.01)
        fw.log_spend(0.01)
        fw.pre_flight_check(0.01)
        fw.log_spend(0.01)
        with self.assertRaises(PermissionError):
            fw.pre_flight_check(0.01)

if __name__ == "__main__":
    unittest.main()
