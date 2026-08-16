import { Routes, Route, Link } from 'react-router-dom'
import MeetingList from './components/MeetingList'
import MeetingDetail from './components/MeetingDetail'
import AskQuestion from './components/AskQuestion'
import ChatLFUCG from './components/ChatLFUCG'
import { About, Methodology, Corrections } from './components/StaticPages'
import RouteChangeTracker from './components/RouteChangeTracker'
import AdSlot from './components/AdSlot'
import { getSiteConfig } from './config/site'

function App() {
  const site = getSiteConfig()
  // Document-driven cities (CivicClerk minutes) have no video/transcript, so
  // the footer source clause differs from video (Granicus/YouTube) cities.
  const sourceClause =
    site.source?.kind === 'document'
      ? `Summaries and structured facts are auto-generated from the official ${site.source?.platform || ''} agenda & minutes documents; verify against the source documents for high-stakes use.`
      : `Transcripts and summaries are auto-generated from public ${site.jurisdiction_short_name} ${site.source?.platform || ''} video; verify against the official video and minutes for high-stakes use.`
  return (
    <div className="app">
      <RouteChangeTracker />
      <header className="header">
        <div className="container">
          <div className="header-top">
            <h1>{site.archive_name}</h1>
            <nav className="header-nav">
              <Link to="/" className="header-link">Browse</Link>
              <Link to="/chat" className="header-link">Chat</Link>
              <Link to="/ask" className="header-link">Ask a Question</Link>
            </nav>
          </div>
          <p>{site.tagline}</p>
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
        <div className="container">
          <AdSlot slot="3748486934" />
        </div>
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
            {site.operator_bio} {sourceClause}
          </p>
        </div>
      </footer>
    </div>
  )
}

export default App
