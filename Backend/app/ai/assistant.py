"""
Fleet AI Assistant — Groq agentic loop with tool calling.
Keeps AI logic isolated from existing fleet API routes.
"""

import json
import os
from typing import List, Dict, Any

from dotenv import load_dotenv
from groq import Groq

from app.ai.prompts import SYSTEM_PROMPT
from app.ai.tools import TOOL_DEFINITIONS, dispatch_tool

load_dotenv()

_groq_client: Groq | None = None

# Client-supplied conversation limits. Only plain user/assistant turns are accepted, so a request can never
# inject its own system prompt or fabricated tool results.
ALLOWED_ROLES = ("user", "assistant")
MAX_MESSAGES = 50
MAX_MESSAGE_CHARS = 6000
# Only the most recent turns (within a character budget) are forwarded to the model.
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 24000

# Tool-call bounds per assistant run.
MAX_TOOL_CALLS_PER_RUN = 12
MAX_CALLS_PER_TOOL = {"get_idle_events": 2}


def validate_chat_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """
    Validate a client conversation and return the role/content-only turns to send to the model.
    Raises ValueError with a user-safe message on invalid input.
    """
    if not messages:
        raise ValueError("No messages provided.")
    if len(messages) > MAX_MESSAGES:
        raise ValueError(f"Too many messages: at most {MAX_MESSAGES} are allowed per request.")

    cleaned = []
    for i, m in enumerate(messages):
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else None
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Message {i + 1} has an unsupported role; only 'user' and 'assistant' are allowed.")
        if not isinstance(content, str):
            raise ValueError(f"Message {i + 1} content must be text.")
        if len(content) > MAX_MESSAGE_CHARS:
            raise ValueError(f"Message {i + 1} is too long: at most {MAX_MESSAGE_CHARS} characters are allowed.")
        cleaned.append({"role": role, "content": content})

    if cleaned[-1]["role"] != "user" or not cleaned[-1]["content"].strip():
        raise ValueError("The last message must be a non-empty user message.")

    # Keep the newest turns that fit the context budget (the last user turn always fits: it is <= MAX_MESSAGE_CHARS).
    window, total = [], 0
    for m in reversed(cleaned[-MAX_CONTEXT_MESSAGES:]):
        total += len(m["content"])
        if total > MAX_CONTEXT_CHARS:
            break
        window.append(m)
    return list(reversed(window))


def _get_client() -> Groq:
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not configured in the environment.")
        _groq_client = Groq(api_key=api_key)
    return _groq_client


def run_assistant(messages: List[Dict[str, Any]]) -> str:
    """
    Run the agentic loop:
      1. Send conversation + tools to Groq.
      2. If Groq requests a tool call, dispatch it and continue.
      3. Return the final text reply.

    `messages` is the full conversation history (excluding the system prompt).
    Maximum 6 agentic steps to prevent runaway loops.
    """
    client = _get_client()

    # Build the full message list with system prompt prepended
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + validate_chat_messages(messages)

    MAX_STEPS = 6
    total_calls = 0
    calls_per_tool: Dict[str, int] = {}

    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=full_messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=1024,
        )

        message = response.choices[0].message

        # No tool calls → final text answer
        if not message.tool_calls:
            return message.content or ""

        # Append assistant's tool-call request to the conversation
        full_messages.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ],
        })

        # Execute each requested tool and append results
        for tc in message.tool_calls:
            name = tc.function.name
            tool_limit = MAX_CALLS_PER_TOOL.get(name)
            if total_calls >= MAX_TOOL_CALLS_PER_RUN or (tool_limit is not None and calls_per_tool.get(name, 0) >= tool_limit):
                # Every tool_call_id still needs a tool message; tell the model the call was not executed.
                tool_result = json.dumps({"error": f"Tool call limit reached; {name} was not executed. Answer with the data already retrieved."})
            else:
                total_calls += 1
                calls_per_tool[name] = calls_per_tool.get(name, 0) + 1
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                tool_result = dispatch_tool(name, args)

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": tool_result,
            })

    # Fallback: ask Groq for a final answer without further tool calls
    full_messages.append({
        "role": "user",
        "content": "Please summarize the information retrieved so far and answer the question.",
    })
    final_response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=full_messages,
        temperature=0.2,
        max_tokens=1024,
    )
    return final_response.choices[0].message.content or ""
