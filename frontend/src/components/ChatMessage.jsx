import { useNavigate } from 'react-router-dom'
import ChatSourceCards from './ChatSourceCards'
import { linkifyCitations, handleCitationClick } from '../utils/citations'

function simpleMarkdown(text) {
  if (!text) return ''
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br>')
    .replace(/^/, '<p>')
    .replace(/$/, '</p>')
}

export default function ChatMessage({ message }) {
  const navigate = useNavigate()
  const isUser = message.role === 'user'
  const time = new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

  return (
    <div className={`chat-message ${isUser ? 'user' : 'assistant'}`}>
      {!isUser && message.model && (
        <span className={`chat-model-badge ${message.model === 'gpt-4o' ? 'openai' : 'anthropic'}`}>
          {message.model === 'gpt-4o' ? 'GPT-4o' : 'Claude Sonnet'}
        </span>
      )}
      <div className="chat-message-content">
        {isUser ? (
          <p>{message.content}</p>
        ) : (
          <div
            onClick={e => handleCitationClick(e, navigate)}
            dangerouslySetInnerHTML={{
              __html: linkifyCitations(simpleMarkdown(message.content), message.sources || []),
            }}
          />
        )}
      </div>
      <span className="chat-message-time">{time}</span>
      {!isUser && message.sources && message.sources.length > 0 && (
        <ChatSourceCards sources={message.sources} />
      )}
    </div>
  )
}