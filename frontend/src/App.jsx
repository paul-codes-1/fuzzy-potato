import { Routes, Route, Link } from 'react-router-dom'
import MeetingList from './components/MeetingList'
import MeetingDetail from './components/MeetingDetail'
import AskQuestion from './components/AskQuestion'
import { WhatsNewPage } from './components/MeetingDiff'
import ChatMeetings from './components/ChatMeetings'
import AdminDashboard from './components/AdminDashboard'
import AnalyticsDashboard from './components/AnalyticsDashboard'
import VoteTracker from './components/VoteTracker'
import DigestSubscribe from './components/DigestSubscribe'
import Highlights from './components/Highlights'
import Onboarding from './components/Onboarding'
import ReportBuilder from './components/ReportBuilder'
import UsageDashboard from './components/UsageDashboard'
import CustomerHealth from './components/CustomerHealth'
import SentimentDashboard from './components/SentimentDashboard'
import MeetingCalendar from './components/MeetingCalendar'
import Changelog from './components/Changelog'
import DemoWalkthrough from './components/DemoWalkthrough'
import LanguageSwitcher from './components/LanguageSwitcher'
import GlobalSearch from './components/GlobalSearch'
import SavedSearches from './components/SavedSearches'
import ErrorBoundary from './components/ErrorBoundary'
import NotFound from './components/NotFound'
import { AnnounceProvider } from './components/A11yAnnouncer'
import { I18nProvider } from './i18n/I18nProvider'
import { useI18n } from './i18n/I18nProvider'
import { useBranding } from './contexts/BrandingContext'

function AppInner() {
  const { branding } = useBranding()
  const { t } = useI18n()

  return (
    <AnnounceProvider>
      <ErrorBoundary>
      <div className="app">
        {/* Skip to main content link for keyboard users */}
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>

        <header className="header" role="banner">
          <div className="container">
            <div className="header-top">
              <h1>
                {branding.logo_url && (
                  <img
                    src={branding.logo_url}
                    alt={branding.display_name}
                    className="header-logo"
                    style={{ height: '2rem', marginRight: '0.5rem', verticalAlign: 'middle' }}
                  />
                )}
                {branding.display_name || t('header.defaultTitle')}
              </h1>
              <nav className="header-nav" aria-label="Main navigation">
                <Link to="/" className="header-link">{t('nav.browse')}</Link>
                <Link to="/chat" className="header-link">{t('nav.chat')}</Link>
                <Link to="/ask" className="header-link">{t('nav.askQuestion')}</Link>
                <Link to="/votes" className="header-link">{t('nav.votes')}</Link>
                <Link to="/analytics" className="header-link">{t('nav.analytics')}</Link>
                {sessionStorage.getItem('admin_api_key') && (
                  <>
                    <Link to="/admin" className="header-link">{t('nav.admin')}</Link>
                    <Link to="/admin/health" className="header-link">Health</Link>
                  </>
                )}
                <Link to="/digest" className="header-link">Digest</Link>
                <Link to="/highlights" className="header-link">Highlights</Link>
                <Link to="/reports" className="header-link">Reports</Link>
                <Link to="/usage" className="header-link">Usage</Link>
                <Link to="/sentiment" className="header-link">Sentiment</Link>
                <Link to="/calendar" className="header-link">Calendar</Link>
                <Link to="/changelog" className="header-link">Changelog</Link>
                <Link to="/whats-new" className="header-link">What's New</Link>
                <Link to="/saved-searches" className="header-link">Saved</Link>
                <Link to="/demo" className="header-link">Product Tour</Link>
                <Link to="/onboarding" className="header-cta">{t('nav.getStarted')}</Link>
                <LanguageSwitcher />
              </nav>
            </div>
            <p>{branding.welcome_message || t('header.defaultSubtitle')}</p>
            <GlobalSearch />
          </div>
        </header>

        <main id="main-content" tabIndex="-1">
          <Routes>
            <Route path="/" element={<MeetingList />} />
            <Route path="/meeting/:clipId" element={<MeetingDetail />} />
            <Route path="/chat" element={<ChatMeetings />} />
            <Route path="/ask" element={<AskQuestion />} />
            <Route path="/votes" element={<VoteTracker />} />
            <Route path="/analytics" element={<AnalyticsDashboard />} />
            <Route path="/admin" element={<AdminDashboard />} />
            <Route path="/admin/health" element={<CustomerHealth />} />
            <Route path="/digest" element={<DigestSubscribe />} />
            <Route path="/highlights" element={<Highlights />} />
            <Route path="/whats-new" element={<WhatsNewPage />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/reports" element={<ReportBuilder />} />
            <Route path="/usage" element={<UsageDashboard />} />
            <Route path="/sentiment" element={<SentimentDashboard />} />
            <Route path="/calendar" element={<MeetingCalendar />} />
            <Route path="/changelog" element={<Changelog />} />
            <Route path="/saved-searches" element={<SavedSearches />} />
            <Route path="/demo" element={<DemoWalkthrough />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>

        <footer className="site-footer" role="contentinfo">
          {branding.footer_text ? (
            <span>{branding.footer_text}</span>
          ) : (
            <a
              href="https://github.com/paul-codes-1/fuzzy-potato/"
              target="_blank"
              rel="noopener noreferrer"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
              </svg>
              {t('footer.openSource')}
            </a>
          )}
          {branding.support_email && (
            <span className="footer-support">
              {' '}| {t('footer.support')}: <a href={`mailto:${branding.support_email}`}>{branding.support_email}</a>
            </span>
          )}
        </footer>
      </div>
      </ErrorBoundary>
    </AnnounceProvider>
  )
}

function App() {
  return (
    <I18nProvider>
      <AppInner />
    </I18nProvider>
  )
}

export default App
