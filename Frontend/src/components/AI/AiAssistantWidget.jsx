import { useState, useRef, useEffect } from "react"
import { useNavigate } from "react-router-dom"
import {
    Bot,
    X,
    Send,
    Trash2,
    Minus,
    Loader2,
    RefreshCw,
    User,
    MapPin,
    ExternalLink,
    Navigation,
    Clock,
    Sparkles,
} from "lucide-react"
import { useAuth } from "../../context/useAuth"
import { sendAiChatMessage } from "../../api/fleetApi"

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

    const [isOpen, setIsOpen] = useState(false)
    const [isVisible, setIsVisible] = useState(false)
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

    // Smooth open animation: mount first, then animate transform/opacity
    const handleOpen = () => {
        setIsVisible(true)
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                setIsOpen(true)
            })
        })
    }

    // Smooth close animation: animate out first, then unmount from DOM
    const handleClose = () => {
        setIsOpen(false)
        setTimeout(() => {
            setIsVisible(false)
        }, 220)
    }

    const toggleOpen = () => {
        if (isOpen) {
            handleClose()
        } else {
            handleOpen()
        }
    }

    useEffect(() => {
        const handleTrigger = () => {
            handleOpen()
        }
        window.addEventListener("open-ai-assistant", handleTrigger)
        return () => window.removeEventListener("open-ai-assistant", handleTrigger)
    }, [])

    useEffect(() => {
        if (isOpen) {
            messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
            setTimeout(() => inputRef.current?.focus(), 150)
        }
    }, [isOpen, messages, isLoading])

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
            const apiMessages = updatedMessages.map((m) => ({
                role: m.role,
                content: m.content,
            }))

            const data = await sendAiChatMessage(apiMessages)
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
                                className="inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-full bg-slate-50 hover:bg-[#1C5E7A] text-[#1C5E7A] hover:text-white border border-[#1C5E7A]/25 transition-all duration-150 cursor-pointer shadow-xs select-none"
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
        <div className="fixed bottom-6 right-6 z-[9999] font-sans antialiased">
            {/* ─── CHAT PANEL WITH SMOOTH UX ANIMATIONS ────────────────────── */}
            {isVisible && (
                <div
                    className={`mb-3 w-[360px] sm:w-[410px] h-[550px] max-h-[calc(100vh-100px)] bg-white border border-slate-200 rounded-2xl shadow-xl flex flex-col overflow-hidden origin-bottom-right transition-all duration-[220ms] ease-out ${
                        isOpen
                            ? "opacity-100 translate-y-0 scale-100 pointer-events-auto"
                            : "opacity-0 translate-y-2 scale-[0.96] pointer-events-none"
                    }`}
                >
                    {/* Header */}
                    <div className="px-4 py-3 bg-[#1C5E7A] text-white flex items-center justify-between select-none shrink-0">
                        <div className="flex items-center gap-2.5">
                            <div className="w-8 h-8 rounded-lg bg-white/15 flex items-center justify-center text-white">
                                <Bot className="w-4.5 h-4.5" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-sm leading-tight text-white">
                                    Fleet Operations Copilot
                                </h3>
                                <p className="text-[11px] text-white/80">
                                    Live telemetry & fleet assistant
                                </p>
                            </div>
                        </div>

                        <div className="flex items-center gap-1">
                            <button
                                onClick={handleClearChat}
                                title="Clear conversation"
                                className="p-1.5 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors cursor-pointer"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                            <button
                                onClick={handleClose}
                                title="Close assistant"
                                className="p-1.5 text-white/80 hover:text-white hover:bg-white/10 rounded-lg transition-colors cursor-pointer"
                            >
                                <Minus className="w-4 h-4" />
                            </button>
                        </div>
                    </div>

                    {/* Messages Container */}
                    <div className="flex-1 overflow-y-auto p-4 space-y-3 text-xs sm:text-sm text-slate-800 bg-slate-50/60">
                        {messages.map((msg, idx) => (
                            <div
                                key={idx}
                                className={`flex gap-2.5 ${
                                    msg.role === "user" ? "justify-end" : "justify-start"
                                }`}
                            >
                                {msg.role === "assistant" && (
                                    <div className="w-7 h-7 rounded-lg bg-[#1C5E7A] text-white flex items-center justify-center flex-shrink-0 mt-0.5">
                                        <Bot className="w-4 h-4" />
                                    </div>
                                )}

                                <div
                                    className={`max-w-[85%] px-3.5 py-2.5 rounded-2xl ${
                                        msg.role === "user"
                                            ? "bg-[#1C5E7A] text-white rounded-br-xs"
                                            : "bg-white border border-slate-200 text-slate-800 rounded-bl-xs shadow-xs"
                                    }`}
                                >
                                    {renderMessageContent(msg)}
                                </div>

                                {msg.role === "user" && (
                                    <div className="w-7 h-7 rounded-lg bg-slate-200 text-slate-700 flex items-center justify-center flex-shrink-0 mt-0.5">
                                        <User className="w-4 h-4" />
                                    </div>
                                )}
                            </div>
                        ))}

                        {/* Loading Indicator */}
                        {isLoading && (
                            <div className="flex gap-2.5 justify-start">
                                <div className="w-7 h-7 rounded-lg bg-[#1C5E7A] text-white flex items-center justify-center flex-shrink-0 mt-0.5">
                                    <Bot className="w-4 h-4" />
                                </div>
                                <div className="bg-white border border-slate-200 px-3.5 py-2.5 rounded-2xl rounded-bl-xs flex items-center gap-2 text-slate-500 shadow-xs">
                                    <Loader2 className="w-4 h-4 animate-spin text-[#1C5E7A]" />
                                    <span className="text-xs">Checking fleet telemetry...</span>
                                </div>
                            </div>
                        )}

                        {/* Error Alert */}
                        {error && (
                            <div className="bg-rose-50 border border-rose-200 text-rose-700 p-3 rounded-xl text-xs flex items-center justify-between gap-2">
                                <span>{error}</span>
                                <button
                                    onClick={() => handleSend()}
                                    className="px-2 py-1 bg-rose-600 hover:bg-rose-700 text-white text-[11px] rounded font-medium transition-colors flex items-center gap-1 cursor-pointer"
                                >
                                    <RefreshCw className="w-3 h-3" /> Retry
                                </button>
                            </div>
                        )}

                        <div ref={messagesEndRef} />
                    </div>

                    {/* Quick Prompts */}
                    {messages.length <= 2 && !isLoading && (
                        <div className="px-3 py-2 border-t border-slate-200 bg-white flex flex-wrap gap-1.5 shrink-0">
                            {QUICK_PROMPTS.map((prompt, pIdx) => (
                                <button
                                    key={pIdx}
                                    onClick={() => handleSend(prompt)}
                                    className="text-[11px] bg-slate-100 hover:bg-[#1C5E7A]/10 hover:border-[#1C5E7A]/40 border border-slate-200 text-slate-700 hover:text-[#1C5E7A] px-2.5 py-1 rounded-full transition-all text-left truncate max-w-full cursor-pointer"
                                >
                                    {prompt}
                                </button>
                            ))}
                        </div>
                    )}

                    {/* Input Area */}
                    <div className="p-3 bg-white border-t border-slate-200 flex items-center gap-2 shrink-0">
                        <textarea
                            ref={inputRef}
                            value={input}
                            onChange={(e) => setInput(e.target.value)}
                            onKeyDown={handleKeyDown}
                            placeholder="Ask about idling, vehicles, alerts..."
                            rows={1}
                            disabled={isLoading}
                            className="flex-1 bg-slate-50 border border-slate-200 focus:border-[#1C5E7A] focus:ring-1 focus:ring-[#1C5E7A] rounded-xl px-3.5 py-2 text-xs sm:text-sm text-slate-900 placeholder-slate-400 resize-none outline-none transition-all disabled:opacity-50 max-h-24"
                        />
                        <button
                            onClick={() => handleSend()}
                            disabled={!input.trim() || isLoading}
                            className="w-9 h-9 bg-[#1C5E7A] hover:bg-[#164b62] disabled:bg-slate-200 disabled:text-slate-400 text-white rounded-xl flex items-center justify-center flex-shrink-0 transition-colors shadow-xs cursor-pointer"
                        >
                            <Send className="w-4 h-4" />
                        </button>
                    </div>
                </div>
            )}

            {/* ─── STABLE TOGGLE BUTTON WITH SUBTLE ICON CROSS-FADE ────────── */}
            <button
                type="button"
                onClick={toggleOpen}
                title={isOpen ? "Close Assistant" : "Open Fleet AI Assistant"}
                className="relative w-12 h-12 rounded-full bg-[#1C5E7A] hover:bg-[#164b62] text-white shadow-lg flex items-center justify-center transition-colors duration-150 active:scale-95 cursor-pointer select-none"
            >
                <Bot
                    className={`w-5 h-5 absolute transition-all duration-200 ease-out ${
                        isOpen ? "opacity-0 scale-75 pointer-events-none" : "opacity-100 scale-100"
                    }`}
                />
                <X
                    className={`w-5 h-5 absolute transition-all duration-200 ease-out ${
                        isOpen ? "opacity-100 scale-100" : "opacity-0 scale-75 pointer-events-none"
                    }`}
                />
            </button>
        </div>
    )
}
