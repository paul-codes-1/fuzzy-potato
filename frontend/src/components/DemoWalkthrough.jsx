import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useI18n } from '../i18n/I18nProvider'

const STEPS = [
  {
    title: 'Browse Meetings',
    description:
      'Search and filter your entire meeting archive. Every council session, committee hearing, and board meeting — organized by date, body, and topic with full-text search.',
    color: 'var(--blue-700)',
    icon: '\u{1F4CB}',
    preview: 'Meeting cards with dates, topics, and duration estimates',
    route: '/',
    routeLabel: 'Browse meetings',
  },
  {
    title: 'Meeting Detail',
    description:
      'Dive into any meeting with AI-extracted votes, financial items, attendance, and public comments. Timestamped links jump straight to the relevant moment in the video.',
    color: 'var(--blue-600)',
    icon: '\u{1F3DB}\uFE0F',
    preview: 'Tabbed view: Overview, Transcript, Agenda, Minutes',
    route: '/',
    routeLabel: 'View a meeting',
  },
  {
    title: 'Ask a Question',
    description:
      'Get instant, sourced answers from your meeting archive. Ask about budgets, ordinances, or any topic — CivicLens retrieves relevant passages and synthesizes a clear response with citations.',
    color: '#2d6a4f',
    icon: '\u{2753}',
    preview: 'Question box with filters for body, date range, and topic',
    route: '/ask',
    routeLabel: 'Try Ask',
  },
  {
    title: 'Conversational Chat',
    description:
      'Have a back-and-forth conversation grounded in your meeting data. Follow up on answers, refine questions, and explore topics naturally — powered by OpenAI or Anthropic models.',
    color: '#7b2d8b',
    icon: '\u{1F4AC}',
    preview: 'Multi-turn chat with model selector and citation links',
    route: '/chat',
    routeLabel: 'Try Chat',
  },
  {
    title: 'Vote Tracker',
    description:
      'Track every recorded vote across your jurisdiction. See who voted for what, filter by outcome or council member, and set up alerts for topics you care about.',
    color: '#b45309',
    icon: '\u{1F5F3}\uFE0F',
    preview: 'Vote records with roll call details and policy alerts',
    route: '/votes',
    routeLabel: 'View votes',
  },
  {
    title: 'Get Started',
    description:
      'Ready to bring CivicLens to your city? Onboard in minutes — connect your Granicus account, configure your jurisdiction, and start processing meetings automatically.',
    color: 'var(--blue-900)',
    icon: '\u{1F680}',
    preview: 'Self-service onboarding with Granicus connection wizard',
    route: '/onboarding',
    routeLabel: 'Start onboarding',
  },
]

export default function DemoWalkthrough() {
  const [current, setCurrent] = useState(0)
  const { t } = useI18n()
  const step = STEPS[current]
  const isLast = current === STEPS.length - 1

  const goNext = useCallback(() => {
    setCurrent((c) => Math.min(c + 1, STEPS.length - 1))
  }, [])

  const goPrev = useCallback(() => {
    setCurrent((c) => Math.max(c - 1, 0))
  }, [])

  useEffect(() => {
    const handler = (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
        e.preventDefault()
        goNext()
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
        e.preventDefault()
        goPrev()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [goNext, goPrev])

  return (
    <div className="container demo-walkthrough">
      <div className="demo-header">
        <div className="demo-header-top">
          <h2 className="demo-title">Product Tour</h2>
          <Link to="/" className="demo-skip">
            Skip tour
          </Link>
        </div>
        <p className="demo-subtitle">
          See how CivicLens turns meeting archives into searchable, actionable knowledge.
          Use arrow keys or the buttons below to navigate.
        </p>
      </div>

      {/* Step indicator dots */}
      <div className="demo-dots" role="tablist" aria-label="Tour steps">
        {STEPS.map((s, i) => (
          <button
            key={i}
            role="tab"
            aria-selected={i === current}
            aria-label={`Step ${i + 1}: ${s.title}`}
            className={`demo-dot ${i === current ? 'demo-dot--active' : ''}`}
            onClick={() => setCurrent(i)}
          />
        ))}
      </div>

      {/* Step content */}
      <div className="demo-step" role="tabpanel" aria-label={step.title}>
        <div className="demo-step-number">
          Step {current + 1} of {STEPS.length}
        </div>

        <div className="demo-step-content">
          <div className="demo-preview" style={{ backgroundColor: step.color }}>
            <span className="demo-preview-icon">{step.icon}</span>
            <span className="demo-preview-text">{step.preview}</span>
          </div>

          <div className="demo-step-info">
            <h3 className="demo-step-title">{step.title}</h3>
            <p className="demo-step-desc">{step.description}</p>
            <Link to={step.route} className="demo-try-link">
              {step.routeLabel} &rarr;
            </Link>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <div className="demo-nav">
        <button
          className="demo-nav-btn"
          onClick={goPrev}
          disabled={current === 0}
          aria-label="Previous step"
        >
          &larr; Previous
        </button>

        {isLast ? (
          <Link to="/onboarding" className="demo-nav-btn demo-nav-btn--cta">
            Get Started
          </Link>
        ) : (
          <button
            className="demo-nav-btn demo-nav-btn--primary"
            onClick={goNext}
            aria-label="Next step"
          >
            Next &rarr;
          </button>
        )}
      </div>
    </div>
  )
}
