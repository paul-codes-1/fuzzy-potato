import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import {
  CivicLensClient,
  CivicLensError,
  AuthenticationError,
  RateLimitError,
  NotFoundError,
} from '../index.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Create a mock fetch that returns the given status and body.
 * Tracks calls for assertion.
 */
function mockFetch(status = 200, body = {}, headers = {}) {
  const calls = [];
  const fn = async (url, opts) => {
    calls.push({ url, opts });
    return {
      ok: status >= 200 && status < 300,
      status,
      headers: new Headers(headers),
      json: async () => body,
      text: async () => JSON.stringify(body),
      arrayBuffer: async () => new ArrayBuffer(0),
    };
  };
  fn.calls = calls;
  return fn;
}

/** Shortcut: build a client with a mock fetch. */
function makeClient(fetchFn, overrides = {}) {
  return new CivicLensClient({
    apiKey: 'test_key_123',
    baseUrl: 'https://test.civiclens.ai',
    maxRetries: 1, // disable retries in most tests for speed
    fetchFn,
    ...overrides,
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('CivicLensClient', () => {
  // -- Construction --------------------------------------------------------

  describe('constructor', () => {
    it('throws when apiKey is missing', () => {
      assert.throws(
        () => new CivicLensClient({ apiKey: '' }),
        /apiKey is required/,
      );
    });

    it('throws when apiKey is undefined', () => {
      assert.throws(
        () => new CivicLensClient({}),
        /apiKey is required/,
      );
    });

    it('uses default baseUrl when none provided', () => {
      const fetch = mockFetch();
      const client = new CivicLensClient({ apiKey: 'k', fetchFn: fetch });
      assert.equal(client.baseUrl, 'https://api.civiclens.ai');
    });

    it('strips trailing slashes from baseUrl', () => {
      const fetch = mockFetch();
      const client = new CivicLensClient({
        apiKey: 'k',
        baseUrl: 'https://example.com///',
        fetchFn: fetch,
      });
      assert.equal(client.baseUrl, 'https://example.com');
    });

    it('stores provided options', () => {
      const fetch = mockFetch();
      const client = new CivicLensClient({
        apiKey: 'my_key',
        baseUrl: 'https://custom.api',
        timeout: 5000,
        maxRetries: 5,
        backoffBase: 500,
        fetchFn: fetch,
      });
      assert.equal(client.apiKey, 'my_key');
      assert.equal(client.baseUrl, 'https://custom.api');
      assert.equal(client.timeout, 5000);
      assert.equal(client.maxRetries, 5);
      assert.equal(client.backoffBase, 500);
    });
  });

  // -- Headers -------------------------------------------------------------

  describe('request headers', () => {
    it('sends X-API-Key header on every request', async () => {
      const fetch = mockFetch(200, { status: 'ok' });
      const client = makeClient(fetch);
      await client.health();

      assert.equal(fetch.calls.length, 1);
      assert.equal(fetch.calls[0].opts.headers['X-API-Key'], 'test_key_123');
    });

    it('sends Content-Type and User-Agent headers', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.health();

      const h = fetch.calls[0].opts.headers;
      assert.equal(h['Content-Type'], 'application/json');
      assert.equal(h['User-Agent'], 'civiclens-js/1.0.0');
    });
  });

  // -- ask() ---------------------------------------------------------------

  describe('ask()', () => {
    it('POSTs to /api/v1/ask with question in body', async () => {
      const fetch = mockFetch(200, { answer: 'yes' });
      const client = makeClient(fetch);
      const result = await client.ask('What about parks?');

      assert.equal(fetch.calls.length, 1);
      assert.equal(fetch.calls[0].url, 'https://test.civiclens.ai/api/v1/ask');
      assert.equal(fetch.calls[0].opts.method, 'POST');

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.question, 'What about parks?');
      assert.deepEqual(result, { answer: 'yes' });
    });

    it('includes optional filters in the body', async () => {
      const fetch = mockFetch(200, { answer: 'filtered' });
      const client = makeClient(fetch);
      await client.ask('budget', {
        meetingBody: 'Council',
        dateAfter: '2024-01-01',
        dateBefore: '2024-12-31',
      });

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.question, 'budget');
      assert.equal(body.meeting_body, 'Council');
      assert.equal(body.date_after, '2024-01-01');
      assert.equal(body.date_before, '2024-12-31');
    });

    it('omits undefined filter fields from body', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.ask('test');

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.ok(!('meeting_body' in body));
      assert.ok(!('date_after' in body));
      assert.ok(!('date_before' in body));
    });
  });

  // -- chat() --------------------------------------------------------------

  describe('chat()', () => {
    it('POSTs to /api/v1/chat with messages and model_provider', async () => {
      const messages = [{ role: 'user', content: 'Hello' }];
      const fetch = mockFetch(200, { reply: 'Hi' });
      const client = makeClient(fetch);
      const result = await client.chat(messages);

      assert.equal(fetch.calls[0].url, 'https://test.civiclens.ai/api/v1/chat');
      assert.equal(fetch.calls[0].opts.method, 'POST');

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.deepEqual(body.messages, messages);
      assert.equal(body.model_provider, 'openai'); // default
      assert.deepEqual(result, { reply: 'Hi' });
    });

    it('allows overriding modelProvider to anthropic', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.chat([{ role: 'user', content: 'hi' }], {
        modelProvider: 'anthropic',
      });

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.model_provider, 'anthropic');
    });

    it('includes optional filters', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.chat([{ role: 'user', content: 'q' }], {
        meetingBody: 'Planning',
        dateAfter: '2025-01-01',
        dateBefore: '2025-06-30',
      });

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.meeting_body, 'Planning');
      assert.equal(body.date_after, '2025-01-01');
      assert.equal(body.date_before, '2025-06-30');
    });
  });

  // -- search (searchVotes, searchFinancial) --------------------------------

  describe('searchVotes()', () => {
    it('GETs /api/v1/votes with query params', async () => {
      const fetch = mockFetch(200, { votes: [] });
      const client = makeClient(fetch);
      const result = await client.searchVotes({ keyword: 'zoning', limit: 10 });

      const url = new URL(fetch.calls[0].url);
      assert.equal(url.pathname, '/api/v1/votes');
      assert.equal(url.searchParams.get('keyword'), 'zoning');
      assert.equal(url.searchParams.get('limit'), '10');
      assert.equal(fetch.calls[0].opts.method, 'GET');
      assert.deepEqual(result, { votes: [] });
    });
  });

  describe('searchFinancial()', () => {
    it('GETs /api/v1/financial with query params', async () => {
      const fetch = mockFetch(200, { items: [] });
      const client = makeClient(fetch);
      await client.searchFinancial({ minAmount: 1000, keyword: 'grant' });

      const url = new URL(fetch.calls[0].url);
      assert.equal(url.pathname, '/api/v1/financial');
      assert.equal(url.searchParams.get('min_amount'), '1000');
      assert.equal(url.searchParams.get('keyword'), 'grant');
    });
  });

  // -- listMeetings / getMeeting (via votes/financial as proxy) -------------
  // The SDK does not have listMeetings/getMeeting directly, so we test
  // representative GET methods: getVoteStats and getMemberVotingRecord.

  describe('getVoteStats()', () => {
    it('GETs /api/v1/votes/stats', async () => {
      const fetch = mockFetch(200, { total: 42 });
      const client = makeClient(fetch);
      const result = await client.getVoteStats();

      assert.equal(fetch.calls[0].url, 'https://test.civiclens.ai/api/v1/votes/stats');
      assert.equal(fetch.calls[0].opts.method, 'GET');
      assert.deepEqual(result, { total: 42 });
    });
  });

  describe('getMemberVotingRecord()', () => {
    it('URL-encodes the member name', async () => {
      const fetch = mockFetch(200, { record: [] });
      const client = makeClient(fetch);
      await client.getMemberVotingRecord('Jane O\'Connor');

      const url = fetch.calls[0].url;
      assert.ok(url.includes(encodeURIComponent("Jane O'Connor")));
    });
  });

  // -- Error handling ------------------------------------------------------

  describe('error handling', () => {
    it('throws AuthenticationError on 401', async () => {
      const fetch = mockFetch(401, { detail: 'Invalid key' });
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.health(),
        (err) => {
          assert.ok(err instanceof AuthenticationError);
          assert.equal(err.statusCode, 401);
          assert.match(err.message, /Authentication failed/);
          return true;
        },
      );
    });

    it('throws AuthenticationError on 403', async () => {
      const fetch = mockFetch(403, { detail: 'Forbidden' });
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.health(),
        (err) => {
          assert.ok(err instanceof AuthenticationError);
          assert.equal(err.statusCode, 403);
          return true;
        },
      );
    });

    it('throws NotFoundError on 404', async () => {
      const fetch = mockFetch(404, { detail: 'Not found' });
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.getVoteStats(),
        (err) => {
          assert.ok(err instanceof NotFoundError);
          assert.equal(err.statusCode, 404);
          return true;
        },
      );
    });

    it('throws RateLimitError on 429', async () => {
      const fetch = mockFetch(429, { detail: 'Too many requests' }, {
        'Retry-After': '30',
      });
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.ask('test'),
        (err) => {
          assert.ok(err instanceof RateLimitError);
          assert.equal(err.statusCode, 429);
          assert.equal(err.retryAfter, 30);
          return true;
        },
      );
    });

    it('throws CivicLensError on other non-ok status', async () => {
      const fetch = mockFetch(422, { detail: 'Validation error' });
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.ask('x'),
        (err) => {
          assert.ok(err instanceof CivicLensError);
          assert.equal(err.statusCode, 422);
          assert.match(err.message, /API error 422/);
          return true;
        },
      );
    });

    it('throws CivicLensError when fetch itself rejects', async () => {
      const fetch = async () => { throw new TypeError('fetch failed'); };
      fetch.calls = [];
      const client = makeClient(fetch);

      await assert.rejects(
        () => client.health(),
        (err) => {
          assert.ok(err instanceof CivicLensError);
          assert.match(err.message, /Request failed/);
          return true;
        },
      );
    });
  });

  // -- Retry behavior ------------------------------------------------------

  describe('retry behavior', () => {
    it('retries on 500 and succeeds on second attempt', async () => {
      let callCount = 0;
      const fetch = async (url, opts) => {
        callCount++;
        if (callCount === 1) {
          return {
            ok: false,
            status: 500,
            headers: new Headers(),
            json: async () => ({ detail: 'Server error' }),
            text: async () => 'Server error',
          };
        }
        return {
          ok: true,
          status: 200,
          headers: new Headers(),
          json: async () => ({ answer: 'retried' }),
          text: async () => '{}',
        };
      };
      fetch.calls = [];

      const client = new CivicLensClient({
        apiKey: 'k',
        baseUrl: 'https://test.civiclens.ai',
        maxRetries: 2,
        backoffBase: 10, // fast for tests
        fetchFn: fetch,
      });

      const result = await client.ask('test');
      assert.equal(callCount, 2);
      assert.deepEqual(result, { answer: 'retried' });
    });
  });

  // -- Rate limit info -----------------------------------------------------

  describe('rate limit tracking', () => {
    it('parses rate limit headers into lastRateLimit', async () => {
      const fetch = mockFetch(200, {}, {
        'X-RateLimit-Limit': '1000',
        'X-RateLimit-Remaining': '999',
        'Retry-After': '0',
      });
      const client = makeClient(fetch);
      await client.health();

      assert.equal(client.lastRateLimit.limit, 1000);
      assert.equal(client.lastRateLimit.remaining, 999);
      assert.equal(client.lastRateLimit.retryAfter, 0);
    });
  });

  // -- POST body serialization for various methods -------------------------

  describe('webhook registration', () => {
    it('POSTs url and events to /api/v1/webhooks', async () => {
      const fetch = mockFetch(200, { id: 'wh_1' });
      const client = makeClient(fetch);
      await client.registerWebhook('https://example.com/hook', ['meeting.processed']);

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.url, 'https://example.com/hook');
      assert.deepEqual(body.events, ['meeting.processed']);
    });
  });

  describe('export', () => {
    it('POSTs format to /api/v1/export', async () => {
      const fetch = mockFetch(200, { job_id: 'j1' });
      const client = makeClient(fetch);
      await client.startExport({ format: 'csv' });

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.format, 'csv');
    });
  });

  describe('branding', () => {
    it('converts camelCase keys to snake_case', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.updateBranding({
        displayName: 'My City',
        primaryColor: '#ff0000',
      });

      const body = JSON.parse(fetch.calls[0].opts.body);
      assert.equal(body.display_name, 'My City');
      assert.equal(body.primary_color, '#ff0000');
    });
  });

  // -- Query params --------------------------------------------------------

  describe('query parameter handling', () => {
    it('omits null/undefined params from URL', async () => {
      const fetch = mockFetch(200, {});
      const client = makeClient(fetch);
      await client.getUsage({ period: '7d' });

      const url = new URL(fetch.calls[0].url);
      assert.equal(url.searchParams.get('period'), '7d');
      // No extra params
      assert.equal([...url.searchParams.keys()].length, 1);
    });
  });
});
