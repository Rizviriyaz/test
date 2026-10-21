import { useState, useRef, useEffect, useCallback } from "react"
import { useNavigate } from "react-router-dom"
import {
    Bot,
    X,
    Send,
    Trash2,
    Loader2,
    RefreshCw,
    User,
    MapPin,
    ExternalLink,
    Navigation,
    Clock,
    Sparkles,
    ChevronDown,
} from "lucide-react"
import { useAuth } from "../../context/useAuth"
import { sendAiChatMessage } from "../../api/fleetApi"
import { buildChatHistory } from "./chatHistory"

const QUICK_PROMPTS = [
    "How is the fleet doing?",
    "Which vehicle is idling the longest?",
    "How much fuel did we consume?",
    "Which vehicle has the worst fuel efficiency?",
    "Which vehicles have high fuel relative to distance?",
]

function extractActionsAndCleanText(content) {
    if (!content) return { text: "", actions: [] }

    // Identify any vehicle ID mentioned in the text (e.g. #map:29432, Vehicle 29432, **29432**)
    const idMatch =
        content.match(/#(?:map|vehicle|trips):(\d+)/i) ||
        content.match(/(?:Vehicle|ID)\s*(?:#|\*\*)?\s*(\d{3,8})/i) ||
        content.match(/\*\*(\d{4,8})\*\*/)
    const fallbackVehicleId = idMatch ? idMatch[1] : null

    const actions = []
    const actionRegex = /\[([^\]]+)\](?:\((#[^)]+)\))?/g

    const cleanedText = content
        .replace(actionRegex, (fullMatch, label, href) => {
            const trimmedLabel = label.trim()
            const lowerLabel = trimmedLabel.toLowerCase()

            const isAction =
                href ||
                lowerLabel.includes("map") ||
                lowerLabel.includes("detail") ||
                lowerLabel.includes("trip") ||
                lowerLabel.includes("idle") ||
                lowerLabel.startsWith("show ") ||
                lowerLabel.startsWith("view ") ||
                lowerLabel.includes("status") ||
                lowerLabel.includes("location")

            if (!isAction) {
                return fullMatch
            }

            let type = "prompt"
            let targetId = fallbackVehicleId

            if (href) {
                if (href.startsWith("#map:")) {
                    type = "map"
                    targetId = href.replace("#map:", "").trim()
                } else if (href.startsWith("#vehicle:")) {
                    type = "vehicle"
                    targetId = href.replace("#vehicle:", "").trim()
                } else if (href.startsWith("#trips:")) {
                    type = "trips"
                    targetId = href.replace("#trips:", "").trim()
                } else if (href.startsWith("#idle")) {
                    type = "idle"
                }
            } else {
                if (lowerLabel.includes("map")) {
                    type = "map"
                } else if (lowerLabel.includes("detail")) {
                    type = "vehicle"
                } else if (lowerLabel.includes("trip")) {
                    type = "trips"
                } else if (lowerLabel.includes("idle")) {
                    type = "idle"
                } else {
                    type = "prompt"
                }
            }

            actions.push({
                label: trimmedLabel,
                type,
                targetId,
                prompt: trimmedLabel,
            })

            return ""
        })
        .trim()

    return { text: cleanedText, actions }
}

export default function AiAssistantWidget() {
    const { isAuthenticated } = useAuth()
    const navigate = useNavigate()

    // Lifecycle states: "closed" | "entering" | "open" | "closing"
    const [status, setStatus] = useState("closed")
    const [messages, setMessages] = useState([
        {
            role: "assistant",
            content:
                "Hello! I am **Fleet AI**, your conversational Fleet Operations Copilot. How can I assist you with your fleet today?",
        },
    ])
    const [input, setInput] = useState("")
    const [isLoading, setIsLoading] = useState(false)
    const [error, setError] = useState(null)
    const messagesEndRef = useRef(null)
    const inputRef = useRef(null)
    const panelRef = useRef(null)
    const safetyTimerRef = useRef(null)

    const isPrefersReducedMotion = useCallback(() => {
        return (
            typeof window !== "undefined" &&
            Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches)
        )
    }, [])

    const clearSafetyTimer = useCallback(() => {
        if (safetyTimerRef.current) {
            clearTimeout(safetyTimerRef.current)
            safetyTimerRef.current = null
        }
    }, [])

    // Smooth open animation: mount with entering animation, transition to open
    const handleOpen = useCallback(() => {
        clearSafetyTimer()
        if (isPrefersReducedMotion()) {
            setStatus("open")
            return
        }

        setStatus("entering")
        safetyTimerRef.current = setTimeout(() => {
            setStatus("open")
            safetyTimerRef.current = null
        }, 350)
    }, [clearSafetyTimer, isPrefersReducedMotion])

    // Smooth close animation: transition to closing animation, unmount when done
    const handleClose = useCallback(() => {
        clearSafetyTimer()
        if (isPrefersReducedMotion()) {
            setStatus("closed")
            return
        }

        setStatus("closing")
        safetyTimerRef.current = setTimeout(() => {
            setStatus("closed")
            safetyTimerRef.current = null
        }, 300)
    }, [clearSafetyTimer, isPrefersReducedMotion])

    const toggleOpen = useCallback(() => {
        if (status === "open" || status === "entering") {
            handleClose()
        } else {
            handleOpen()
        }
    }, [status, handleClose, handleOpen])

    const handleAnimationEnd = useCallback(
        (e) => {
            if (e.target !== panelRef.current) return
            clearSafetyTimer()

            if (status === "entering") {
                setStatus("open")
            } else if (status === "closing") {
                setStatus("closed")
            }
        },
        [status, clearSafetyTimer]
    )

    useEffect(() => {
        return () => {
            clearSafetyTimer()
        }
    }, [clearSafetyTimer])

    useEffect(() => {
        const handleTrigger = () => {
            handleOpen()
        }
        window.addEventListener("open-ai-assistant", handleTrigger)
        return () => window.removeEventListener("open-ai-assistant", handleTrigger)
    }, [handleOpen])

    const isMounted = status !== "closed"
    const isOpenedOrOpening = status === "open" || status === "entering"

    useEffect(() => {
        if (isMounted) {
            messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
            const timer = setTimeout(() => {
                inputRef.current?.focus({ preventScroll: true })
            }, 150)
            return () => clearTimeout(timer)
        }
    }, [isMounted, messages, isLoading])

    if (!isAuthenticated) return null

    const handleSend = async (textToSend) => {
        const query = (textToSend || input).trim()
        if (!query || isLoading) return

        setInput("")
        setError(null)

        const userMsg = { role: "user", content: query }
        const updatedMessages = [...messages, userMsg]
        setMessages(updatedMessages)
        setIsLoading(true)

        try {
            const data = await sendAiChatMessage(buildChatHistory(updatedMessages))
            if (data && data.reply) {
                setMessages((prev) => [
                    ...prev,
                    { role: "assistant", content: data.reply },
                ])
            } else {
                throw new Error("Invalid response format from Fleet AI")
            }
        } catch (err) {
            console.error("Fleet AI Error:", err)
            setError(err.message || "Failed to reach Fleet AI assistant.")
        } finally {
            setIsLoading(false)
        }
    }

    const handleKeyDown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault()
            handleSend()
        }
    }

    const handleClearChat = () => {
        setMessages([
            {
                role: "assistant",
                content:
                    "Chat history cleared. How can I assist you with your fleet operations now?",
            },
        ])
        setError(null)
    }

    const handleActionClick = (action) => {
        if (action.type === "map") {
            navigate(action.targetId ? `/tracking?vehicleId=${action.targetId}` : "/tracking")
        } else if (action.type === "vehicle") {
            navigate(action.targetId ? `/tracking?vehicleId=${action.targetId}` : "/dashboard")
        } else if (action.type === "trips") {
            navigate(action.targetId ? `/tracking?vehicleId=${action.targetId}` : "/tracking")
        } else if (action.type === "idle") {
            navigate("/idle-analytics")
        } else if (action.type === "prompt") {
            handleSend(action.prompt)
        }
    }

    const renderMessageContent = (msg) => {
        if (msg.role === "user") {
            return <p className="whitespace-pre-wrap">{msg.content}</p>
        }

        const { text, actions } = extractActionsAndCleanText(msg.content)
        const lines = text.split("\n")

        return (
            <div>
                <div className="space-y-1.5 leading-relaxed">
                    {lines.map((line, idx) => {
                        if (!line.trim()) {
                            return <div key={idx} className="h-1" />
                        }

                        const parts = line.split(/(\*\*.*?\*\*)/g)
                        const formatted = parts.map((part, pIdx) => {
                            if (part.startsWith("**") && part.endsWith("**")) {
                                return (
                                    <strong key={pIdx} className="font-semibold text-slate-900">
                                        {part.slice(2, -2)}
                                    </strong>
                                )
                            }
                            return part
                        })

                        if (line.trim().startsWith("- ") || line.trim().startsWith("* ")) {
                            return (
                                <li key={idx} className="ml-4 list-disc my-0.5">
                                    {formatted}
                                </li>
                            )
                        }

                        return <p key={idx}>{formatted}</p>
                    })}
                </div>

                {/* Contextual Action Buttons */}
                {actions.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2.5 pt-2 border-t border-slate-100">
                        {actions.map((act, aIdx) => (
                            <button
                                key={aIdx}
                                type="button"
                                onClick={() => handleActionClick(act)}
                                className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-lg bg-brand-light hover:bg-brand text-brand hover:text-white border border-brand-border/80 hover:border-brand transition-all duration-150 cursor-pointer select-none shadow-2xs"
                            >
                                {act.type === "map" && <MapPin className="w-3 h-3 shrink-0" />}
                                {act.type === "vehicle" && <ExternalLink className="w-3 h-3 shrink-0" />}
                                {act.type === "trips" && <Navigation className="w-3 h-3 shrink-0" />}
                                {act.type === "idle" && <Clock className="w-3 h-3 shrink-0" />}
                                {act.type === "prompt" && <Sparkles className="w-3 h-3 shrink-0" />}
                                <span>{act.label}</span>
                            </button>
                        ))}
                    </div>
                )}
            </div>
        )
    }

    return (
        <div className="fixed bottom-4 right-4 sm:bottom-6 sm:right-6 z-[9999] font-sans antialiased flex flex-col items-end">
            {/* ─── CHAT PANEL WITH SMOOTH UX ANIMATIONS ────────────────────── */}
            {isMounted && (
                <div
                    ref={panelRef}
                    onAnimationEnd={handleAnimationEnd}
                    role="dialog"
                    aria-label="Fleet Operations Copilot"
                    className={`mb-3 w-[calc(100vw-2rem)] sm:w-[420px] max-w-[420px] h-[560px] max-h-[calc(100dvh-6rem)] bg-white border border-slate-200/90 rounded-2xl shadow-[0_10px_35px_-5px_rgba(15,23,42,0.14),0_20px_48px_-10px_rgba(15,23,42,0.18)] flex flex-col overflow-hidden origin-bottom-right ${
                        status === "entering"
                            ? "ai-assistant-entering"
                            : status === "closing"
                              ? "ai-assistant-closing"
                              : "ai-assistant-open"
                    }`}
                >
                    {/* Header */}
                    <div className="px-4 py-3 bg-gradient-to-r from-[#295F74] to-[#33758F] text-white flex items-center justify-between select-none shrink-0 shadow-xs">
                        <div className="flex items-center gap-2.5 min-w-0">
                            <div className="relative w-8 h-8 rounded-lg bg-white/15 border border-white/20 flex items-center justify-center text-white shrink-0 shadow-xs">
                                <Bot className="w-4.5 h-4.5" />
                                <span className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-emerald-400 ring-2 ring-[#295F74]" />
                            </div>
                            <div className="min-w-0">
                                <h3 className="font-semibold text-sm leading-tight text-white truncate">
                                    Fleet Operations Copilot
                                </h3>
                                <p className="text-[11px] text-white/80 leading-tight truncate">
                                    Live telemetry & fleet assistant
                                </p>
                            </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0">
                            <button
                                type="button"
                                onClick={handleClearChat}
                                title="Clear conversation"
                                aria-label="Clear conversation"
                                className="p-1.5 text-white/80 hover:text-white hover:bg-white/15 rounded-lg transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                            <button
                                type="button"
                                onClick={handleClose}
                                title="Close assistant"
                                aria-label="Close assistant"
                                className="p-1.5 text-white/80 hover:text-white hover:bg-white/15 rounded-lg transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        </div>
                    </div>

                    {/* Messages Container */}
                    <div className="flex-1 overflow-y-auto p-4 space-y-3 text-xs sm:text-[13px] text-slate-800 bg-slate-50/70">
                        {messages.map((msg, idx) => (
                            <div
                                key={idx}
                                className={`flex gap-2.5 ${
                                    msg.role === "user" ? "justify-end" : "justify-start"
                                }`}
                            >
                                {msg.role === "assistant" && (
                                    <div className="w-7 h-7 rounded-lg bg-brand text-white flex items-center justify-center shrink-0 mt-0.5 shadow-2xs">
                                        <Bot className="w-4 h-4" />
                                    </div>
                                )}

                                <div
                                    className={`max-w-[85%] px-3.5 py-2.5 rounded-2xl ${
                                        msg.role === "user"
                                            ? "bg-brand text-white rounded-br-xs shadow-xs"
                                            : "bg-white border border-slate-200/90 text-slate-800 rounded-bl-xs shadow-2xs"
                                    }`}
                                >
                                    {renderMessageContent(msg)}
                                </div>

                                {msg.role === "user" && (
                                    <div className="w-7 h-7 rounded-lg bg-slate-200 text-slate-700 flex items-center justify-center shrink-0 mt-0.5 shadow-2xs">
                                        <User className="w-4 h-4" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {/* Loading Indicator */}
                        {isLoading && (
                            <div className="flex gap-2.5 justify-start">
                                <div className="w-7 h-7 rounded-lg bg-brand text-white flex items-center justify-center shrink-0 mt-0.5 shadow-2xs">
                                    <Bot className="w-4 h-4" />
                                </div>
                                <div className="bg-white border border-slate-200/90 px-3.5 py-2.5 rounded-2xl rounded-bl-xs flex items-center gap-2.5 text-slate-500 shadow-2xs">
                                    <Loader2 className="w-3.5 h-3.5 animate-spin text-brand shrink-0" />
                                    <span className="text-xs font-medium text-slate-600">Analyzing fleet telemetry...</span>
                                </div>
                            </div>
                        )}

                        {/* Error Alert */}
                        {error && (
                            <div className="bg-rose-50 border border-rose-200 text-rose-700 p-3 rounded-xl text-xs flex items-center justify-between gap-2 shadow-2xs">
                                <span className="leading-snug">{error}</span>
                                <button
                                    type="button"
                                    onClick={() => handleSend()}
                                    className="px-2.5 py-1 bg-rose-600 hover:bg-rose-700 text-white text-[11px] rounded-lg font-medium transition-colors flex items-center gap-1 cursor-pointer shrink-0 shadow-2xs active:scale-95"
                                >
                                    <RefreshCw className="w-3 h-3" /> Retry
                                </button>
                            </div>
                        )}

                        <div ref={messagesEndRef} />
                    </div>

                    {/* Quick Prompts */}
                    {messages.length <= 2 && !isLoading && (
                        <div className="px-3.5 py-2.5 border-t border-slate-200/80 bg-white flex flex-col gap-1.5 shrink-0">
                            <div className="flex items-center gap-1 text-[11px] font-medium text-slate-500">
                                <Sparkles className="w-3 h-3 text-brand" />
                                <span>Suggested prompts</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                                {QUICK_PROMPTS.map((prompt, pIdx) => (
                                    <button
                                        key={pIdx}
                                        type="button"
                                        onClick={() => handleSend(prompt)}
                                        className="text-[11px] font-medium bg-slate-50 hover:bg-brand-light border border-slate-200/90 hover:border-brand-border text-slate-700 hover:text-brand px-2.5 py-1 rounded-full transition-all text-left truncate max-w-full cursor-pointer shadow-2xs active:scale-[0.98]"
                                    >
                                        {prompt}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {/* Input Area */}
                    <div className="p-3 bg-white border-t border-slate-200/80 shrink-0">
                        <div className="flex items-end gap-2 bg-slate-50/90 border border-slate-200/90 focus-within:border-brand focus-within:ring-2 focus-within:ring-brand/15 focus-within:bg-white rounded-xl p-1.5 transition-all shadow-2xs">
                            <textarea
                                ref={inputRef}
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={handleKeyDown}
                                placeholder="Ask about idling, vehicles, alerts..."
                                rows={1}
                                disabled={isLoading}
                                className="flex-1 bg-transparent px-2.5 py-1.5 text-xs sm:text-sm text-slate-900 placeholder-slate-400 resize-none outline-none disabled:opacity-50 max-h-24 leading-relaxed"
                            />
                            <button
                                type="button"
                                onClick={() => handleSend()}
                                disabled={!input.trim() || isLoading}
                                title="Send message"
                                aria-label="Send message"
                                className="w-8 h-8 sm:w-9 sm:h-9 bg-brand hover:bg-brand-hover disabled:bg-slate-200 disabled:text-slate-400 text-white rounded-lg flex items-center justify-center shrink-0 transition-all cursor-pointer disabled:cursor-not-allowed active:scale-95 shadow-2xs disabled:shadow-none"
                            >
                                <Send className="w-4 h-4" />
                            </button>
                        </div>
                        <div className="mt-1.5 px-1 flex items-center justify-between text-[10px] text-slate-400 select-none">
                            <span>Press <kbd className="font-mono bg-slate-100 px-1 py-0.5 rounded text-[9px] text-slate-600 border border-slate-200">Enter</kbd> to send</span>
                            <span>ATS Fleet AI</span>
                        </div>
                    </div>
                </div>
            )}

            {/* ─── STABLE TOGGLE BUTTON WITH SMOOTH ICON TRANSITION ────────── */}
            <button
                type="button"
                onClick={toggleOpen}
                title={isOpenedOrOpening ? "Collapse assistant" : "Open Fleet AI Assistant"}
                aria-label={isOpenedOrOpening ? "Collapse assistant" : "Open Fleet AI Assistant"}
                className="relative w-12 h-12 rounded-full bg-brand hover:bg-brand-hover text-white shadow-lg hover:shadow-xl flex items-center justify-center transition-all duration-200 active:scale-95 cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2"
            >
                <Bot
                    className={`w-5 h-5 absolute transition-all duration-200 ease-out ${
                        isOpenedOrOpening ? "opacity-0 scale-75 pointer-events-none" : "opacity-100 scale-100"
                    }`}
                />
                <ChevronDown
                    className={`w-5 h-5 absolute transition-all duration-200 ease-out ${
                        isOpenedOrOpening ? "opacity-100 scale-100" : "opacity-0 scale-75 pointer-events-none"
                    }`}
                />
            </button>
        </div>
    )
}

