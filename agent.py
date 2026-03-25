"""
agent.py — Nova's brain
========================
This is the core of the agent. It:

1. Builds a rich system prompt using stored memory (facts + last report)
2. Loads recent conversation history from SQLite
3. Calls Claude Sonnet with tools (web_search + portfolio_research)
4. Handles Claude's tool calls in an agentic loop
5. Saves the exchange to memory
6. Extracts any new facts from the conversation automatically

The agentic loop is the key concept here. Claude doesn't just respond once —
it can call a tool, get results, think again, call another tool, then respond.
We keep looping until Claude stops calling tools and writes its final answer.
"""

import json
import logging
import re
from datetime import datetime
from anthropic import Anthropic
from memory import Memory
from portfolio import fetch_market_data, load_holdings, build_analysis_prompt

log = logging.getLogger("nova.agent")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000
MAX_TOOL_LOOPS = 5  # safety cap — prevents runaway tool calling


# ── System Prompt ─────────────────────────────────────────────────────────────

def build_system_prompt(memory: Memory) -> str:
    """
    Build Nova's system prompt dynamically, injecting:
    - Her persona and capabilities
    - Everything she remembers about the user (facts)
    - A summary of the last portfolio report (for comparison)

    Lesson: The system prompt is sent with EVERY API call. This is how
    you give the LLM its 'personality' and persistent context. It doesn't
    change mid-conversation — it's the stable foundation.
    """
    facts = memory.format_facts_for_prompt()
    last_report = memory.get_report_summary("weekly")

    return f"""
You are Nova, a highly capable personal AI agent and CFA-qualified financial analyst 
with 25 years of institutional portfolio management experience.

You are the dedicated assistant for one specific user. You remember everything 
they tell you and build on it over time. You are proactive, precise, and direct — 
you don't hedge unnecessarily or pad responses with filler.

== WHAT YOU KNOW ABOUT YOUR USER ==
{facts}

== LAST WEEKLY PORTFOLIO REPORT ==
{last_report}

== YOUR CAPABILITIES ==
- Portfolio research: fetch live market data, search for news, write CFA-style briefs
- Scheduled reports: weekly pulse, monthly review, quarterly deep-dive
- General research: answer any question using web search
- Memory: remember facts the user tells you and reference them naturally

== MEMORY INSTRUCTIONS ==
When the user tells you something important about themselves, their portfolio,
or their preferences, extract it as a fact in this exact format at the END 
of your response (invisible to user, used internally):

FACT: category | key | value

Examples:
FACT: personal | name | Robert
FACT: portfolio | holdings_file | ~/portfolio/holdings.csv  
FACT: preference | report_style | concise with bullet points
FACT: portfolio | risk_tolerance | moderate-aggressive

Only emit FACT lines for genuinely new or updated information. 
Do not repeat facts you already know.

== STYLE ==
- Write like a Bloomberg terminal analyst, not a chatbot
- Be direct and concise unless a detailed report is requested
- Use markdown formatting — Telegram renders bold (*text*) and code (`text`)
- For portfolio reports, always use the structured format: 
  Snapshot → Market Context → Position Notes → Risk Flags → Themes

Today's date: {datetime.now().strftime("%A, %B %d, %Y")}
""".strip()


# ── Tool Definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        # Web search — Claude decides when to use this
        "type": "web_search_20250305",
        "name": "web_search",
    },
    {
        # Portfolio research — triggers our local data fetch
        "name": "run_portfolio_research",
        "description": (
            "Fetch live market data for the user's portfolio holdings and "
            "prepare a structured research brief. Use this when the user asks "
            "for a portfolio update, weekly brief, monthly review, quarterly review, "
            "or research on a specific ticker. "
            "mode options: 'weekly', 'monthly', 'quarterly', 'single:TICKER'"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "description": "Analysis mode: weekly | monthly | quarterly | single:TICKER",
                    "default": "weekly"
                }
            },
            "required": ["mode"]
        }
    }
]


# ── Tool Execution ─────────────────────────────────────────────────────────────

async def execute_tool(tool_name: str, tool_input: dict) -> str:
    """
    Execute a tool call requested by Claude and return the result as a string.

    Lesson: When Claude calls a tool, we get back:
      - tool_name: which tool it wants to use
      - tool_input: the arguments it's passing

    We execute the tool, get results, then feed them back to Claude
    as a 'tool_result' message so it can continue reasoning.
    """
    log.info(f"Tool call: {tool_name}({tool_input})")

    if tool_name == "run_portfolio_research":
        try:
            mode = tool_input.get("mode", "weekly")
            holdings_df = load_holdings()
            market_data = fetch_market_data(holdings_df)
            prompt_data = build_analysis_prompt(market_data, mode)
            # Return the raw data as JSON — Claude will write the actual report
            return json.dumps({
                "status": "success",
                "mode": mode,
                "holdings_count": len(market_data),
                "market_data": market_data,
                "analysis_instructions": prompt_data
            }, default=str)
        except FileNotFoundError as e:
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    # web_search is handled natively by Anthropic — we never hit this branch
    return json.dumps({"status": "error", "message": f"Unknown tool: {tool_name}"})


# ── Fact Extraction ────────────────────────────────────────────────────────────

def extract_and_save_facts(response_text: str, memory: Memory):
    """
    Parse FACT: lines from Claude's response and save them to SQLite.
    Then strip the FACT lines from the response before sending to user.

    Lesson: This is a simple but effective pattern called 'structured extraction'.
    We instruct Claude to embed machine-readable data in its output, then
    parse it programmatically. No separate API call needed.
    """
    fact_pattern = re.compile(
        r'^FACT:\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+)$',
        re.MULTILINE
    )
    facts_found = fact_pattern.findall(response_text)

    for category, key, value in facts_found:
        memory.upsert_fact(
            category=category.strip().lower(),
            key=key.strip().lower(),
            value=value.strip(),
            source="conversation"
        )
        log.debug(f"Extracted fact: [{category}] {key} = {value}")

    # Remove FACT lines from the response the user sees
    clean_response = fact_pattern.sub("", response_text).strip()
    return clean_response


# ── Main Agent Loop ────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, memory: Memory):
        self.memory = memory
        self.client = Anthropic()

    async def chat(self, user_message: str) -> str:
        """
        Process a user message and return Nova's response.

        This implements the agentic loop:
        1. Build context (system prompt + history + new message)
        2. Call Claude
        3. If Claude calls a tool → execute it → loop back to step 2
        4. If Claude writes text → extract facts → return to user

        The loop continues until Claude either:
        - Returns a final text response (stop_reason = "end_turn")
        - Hits our MAX_TOOL_LOOPS safety cap
        """

        # Save user message to history
        self.memory.add_message("user", user_message)

        # Build message list: history + current message
        messages = self.memory.get_recent_history(limit=20)

        # Ensure the last message is the current one
        # (get_recent_history already includes it since we just saved it)

        system_prompt = build_system_prompt(self.memory)

        loop_count = 0
        final_response = None

        while loop_count < MAX_TOOL_LOOPS:
            loop_count += 1
            log.info(f"Agent loop {loop_count}/{MAX_TOOL_LOOPS}")

            # Call Claude
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )

            log.info(f"Stop reason: {response.stop_reason}")

            # ── Case 1: Claude wants to use a tool ────────────────────────────
            if response.stop_reason == "tool_use":

                # Add Claude's response (with tool calls) to message history
                messages.append({
                    "role": "assistant",
                    "content": response.content
                })

                # Execute each tool Claude requested
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = await execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Add tool results back into messages so Claude can continue
                messages.append({
                    "role": "user",
                    "content": tool_results
                })

                # Loop again — Claude will now reason over the tool results

            # ── Case 2: Claude is done, return final response ──────────────────
            elif response.stop_reason == "end_turn":

                # Extract text from response blocks
                text_blocks = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                raw_response = "\n\n".join(text_blocks).strip()

                # Extract and save any facts Claude embedded
                final_response = extract_and_save_facts(raw_response, self.memory)

                # Save assistant response to history
                self.memory.add_message("assistant", final_response)

                # If this looks like a portfolio report, save it
                if any(kw in user_message.lower() for kw in
                       ["brief", "report", "review", "portfolio", "stocks"]):
                    mode = "weekly"
                    if "quarterly" in user_message.lower():
                        mode = "quarterly"
                    elif "monthly" in user_message.lower():
                        mode = "monthly"
                    self.memory.save_report(mode, final_response)

                break

            else:
                # Unexpected stop reason
                log.warning(f"Unexpected stop_reason: {response.stop_reason}")
                final_response = "I encountered an unexpected issue. Please try again."
                break

        if final_response is None:
            final_response = "I hit my tool loop limit. Please try a simpler query."

        return final_response
