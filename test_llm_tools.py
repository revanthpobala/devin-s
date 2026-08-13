from src.clients.llm_client import query_local_llm, execute_tool_call

sys_prompt = "You are a research AI. Use the provided tools to answer the user's question. If asked about the probability of a macro event, use fetch_prediction_market."
usr_prompt = "Can you check Kalshi to see the probability of the Fed cutting rates? Also check if there are any prediction markets for Amazon (AMZN) earnings."

response = query_local_llm(sys_prompt, usr_prompt, use_tools=True, summarize_tool_context="")
print('--- LLM RESPONSE ---')
print(response)
