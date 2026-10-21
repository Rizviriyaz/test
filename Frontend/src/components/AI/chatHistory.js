// Bounds the conversation sent to /api/ai/chat. Mirrors the backend context window in app/ai/assistant.py
// (MAX_CONTEXT_MESSAGES / MAX_CONTEXT_CHARS): older turns would be dropped server-side anyway, and sending the
// full history makes long chats exceed the backend's MAX_MESSAGES limit (HTTP 400).
export const MAX_HISTORY_MESSAGES = 20
export const MAX_HISTORY_CHARS = 24000

/**
 * Newest contiguous turns that fit the message and character budgets, as { role, content } only.
 * The latest message (the user's new question) is always kept, even if it alone exceeds the budget,
 * so the backend can return its own length error instead of the question being silently dropped.
 */
export function buildChatHistory(messages) {
    const recent = (messages || []).slice(-MAX_HISTORY_MESSAGES)
    const history = []
    let total = 0
    for (let i = recent.length - 1; i >= 0; i--) {
        const { role, content } = recent[i]
        const length = typeof content === "string" ? content.length : 0
        if (history.length > 0 && total + length > MAX_HISTORY_CHARS) break
        total += length
        history.unshift({ role, content })
    }
    return history
}
