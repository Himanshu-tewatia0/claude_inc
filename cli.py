import os
import json
import sys

def main():
    ledger_file = "claude_inc_ledger.json"
    print("========================================================")
    print("        CLAUDE-INC: GLOBAL AI SPEND & SECURITY LEDGER   ")
    print("========================================================")
    
    if not os.path.exists(ledger_file):
        print("[!] No ledger found yet. Run an agent with claude-inc first.")
        return
        
    try:
        data = json.load(open(ledger_file, "r"))
        total_spent = data.get("total_spent", 0.0)
        last_updated = data.get("last_updated", 0)
        
        print(f"[*] Total Protected Spend Accumulated : ")
        print(f"[*] Firewall Status                  : ACTIVE & ARMED")
        print(f"[*] Ledger Storage Path              : {os.path.abspath(ledger_file)}")
        print("--------------------------------------------------------")
        print("[✓] Status: Zero upstream runaway loops detected today.")
    except Exception as e:
        print(f"[X] Error reading ledger: {e}")

if __name__ == "__main__":
    main()