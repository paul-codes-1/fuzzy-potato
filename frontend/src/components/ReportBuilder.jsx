import { useState, useRef, useCallback } from 'react'
import { useBranding } from '../hooks/useBranding'

const API_BASE = import.meta.env.VITE_API_BASE || ''

const REPORT_TYPES = [
  {
    id: 'meeting',
    title: 'Meeting Summary',
    description: 'Complete report for a single meeting with votes, financials, attendance, and public comments.',
    icon: '\u{1F4CB}',
    fields: ['clip_id'],
  },
  {
    id: 'member',
    title: 'Council Member Report Card',
    description: 'Voting record, attendance rate, motions made, and financial positions for one council member.',
    icon: '\u{1F464}',
    fields: ['name', 'start', 'end'],
  },
  {
    id: 'financial',
    title: 'Financial Summary',
    description: 'All financial items for a date range with totals by category and full line-item detail.',
    icon: '\u{1F4B0}',
    fields: ['start', 'end'],
  },
  {
    id: 'tracking',
    title: 'Policy Tracking',
    description: 'Alert matches, vote outcomes, and context for your tracked policy topics and keywords.',
    icon: '\u{1F514}',
    fields: ['alert_ids'],
  },
  {
    id: 'digest',
    title: 'Monthly / Quarterly Digest',
    description: 'Summary of all meetings in a period with vote counts, financial totals, and key topics.',
    icon: '\u{1F4C5}',
    fields: ['period', 'month'],
  },
]

function getApiKey() {
  return sessionStorage.getItem('api_key') || ''
}

function buildReportUrl(type, params) {
  const key = getApiKey()
  const base = `${API_BASE}/api/v1/reports`

  switch (type) {
    case 'meeting':
      return `${base}/meeting/${encodeURIComponent(params.clip_id)}?_key=${key}`
    case 'member': {
      const qs = new URLSearchParams()
      if (params.start) qs.set('start', params.start)
      if (params.end) qs.set('end', params.end)
      return `${base}/member/${encodeURIComponent(params.name)}?${qs.toString()}`
    }
    case 'financial': {
      const qs = new URLSearchParams()
      if (params.start) qs.set('start', params.start)
      if (params.end) qs.set('end', params.end)
      return `${base}/financial?${qs.toString()}`
    }
    case 'tracking':
      return `${base}/tracking?alert_ids=${encodeURIComponent(params.alert_ids)}`
    case 'digest': {
      const qs = new URLSearchParams()
      qs.set('period', params.period || 'monthly')
      qs.set('month', params.month)
      return `${base}/digest?${qs.toString()}`
    }
    default:
      return ''
  }
}

function ReportCard({ report, selected, onSelect }) {
  return (
    <button
      className={`rb-card ${selected ? 'rb-card-selected' : ''}`}
      onClick={() => onSelect(report.id)}
      aria-pressed={selected}
      type="button"
    >
      <span className="rb-card-icon" aria-hidden="true">{report.icon}</span>
      <div className="rb-card-content">
        <h3 className="rb-card-title">{report.title}</h3>
        <p className="rb-card-desc">{report.description}</p>
      </div>
    </button>
  )
}

function ConfigForm({ reportType, params, onChange }) {
  const report = REPORT_TYPES.find((r) => r.id === reportType)
  if (!report) return null

  const fields = report.fields

  return (
    <div className="rb-form">
      <h3 className="rb-form-title">Configure: {report.title}</h3>

      {fields.includes('clip_id') && (
        <label className="rb-field">
          <span className="rb-label">Meeting Clip ID</span>
          <input
            type="text"
            value={params.clip_id || ''}
            onChange={(e) => onChange({ ...params, clip_id: e.target.value })}
            placeholder="e.g. 6669"
            className="rb-input"
          />
        </label>
      )}

      {fields.includes('name') && (
        <label className="rb-field">
          <span className="rb-label">Council Member Name</span>
          <input
            type="text"
            value={params.name || ''}
            onChange={(e) => onChange({ ...params, name: e.target.value })}
            placeholder="e.g. Brown"
            className="rb-input"
          />
        </label>
      )}

      {fields.includes('start') && (
        <label className="rb-field">
          <span className="rb-label">Start Date</span>
          <input
            type="date"
            value={params.start || ''}
            onChange={(e) => onChange({ ...params, start: e.target.value })}
            className="rb-input"
          />
        </label>
      )}

      {fields.includes('end') && (
        <label className="rb-field">
          <span className="rb-label">End Date</span>
          <input
            type="date"
            value={params.end || ''}
            onChange={(e) => onChange({ ...params, end: e.target.value })}
            className="rb-input"
          />
        </label>
      )}

      {fields.includes('alert_ids') && (
        <label className="rb-field">
          <span className="rb-label">Alert IDs (comma-separated)</span>
          <input
            type="text"
            value={params.alert_ids || ''}
            onChange={(e) => onChange({ ...params, alert_ids: e.target.value })}
            placeholder="e.g. abc-123, def-456"
            className="rb-input"
          />
        </label>
      )}

      {fields.includes('period') && (
        <label className="rb-field">
          <span className="rb-label">Period</span>
          <select
            value={params.period || 'monthly'}
            onChange={(e) => onChange({ ...params, period: e.target.value })}
            className="rb-input"
          >
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
          </select>
        </label>
      )}

      {fields.includes('month') && (
        <label className="rb-field">
          <span className="rb-label">Month (YYYY-MM)</span>
          <input
            type="month"
            value={params.month || ''}
            onChange={(e) => onChange({ ...params, month: e.target.value })}
            className="rb-input"
          />
        </label>
      )}
    </div>
  )
}

function isFormValid(reportType, params) {
  switch (reportType) {
    case 'meeting':
      return !!params.clip_id?.trim()
    case 'member':
      return !!params.name?.trim()
    case 'financial':
      return true
    case 'tracking':
      return !!params.alert_ids?.trim()
    case 'digest':
      return !!params.month?.trim()
    default:
      return false
  }
}

export default function ReportBuilder() {
  const { branding } = useBranding()
  const [selectedType, setSelectedType] = useState(null)
  const [params, setParams] = useState({})
  const [previewUrl, setPreviewUrl] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const iframeRef = useRef(null)

  const handleSelect = useCallback((typeId) => {
    setSelectedType(typeId)
    setParams({})
    setPreviewUrl(null)
    setError(null)
  }, [])

  const handleGenerate = useCallback(async () => {
    if (!selectedType || !isFormValid(selectedType, params)) return

    setLoading(true)
    setError(null)

    try {
      const url = buildReportUrl(selectedType, params)
      const apiKey = getApiKey()

      // Fetch the HTML with auth header, then load into iframe via blob URL
      const response = await fetch(url, {
        headers: {
          'X-API-Key': apiKey,
          'Accept': 'text/html',
        },
      })

      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Server error ${response.status}: ${text.slice(0, 200)}`)
      }

      const html = await response.text()
      const blob = new Blob([html], { type: 'text/html' })
      const blobUrl = URL.createObjectURL(blob)

      // Revoke previous blob URL
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl)
      }

      setPreviewUrl(blobUrl)
    } catch (err) {
      setError(err.message || 'Failed to generate report')
    } finally {
      setLoading(false)
    }
  }, [selectedType, params, previewUrl])

  const handlePrint = useCallback(() => {
    if (iframeRef.current?.contentWindow) {
      iframeRef.current.contentWindow.print()
    }
  }, [])

  const valid = selectedType && isFormValid(selectedType, params)

  return (
    <div className="container rb-container">
      <div className="rb-header">
        <h2>Report Builder</h2>
        <p>
          Generate printable PDF reports for distribution to council members,
          administrators, and advocacy groups. Select a report type, configure
          parameters, then preview and print.
        </p>
      </div>

      {/* Report type cards */}
      <div className="rb-grid">
        {REPORT_TYPES.map((report) => (
          <ReportCard
            key={report.id}
            report={report}
            selected={selectedType === report.id}
            onSelect={handleSelect}
          />
        ))}
      </div>

      {/* Configuration form */}
      {selectedType && (
        <ConfigForm
          reportType={selectedType}
          params={params}
          onChange={setParams}
        />
      )}

      {/* Action buttons */}
      {selectedType && (
        <div className="rb-actions">
          <button
            className="rb-btn rb-btn-primary"
            onClick={handleGenerate}
            disabled={!valid || loading}
          >
            {loading ? 'Generating...' : 'Generate Report'}
          </button>
          {previewUrl && (
            <button className="rb-btn rb-btn-secondary" onClick={handlePrint}>
              Print / Save PDF
            </button>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rb-error" role="alert">
          {error}
        </div>
      )}

      {/* Preview iframe */}
      {previewUrl && (
        <div className="rb-preview">
          <div className="rb-preview-toolbar">
            <span className="rb-preview-label">Report Preview</span>
            <button className="rb-btn rb-btn-secondary rb-btn-sm" onClick={handlePrint}>
              Print / Save PDF
            </button>
          </div>
          <iframe
            ref={iframeRef}
            src={previewUrl}
            className="rb-iframe"
            title="Report Preview"
          />
        </div>
      )}
    </div>
  )
}
