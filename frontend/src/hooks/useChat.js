import { useState, useCallback, useRef } from 'react'

export function useChat() {
  const [messages, setMessages] = useState([])
  const [filters, setFilters] = useState({
    meeting_body: '',
    date_after: '',
    date_before: '',
  })
  const [modelProvider, setModelProvider] = useState('openai')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Use a ref to always have the current messages available in callbacks
  const messagesRef = useRef(messages)
  messagesRef.current = messages

  const sendMessage = useCallback(async (content) => {
    const userMessage = { role: 'user', content, timestamp: new Date() }

    setMessages(prev => [...prev, userMessage])
    setLoading(true)
    setError(null)

    try {
      const body = {
        messages: [...messagesRef.current, { role: 'user', content }].map(m => ({
          role: m.role,
          content: m.content,
        })),
        model_provider: modelProvider,
      }

      if (filters.meeting_body) body.meeting_body = filters.meeting_body
      if (filters.date_after) body.date_after = filters.date_after
      if (filters.date_before) body.date_before = filters.date_before

      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        throw new Error(`Server error: ${response.status}`)
      }

      const data = await response.json()

      const assistantMessage = {
        role: 'assistant',
        content: data.content,
        sources: data.sources || [],
        timestamp: new Date(),
        model: data.model_used,
      }

      setMessages(prev => [...prev, assistantMessage])
    } catch (err) {
      setError(err.message || 'Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [filters, modelProvider])

  const clearChat = useCallback(() => {
    setMessages([])
    setError(null)
  }, [])

  const retry = useCallback(() => {
    const currentMessages = messagesRef.current
    const lastUserMsg = [...currentMessages].reverse().find(m => m.role === 'user')
    if (!lastUserMsg) return

    // Remove the last user message (sendMessage will re-add it)
    const lastUserIndex = currentMessages.lastIndexOf(lastUserMsg)
    setMessages(prev => prev.slice(0, lastUserIndex))
    setError(null)
    setTimeout(() => sendMessage(lastUserMsg.content), 0)
  }, [sendMessage])

  return {
    messages,
    filters,
    modelProvider,
    loading,
    error,
    sendMessage,
    setModelProvider,
    setFilters,
    clearChat,
    retry,
  }
}
