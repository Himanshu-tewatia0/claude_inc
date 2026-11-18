from agentfuse import AgentFirewall

fw = AgentFirewall(max_budget_usd=0.05)

@fw.guard(estimated_cost=0.01)
def run_agent():
    print("[DEMO] Agent call executed securely under budget guard.")

if __name__ == "__main__":
    for i in range(3):
        print(f"Run {i+1}:")
        run_agent()
