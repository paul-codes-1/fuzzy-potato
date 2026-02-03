import { Routes, Route } from 'react-router-dom'
import MeetingList from './components/MeetingList'
import MeetingDetail from './components/MeetingDetail'

function App() {
  return (
    <div className="app">
      <header className="header">
        <div className="container">
          <h1>LFUCG Meeting Archive</h1>
          <p>Lexington-Fayette Urban County Government Meeting Transcripts & Summaries</p>
        </div>
      </header>

      <main>
        <Routes>
          <Route path="/" element={<MeetingList />} />
          <Route path="/meeting/:clipId" element={<MeetingDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
