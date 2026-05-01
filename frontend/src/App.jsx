import { useState, useEffect } from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import { useSearchIndex } from './contexts/SearchContext'
import MeetingList from './components/MeetingList'
import MeetingDetail from './components/MeetingDetail'
import AskQuestion from './components/AskQuestion'
import ChatLFUCG from './components/ChatLFUCG'
import { About, Methodology, Corrections } from './components/StaticPages'

const LOADING_MESSAGES = [
  'Loading meeting archive...',
  'Building search index...',
  'Indexing transcripts and agendas...',
  'Preparing full-text search...',
  'Tip: Use quotes for exact phrase matching, e.g. "short-term rentals"',
  'Tip: Filter by meeting body to narrow results',
  'Tip: Click timestamps in summaries to jump to that point in the video',
  'Tip: The Overview tab shows extracted votes, financials, and public comments',
  'Tip: Try the Chat feature for multi-turn conversations about meetings',
  'Tip: Each meeting has agenda, minutes, transcript, and AI summary tabs',
  'Processing meeting data...',
  'Almost ready...',
]

function FullScreenLoading() {
  const [messageIndex, setMessageIndex] = useState(0)

  useEffect(() => {
    const interval = setInterval(() => {
      setMessageIndex(prev => (prev + 1) % LOADING_MESSAGES.length)
    }, 3000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div className="fullscreen-loading">
      <div className="fullscreen-loading-content">
        <div className="loading-spinner" />
        <h2 className="loading-title">LFUCG Meeting Archive</h2>
        <p className="loading-message" key={messageIndex}>
          {LOADING_MESSAGES[messageIndex]}
        </p>
      </div>
    </div>
  )
}

function App() {
  const { isLoaded: flexSearchLoaded } = useSearchIndex()

  if (!flexSearchLoaded) {
    return <FullScreenLoading />
  }

  return (
    <div className="app">
      <header className="header">
        <div className="container">
          <div className="header-top">
            <h1>LFUCG Meeting Archive</h1>
            <nav className="header-nav">
              <Link to="/" className="header-link">Browse</Link>
              <Link to="/chat" className="header-link">Chat</Link>
              <Link to="/ask" className="header-link">Ask a Question</Link>
            </nav>
          </div>
          <p>Lexington-Fayette Urban County Government Meeting Transcripts & Summaries</p>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<MeetingList />} />
          <Route path="/meeting/:clipId" element={<MeetingDetail />} />
          <Route path="/chat" element={<ChatLFUCG />} />
          <Route path="/ask" element={<AskQuestion />} />
          <Route path="/about" element={<About />} />
          <Route path="/about/methodology" element={<Methodology />} />
          <Route path="/corrections" element={<Corrections />} />
        </Routes>
      </main>

      <footer className="site-footer">
        <div className="container">
          <nav className="site-footer-nav" aria-label="Site information">
            <Link to="/about">About</Link>
            <Link to="/about/methodology">Methodology</Link>
            <Link to="/corrections">Corrections</Link>
            <a href="/llms.txt">llms.txt</a>
            <a href="/sitemap_index.xml">Sitemap</a>
          </nav>
          <p className="site-footer-disclosure">
            Operated by Paul Oliva as a civic-tech side project. Transcripts and
            summaries are auto-generated from public LFUCG Granicus video; verify
            against the official video and minutes for high-stakes use.
          </p>
        </div>
      </footer>
    </div>
  )
}

export default App
