/**
 * CivicLens JavaScript/Node SDK
 *
 * Promise-based client for the CivicLens API with automatic retry,
 * rate-limit awareness, and full method coverage.
 *
 * Works in Node.js 18+ (native fetch) and modern browsers.
 * Compatible with both ESM and CJS (see package.json exports).
 *
 * @example
 * import { CivicLensClient } from '@civiclens/sdk';
 *
 * const cl = new CivicLensClient({ apiKey: 'cl_...' });
 * const result = await cl.ask('What has the city done about short-term rentals?');
 * console.log(result.answer);
 */

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

class CivicLensError extends Error {
  /**
   * @param {string} message
   * @param {number|null} statusCode
   * @param {*} body
   */
  constructor(message, statusCode = null, body = null) {
    super(message);
    this.name = 'CivicLensError';
    this.statusCode = statusCode;
    this.body = body;
  }
}

class AuthenticationError extends CivicLensError {
  constructor(message, statusCode, body) {
    super(message, statusCode, body);
    this.name = 'AuthenticationError';
  }
}

class RateLimitError extends CivicLensError {
  /**
   * @param {string} message
   * @param {number|null} retryAfter  Seconds to wait before retrying.
   * @param {number|null} statusCode
   * @param {*} body
   */
  constructor(message, retryAfter = null, statusCode = null, body = null) {
    super(message, statusCode, body);
    this.name = 'RateLimitError';
    this.retryAfter = retryAfter;
  }
}

class NotFoundError extends CivicLensError {
  constructor(message, statusCode, body) {
    super(message, statusCode, body);
    this.name = 'NotFoundError';
  }
}

// ---------------------------------------------------------------------------
// Rate-limit header parser
// ---------------------------------------------------------------------------

class RateLimitInfo {
  /**
   * @param {number|null} limit
   * @param {number|null} remaining
   * @param {number|null} retryAfter
   */
  constructor(limit, remaining, retryAfter) {
    this.limit = limit;
    this.remaining = remaining;
    this.retryAfter = retryAfter;
  }

  /**
   * Parse rate-limit info from response headers.
   * @param {Headers} headers
   * @returns {RateLimitInfo}
   */
  static fromHeaders(headers) {
    const toInt = (key) => {
      const v = headers.get(key);
      if (v !== null) { const n = parseInt(v, 10); if (!isNaN(n)) return n; }
      return null;
    };
    const toFloat = (key) => {
      const v = headers.get(key);
      if (v !== null) { const n = parseFloat(v); if (!isNaN(n)) return n; }
      return null;
    };
    return new RateLimitInfo(
      toInt('X-RateLimit-Limit'),
      toInt('X-RateLimit-Remaining'),
      toFloat('Retry-After'),
    );
  }
}

// ---------------------------------------------------------------------------
// Retryable status codes
// ---------------------------------------------------------------------------

const RETRYABLE = new Set([429, 500, 502, 503, 504]);

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

class CivicLensClient {
  /**
   * @param {Object} options
   * @param {string}  options.apiKey       Tenant API key.
   * @param {string}  [options.baseUrl]    API base URL (default: https://api.civiclens.ai).
   * @param {number}  [options.timeout]    Request timeout in ms (default: 60000).
   * @param {number}  [options.maxRetries] Max retries on transient failures (default: 3).
   * @param {number}  [options.backoffBase] Backoff base in ms (default: 1000).
   * @param {typeof globalThis.fetch} [options.fetchFn] Custom fetch implementation.
   */
  constructor({
    apiKey,
    baseUrl = 'https://api.civiclens.ai',
    timeout = 60000,
    maxRetries = 3,
    backoffBase = 1000,
    fetchFn,
  }) {
    if (!apiKey) throw new Error('apiKey is required');
    this.apiKey = apiKey;
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.timeout = timeout;
    this.maxRetries = maxRetries;
    this.backoffBase = backoffBase;
    this._fetch = fetchFn || globalThis.fetch;
    /** @type {RateLimitInfo|null} */
    this.lastRateLimit = null;
  }

  // -- Transport ----------------------------------------------------------

  /**
   * @param {string} method
   * @param {string} path
   * @param {Object} [options]
   * @param {Object} [options.body]
   * @param {Object} [options.params]
   * @returns {Promise<Response>}
   */
  async _request(method, path, { body, params } = {}) {
    let url = `${this.baseUrl}${path}`;

    // Append query params
    if (params) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null) qs.append(k, String(v));
      }
      const qsStr = qs.toString();
      if (qsStr) url += `?${qsStr}`;
    }

    const headers = {
      'X-API-Key': this.apiKey,
      'Content-Type': 'application/json',
      'User-Agent': 'civiclens-js/1.0.0',
    };

    let lastError = null;
    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeout);

      try {
        const response = await this._fetch(url, {
          method,
          headers,
          body: body ? JSON.stringify(body) : undefined,
          signal: controller.signal,
        });
        clearTimeout(timer);

        // Parse rate-limit headers
        this.lastRateLimit = RateLimitInfo.fromHeaders(response.headers);

        if (response.ok) return response;

        // Retry on transient errors
        if (RETRYABLE.has(response.status) && attempt < this.maxRetries) {
          let wait = this.backoffBase * Math.pow(2, attempt - 1);
          if (response.status === 429 && this.lastRateLimit.retryAfter) {
            wait = Math.max(wait, this.lastRateLimit.retryAfter * 1000);
          }
          await this._sleep(wait);
          continue;
        }

        // Non-retryable error
        await this._throwForStatus(response);
      } catch (err) {
        clearTimeout(timer);
        if (err instanceof CivicLensError) throw err;
        lastError = err;
        if (attempt < this.maxRetries) {
          await this._sleep(this.backoffBase * Math.pow(2, attempt - 1));
          continue;
        }
        throw new CivicLensError(`Request failed after ${this.maxRetries} retries: ${err.message}`);
      }
    }

    throw new CivicLensError('Request failed unexpectedly');
  }

  /**
   * @param {Response} response
   */
  async _throwForStatus(response) {
    let body;
    try { body = await response.json(); } catch { body = await response.text(); }
    const detail = (body && typeof body === 'object' && body.detail) ? body.detail : String(body);
    const status = response.status;

    if (status === 429) {
      throw new RateLimitError(
        `Rate limit exceeded: ${detail}`,
        this.lastRateLimit ? this.lastRateLimit.retryAfter : null,
        status,
        body,
      );
    }
    if (status === 401 || status === 403) {
      throw new AuthenticationError(`Authentication failed: ${detail}`, status, body);
    }
    if (status === 404) {
      throw new NotFoundError(`Not found: ${detail}`, status, body);
    }
    throw new CivicLensError(`API error ${status}: ${detail}`, status, body);
  }

  /** @param {number} ms */
  _sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

  async _get(path, params) {
    const resp = await this._request('GET', path, { params });
    return resp.json();
  }

  async _post(path, body) {
    const resp = await this._request('POST', path, { body });
    return resp.json();
  }

  async _put(path, body) {
    const resp = await this._request('PUT', path, { body });
    return resp.json();
  }

  async _patch(path, body) {
    const resp = await this._request('PATCH', path, { body });
    return resp.json();
  }

  async _delete(path) {
    const resp = await this._request('DELETE', path);
    return resp.json();
  }

  // -----------------------------------------------------------------------
  // Q&A
  // -----------------------------------------------------------------------

  /**
   * Ask a single question about meeting archives.
   * @param {string} question
   * @param {Object} [options]
   * @param {string} [options.meetingBody]
   * @param {string} [options.dateAfter]   YYYY-MM-DD
   * @param {string} [options.dateBefore]  YYYY-MM-DD
   * @returns {Promise<Object>}
   */
  async ask(question, { meetingBody, dateAfter, dateBefore } = {}) {
    const body = { question };
    if (meetingBody) body.meeting_body = meetingBody;
    if (dateAfter) body.date_after = dateAfter;
    if (dateBefore) body.date_before = dateBefore;
    return this._post('/api/v1/ask', body);
  }

  /**
   * Multi-turn conversational Q&A.
   * @param {Array<{role: string, content: string}>} messages
   * @param {Object} [options]
   * @param {string} [options.meetingBody]
   * @param {string} [options.dateAfter]
   * @param {string} [options.dateBefore]
   * @param {string} [options.modelProvider]  "openai" or "anthropic"
   * @returns {Promise<Object>}
   */
  async chat(messages, { meetingBody, dateAfter, dateBefore, modelProvider = 'openai' } = {}) {
    const body = { messages, model_provider: modelProvider };
    if (meetingBody) body.meeting_body = meetingBody;
    if (dateAfter) body.date_after = dateAfter;
    if (dateBefore) body.date_before = dateBefore;
    return this._post('/api/v1/chat', body);
  }

  // -----------------------------------------------------------------------
  // Votes & Financial
  // -----------------------------------------------------------------------

  /**
   * Search extracted votes.
   * @param {Object} [options]
   * @param {string} [options.member]
   * @param {string} [options.dateAfter]
   * @param {string} [options.dateBefore]
   * @param {string} [options.outcome]  "passed" or "failed"
   * @param {string} [options.keyword]
   * @param {number} [options.limit]
   * @param {number} [options.offset]
   * @returns {Promise<Object>}
   */
  async searchVotes({ member, dateAfter, dateBefore, outcome, keyword, limit = 50, offset = 0 } = {}) {
    return this._get('/api/v1/votes', {
      member: member || '',
      date_after: dateAfter || '',
      date_before: dateBefore || '',
      outcome: outcome || '',
      keyword: keyword || '',
      limit,
      offset,
    });
  }

  /**
   * Get a council member's full voting record.
   * @param {string} name
   * @returns {Promise<Object>}
   */
  async getMemberVotingRecord(name) {
    return this._get(`/api/v1/votes/member/${encodeURIComponent(name)}`);
  }

  /** @returns {Promise<Object>} */
  async getVoteStats() { return this._get('/api/v1/votes/stats'); }

  /**
   * Search financial items.
   * @param {Object} [options]
   * @param {number} [options.minAmount]
   * @param {number} [options.maxAmount]
   * @param {string} [options.itemType]
   * @param {string} [options.dateAfter]
   * @param {string} [options.dateBefore]
   * @param {string} [options.keyword]
   * @param {number} [options.limit]
   * @param {number} [options.offset]
   * @returns {Promise<Object>}
   */
  async searchFinancial({ minAmount, maxAmount, itemType, dateAfter, dateBefore, keyword, limit = 50, offset = 0 } = {}) {
    return this._get('/api/v1/financial', {
      min_amount: minAmount,
      max_amount: maxAmount,
      item_type: itemType || '',
      date_after: dateAfter || '',
      date_before: dateBefore || '',
      keyword: keyword || '',
      limit,
      offset,
    });
  }

  /** @returns {Promise<Object>} */
  async getFinancialSummary() { return this._get('/api/v1/financial/summary'); }

  // -----------------------------------------------------------------------
  // Alerts
  // -----------------------------------------------------------------------

  /**
   * Create a policy monitoring alert.
   * @param {string} name
   * @param {string} type  e.g. "keyword", "member", "financial_threshold"
   * @param {Object} config
   * @returns {Promise<Object>}
   */
  async createAlert(name, type, config) {
    return this._post('/api/v1/alerts', { name, type, config });
  }

  /** @returns {Promise<Object>} */
  async listAlerts() { return this._get('/api/v1/alerts'); }

  /**
   * Update an alert.
   * @param {string} alertId
   * @param {Object} [updates]  { name?, config?, enabled? }
   * @returns {Promise<Object>}
   */
  async updateAlert(alertId, updates = {}) {
    return this._put(`/api/v1/alerts/${alertId}`, updates);
  }

  /**
   * Delete an alert.
   * @param {string} alertId
   * @returns {Promise<Object>}
   */
  async deleteAlert(alertId) { return this._delete(`/api/v1/alerts/${alertId}`); }

  /**
   * Get matches for an alert.
   * @param {string} alertId
   * @param {Object} [options]
   * @param {number} [options.limit]
   * @param {number} [options.offset]
   * @returns {Promise<Object>}
   */
  async getAlertMatches(alertId, { limit = 50, offset = 0 } = {}) {
    return this._get(`/api/v1/alerts/${alertId}/matches`, { limit, offset });
  }

  // -----------------------------------------------------------------------
  // Export & FOIA
  // -----------------------------------------------------------------------

  /**
   * Start an async data export job.
   * @param {Object} [options]
   * @param {string} [options.format]  "json", "csv", or "zip"
   * @param {Object} [options.filters]
   * @returns {Promise<Object>}
   */
  async startExport({ format = 'json', filters } = {}) {
    const body = { format };
    if (filters) body.filters = filters;
    return this._post('/api/v1/export', body);
  }

  /**
   * Check the status of an export job.
   * @param {string} jobId
   * @returns {Promise<Object>}
   */
  async getExportStatus(jobId) { return this._get(`/api/v1/export/${jobId}`); }

  /**
   * List past export jobs.
   * @param {Object} [options]
   * @param {number} [options.limit]
   * @returns {Promise<Object>}
   */
  async getExportHistory({ limit = 50 } = {}) { return this._get('/api/v1/export/history', { limit }); }

  /**
   * Download a completed export as an ArrayBuffer.
   * @param {string} jobId
   * @returns {Promise<ArrayBuffer>}
   */
  async downloadExport(jobId) {
    const resp = await this._request('GET', `/api/v1/export/${jobId}/download`);
    return resp.arrayBuffer();
  }

  /**
   * Submit a FOIA-style search.
   * @param {string} query  Max 5000 characters.
   * @returns {Promise<Object>}
   */
  async foiaSearch(query) { return this._post('/api/v1/foia', { query }); }

  /**
   * Check FOIA request status.
   * @param {string} requestId
   * @returns {Promise<Object>}
   */
  async getFoiaStatus(requestId) { return this._get(`/api/v1/foia/${requestId}`); }

  // -----------------------------------------------------------------------
  // Analytics
  // -----------------------------------------------------------------------

  /**
   * Get usage summary.
   * @param {Object} [options]
   * @param {string} [options.period]  e.g. "30d", "7d", "1m"
   * @returns {Promise<Object>}
   */
  async getUsage({ period = '30d' } = {}) { return this._get('/api/v1/analytics/usage', { period }); }

  /**
   * Get query volume over time.
   * @param {Object} [options]
   * @param {string} [options.period]
   * @returns {Promise<Object>}
   */
  async getQueryVolume({ period = '7d' } = {}) { return this._get('/api/v1/analytics/queries', { period }); }

  /**
   * Get popular topics and meeting bodies.
   * @param {Object} [options]
   * @param {string} [options.period]
   * @param {number} [options.limit]
   * @returns {Promise<Object>}
   */
  async getPopularTopics({ period = '30d', limit = 10 } = {}) {
    return this._get('/api/v1/analytics/popular-topics', { period, limit });
  }

  /**
   * Get recent queries.
   * @param {Object} [options]
   * @param {number} [options.limit]
   * @returns {Promise<Object>}
   */
  async getRecentQueries({ limit = 50 } = {}) { return this._get('/api/v1/analytics/recent', { limit }); }

  /**
   * Get peak usage hours (UTC).
   * @param {Object} [options]
   * @param {string} [options.period]
   * @returns {Promise<Object>}
   */
  async getPeakHours({ period = '30d' } = {}) { return this._get('/api/v1/analytics/peak-hours', { period }); }

  // -----------------------------------------------------------------------
  // Audit
  // -----------------------------------------------------------------------

  /**
   * Search the audit log.
   * @param {Object} [options]
   * @returns {Promise<Object>}
   */
  async searchAuditLog({ action, resourceType, dateAfter, dateBefore, limit = 50, offset = 0 } = {}) {
    return this._get('/api/v1/audit', {
      action,
      resource_type: resourceType,
      date_after: dateAfter,
      date_before: dateBefore,
      limit,
      offset,
    });
  }

  /**
   * Get audit statistics.
   * @param {Object} [options]
   * @param {number} [options.days]
   * @returns {Promise<Object>}
   */
  async getAuditStats({ days = 30 } = {}) { return this._get('/api/v1/audit/stats', { days }); }

  /** Verify audit hash chain integrity. @returns {Promise<Object>} */
  async verifyAuditIntegrity() { return this._get('/api/v1/audit/verify'); }

  // -----------------------------------------------------------------------
  // Billing
  // -----------------------------------------------------------------------

  /**
   * Create a Stripe Checkout session.
   * @param {Object} options
   * @param {string} options.successUrl
   * @param {string} options.cancelUrl
   * @returns {Promise<Object>}
   */
  async createCheckout({ successUrl, cancelUrl }) {
    return this._post('/api/v1/billing/checkout', { success_url: successUrl, cancel_url: cancelUrl });
  }

  /**
   * Create a Stripe Billing Portal session.
   * @param {Object} options
   * @param {string} options.returnUrl
   * @returns {Promise<Object>}
   */
  async createBillingPortal({ returnUrl }) {
    return this._post('/api/v1/billing/portal', { return_url: returnUrl });
  }

  /** @returns {Promise<Object>} */
  async getBillingUsage() { return this._get('/api/v1/billing/usage'); }

  // -----------------------------------------------------------------------
  // Webhooks
  // -----------------------------------------------------------------------

  /**
   * Register a webhook.
   * @param {string} url     HTTPS callback URL.
   * @param {string[]} events  Event types to subscribe to.
   * @param {Object} [options]
   * @param {string} [options.secret]
   * @returns {Promise<Object>}
   */
  async registerWebhook(url, events, { secret } = {}) {
    const body = { url, events };
    if (secret) body.secret = secret;
    return this._post('/api/v1/webhooks', body);
  }

  /** @returns {Promise<Object>} */
  async listWebhooks() { return this._get('/api/v1/webhooks'); }

  /** @param {string} webhookId @returns {Promise<Object>} */
  async deleteWebhook(webhookId) { return this._delete(`/api/v1/webhooks/${webhookId}`); }

  /**
   * Get webhook delivery log.
   * @param {string} webhookId
   * @param {Object} [options]
   * @param {number} [options.limit]
   * @returns {Promise<Object>}
   */
  async getWebhookDeliveries(webhookId, { limit = 50 } = {}) {
    return this._get(`/api/v1/webhooks/${webhookId}/deliveries`, { limit });
  }

  /** @param {string} webhookId @returns {Promise<Object>} */
  async testWebhook(webhookId) { return this._post(`/api/v1/webhooks/${webhookId}/test`); }

  // -----------------------------------------------------------------------
  // Scheduler
  // -----------------------------------------------------------------------

  /** @returns {Promise<Object>} */
  async getSchedule() { return this._get('/api/v1/schedule'); }

  /**
   * Update meeting ingestion schedule.
   * @param {Object} [options]
   * @param {string} [options.cronExpression]  5-field cron
   * @param {number} [options.maxClips]        1-100
   * @param {boolean} [options.enabled]
   * @returns {Promise<Object>}
   */
  async updateSchedule({ cronExpression, maxClips, enabled } = {}) {
    const body = {};
    if (cronExpression !== undefined) body.cron_expression = cronExpression;
    if (maxClips !== undefined) body.max_clips = maxClips;
    if (enabled !== undefined) body.enabled = enabled;
    return this._put('/api/v1/schedule', body);
  }

  /**
   * Get schedule execution history.
   * @param {Object} [options]
   * @param {number} [options.limit]
   * @returns {Promise<Object>}
   */
  async getScheduleHistory({ limit = 20 } = {}) { return this._get('/api/v1/schedule/history', { limit }); }

  /** Trigger immediate processing run. @returns {Promise<Object>} */
  async triggerRunNow() { return this._post('/api/v1/schedule/run-now'); }

  // -----------------------------------------------------------------------
  // Branding
  // -----------------------------------------------------------------------

  /** @returns {Promise<Object>} */
  async getBranding() { return this._get('/api/v1/branding'); }

  /**
   * Update branding.
   * @param {Object} fields  { displayName, logoUrl, primaryColor, ... }
   * @returns {Promise<Object>}
   */
  async updateBranding(fields) {
    // Convert camelCase keys to snake_case for the API
    const body = {};
    const map = {
      displayName: 'display_name', logoUrl: 'logo_url',
      primaryColor: 'primary_color', secondaryColor: 'secondary_color',
      accentColor: 'accent_color', faviconUrl: 'favicon_url',
      customCss: 'custom_css', welcomeMessage: 'welcome_message',
      footerText: 'footer_text', supportEmail: 'support_email',
    };
    for (const [js, api] of Object.entries(map)) {
      if (fields[js] !== undefined) body[api] = fields[js];
    }
    // Also accept snake_case directly
    for (const [k, v] of Object.entries(fields)) {
      if (!(k in map) && v !== undefined) body[k] = v;
    }
    return this._put('/api/v1/branding', body);
  }

  /** @returns {Promise<Object>} */
  async resetBranding() { return this._post('/api/v1/branding/reset'); }

  // -----------------------------------------------------------------------
  // Status
  // -----------------------------------------------------------------------

  /** @returns {Promise<Object>} */
  async getStatus() { return this._get('/api/v1/status'); }

  /**
   * Get incident history.
   * @param {Object} [options]
   * @param {number} [options.days]
   * @returns {Promise<Object>}
   */
  async getIncidentHistory({ days = 90 } = {}) { return this._get('/api/v1/status/history', { days }); }

  /** @returns {Promise<Object>} */
  async getUptime() { return this._get('/api/v1/status/uptime'); }

  // -----------------------------------------------------------------------
  // Health
  // -----------------------------------------------------------------------

  /** Lightweight health check (no auth required). @returns {Promise<Object>} */
  async health() { return this._get('/health'); }

  /** Authenticated health check. @returns {Promise<Object>} */
  async healthAuthenticated() { return this._get('/api/v1/health'); }
}

// ---------------------------------------------------------------------------
// Exports (ESM + CJS compatible)
// ---------------------------------------------------------------------------

export {
  CivicLensClient,
  CivicLensError,
  AuthenticationError,
  RateLimitError,
  NotFoundError,
  RateLimitInfo,
};

export default CivicLensClient;
