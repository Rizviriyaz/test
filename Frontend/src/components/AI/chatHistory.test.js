// Offline tests for the AI chat history bound. Run: npm test (node --test).
import { test } from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { buildChatHistory, MAX_HISTORY_MESSAGES, MAX_HISTORY_CHARS } from "./chatHistory.js"

const WELCOME = { role: "assistant", content: "Hello! I am **Fleet AI**." }
const BACKEND_MAX_MESSAGES = 50
const BACKEND_MAX_MESSAGE_CHARS = 6000

function conversation(exchanges, size = 10) {
    const msgs = [WELCOME]
    for (let i = 0; i < exchanges; i++) {
        msgs.push({ role: "user", content: `q${i} `.padEnd(size, "u") })
        msgs.push({ role: "assistant", content: `a${i} `.padEnd(size, "a") })
    }
    return msgs
}

test("short conversations are sent unchanged", () => {
    const msgs = [...conversation(2), { role: "user", content: "latest" }]
    assert.deepEqual(buildChatHistory(msgs), msgs)
})

test("long conversations are bounded and stay within backend limits", () => {
    const msgs = [...conversation(60), { role: "user", content: "latest question" }]
    assert.ok(msgs.length > BACKEND_MAX_MESSAGES)
    const sent = buildChatHistory(msgs)
    assert.equal(sent.length, MAX_HISTORY_MESSAGES)
    assert.ok(sent.length <= BACKEND_MAX_MESSAGES)
    assert.deepEqual(sent[sent.length - 1], { role: "user", content: "latest question" })
    assert.deepEqual(sent, msgs.slice(-MAX_HISTORY_MESSAGES), "newest contiguous turns, order preserved")
})

test("character budget drops the oldest turns first", () => {
    const msgs = [...conversation(20, 5000), { role: "user", content: "latest" }]
    const sent = buildChatHistory(msgs)
    const total = sent.reduce((n, m) => n + m.content.length, 0)
    assert.ok(total <= MAX_HISTORY_CHARS, `total ${total}`)
    assert.ok(sent.length < MAX_HISTORY_MESSAGES)
    assert.deepEqual(sent, msgs.slice(-sent.length))
    assert.equal(sent[sent.length - 1].content, "latest")
})

test("latest user message is always kept, even when it alone exceeds the budget", () => {
    const huge = { role: "user", content: "x".repeat(MAX_HISTORY_CHARS + 1) }
    assert.deepEqual(buildChatHistory([...conversation(3), huge]), [huge])
})

test("roles and content are kept intact; UI-only fields are not sent", () => {
    const msgs = [{ role: "assistant", content: "**bold** [View on map](#map:1)", id: 7, pending: true },
        { role: "user", content: "  spaced  " }]
    assert.deepEqual(buildChatHistory(msgs), [
        { role: "assistant", content: "**bold** [View on map](#map:1)" },
        { role: "user", content: "  spaced  " },
    ])
})

test("limits match the backend context window and per-message cap", () => {
    const backend = readFileSync(new URL("../../../../Backend/app/ai/assistant.py", import.meta.url), "utf8")
    const num = (name) => Number(backend.match(new RegExp(`^${name} = (\\d+)`, "m"))[1])
    assert.equal(num("MAX_CONTEXT_MESSAGES"), MAX_HISTORY_MESSAGES)
    assert.equal(num("MAX_CONTEXT_CHARS"), MAX_HISTORY_CHARS)
    assert.equal(num("MAX_MESSAGES"), BACKEND_MAX_MESSAGES)
    assert.equal(num("MAX_MESSAGE_CHARS"), BACKEND_MAX_MESSAGE_CHARS)
})

test("widget sends the bounded history", () => {
    const widget = readFileSync(new URL("./AiAssistantWidget.jsx", import.meta.url), "utf8")
    assert.match(widget, /sendAiChatMessage\(buildChatHistory\(updatedMessages\)\)/)
})
