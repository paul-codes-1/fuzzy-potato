import { Link } from 'react-router-dom'

function NotFound() {
  return (
    <div className="not-found">
      <div className="not-found-content">
        <div className="not-found-code">404</div>
        <h1 className="not-found-title">Page not found</h1>
        <p className="not-found-message">
          The page you're looking for doesn't exist or may have been moved.
          Try searching for what you need, or head back to the meeting archive.
        </p>
        <div className="not-found-suggestions">
          <h2 className="not-found-suggestions-title">Try these instead</h2>
          <ul className="not-found-suggestions-list">
            <li>
              <Link to="/">Browse all meetings</Link>
            </li>
            <li>
              <Link to="/ask">Ask a question about meetings</Link>
            </li>
            <li>
              <Link to="/votes">View vote tracker</Link>
            </li>
            <li>
              <Link to="/chat">Chat with meeting archive</Link>
            </li>
          </ul>
        </div>
        <div className="not-found-actions">
          <Link to="/" className="not-found-btn-primary">
            Go to home page
          </Link>
        </div>
      </div>
    </div>
  )
}

export default NotFound
