import os
import json
import time

class AtomicLedger:
    def __init__(self, path="agentfuse_ledger.json"):
        self.path = path

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"total_spent": 0.0}

    def save(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
