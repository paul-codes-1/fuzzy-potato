import { Routes, Route, Link } from 'react-router-dom'
import MeetingList from './components/MeetingList'
import MeetingDetail from './components/MeetingDetail'
import AskQuestion from './components/AskQuestion'
import ChatLFUCG from './components/ChatLFUCG'
import { About, Methodology, Corrections } from './components/StaticPages'

function App() {
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
            <a href="/skill.md">skill.md</a>
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
