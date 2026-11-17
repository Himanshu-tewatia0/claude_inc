import time
from claude_inc import AgentFirewall

def run_rogue_agent_simulation():
    print('========================================================')
    print('   CLAUDE-INC: ROGUE AGENT & RUNAWAY LOOP SIMULATOR     ')
    print('========================================================')
    
    # Initialize firewall with strict constraints: .03 budget, max 3 calls per minute
    firewall = AgentFirewall(max_budget_usd=0.03, max_calls_per_minute=3)
    
    # Simulating an autonomous agent executing tasks in a loop
    for step in range(1, 6):
        print(f'\n[Agent Iteration {step}] Attempting LLM execution...')
        try:
            # Pre-flight check before API call
            firewall.pre_flight_check(estimated_cost_usd=0.01)
            
            # Simulate API call latency & success
            time.sleep(0.2)
            firewall.log_spend(0.01)
            print(f'[SUCCESS] Iteration {step} completed safely.')
            
        except Exception as e:
            print(f'\n[CRITICAL ALERT CAUGHT BY CLAUDE-INC]: {e}')
            print('[DEFENSE ENGAGED] Runaway loop terminated locally. Zero upstream bill incurred!')
            break

if __name__ == '__main__':
    run_rogue_agent_simulation()
