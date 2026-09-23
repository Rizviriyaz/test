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
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(messages)

    MAX_STEPS = 6

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
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            tool_result = dispatch_tool(tc.function.name, args)

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
