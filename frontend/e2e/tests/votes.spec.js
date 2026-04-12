import { test, expect } from '@playwright/test'
import { installMockRoutes } from '../fixtures/mock-data.js'

const VOTES_RESPONSE = {
  votes: [
    {
      clip_id: 7001,
      meeting_date: '2026-01-15',
      identifier: 'Ordinance 0016-26',
      description: 'Zoning change from Agricultural-Rural to Medium Density Residential',
      motion_by: 'Brown',
      second_by: 'Curtis',
      outcome: 'passed',
      ayes: 8,
      nays: 0,
      votes_for: ['Beasley', 'Boone', 'Brown', 'Curtis', 'Ellinger', 'Evans', 'Farmer', 'Plomin'],
      votes_against: [],
    },
    {
      clip_id: 7001,
      meeting_date: '2026-01-15',
      identifier: 'Resolution 0044-26',
      description: 'General Obligation Bonds for infrastructure improvements',
      motion_by: 'Evans',
      second_by: 'Farmer',
      outcome: 'passed',
      ayes: 7,
      nays: 1,
      votes_for: ['Beasley', 'Boone', 'Brown', 'Curtis', 'Evans', 'Farmer', 'Plomin'],
      votes_against: ['Ellinger'],
    },
  ],
  total: 2,
  page: 1,
  per_page: 20,
}

const VOTE_STATS = {
  total_votes: 2,
  passed: 2,
  failed: 0,
  pass_rate: 100,
  most_active_movers: [
    { member: 'Brown', count: 5 },
    { member: 'Evans', count: 3 },
  ],
}

const FINANCIAL_RESPONSE = {
  items: [
    {
      clip_id: 7001,
      meeting_date: '2026-01-15',
      description: 'General Obligation Bonds for infrastructure improvements',
      amount: '$18,040,000',
      type: 'appropriation',
      identifier: 'Resolution 0044-26',
    },
  ],
  total: 1,
}

test.describe('Vote Tracker Page', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    // Mock vote tracker API endpoints
    await page.route('**/api/v1/votes?*', (route) =>
      route.fulfill({ json: VOTES_RESPONSE })
    )
    await page.route('**/api/v1/votes/stats*', (route) =>
      route.fulfill({ json: VOTE_STATS })
    )
    await page.route('**/api/v1/votes/financial*', (route) =>
      route.fulfill({ json: FINANCIAL_RESPONSE })
    )
    await page.route('**/api/v1/votes/member/*', (route) =>
      route.fulfill({
        json: {
          member: 'Brown',
          total_votes: 10,
          voted_for: 9,
          voted_against: 1,
          motioned: 5,
          votes: [],
        },
      })
    )
    await page.route('**/api/v1/tracker/alerts*', (route) =>
      route.fulfill({ json: { alerts: [] } })
    )
    await page.goto('/votes')
  })

  test('renders the page header', async ({ page }) => {
    await expect(page.getByText('Vote Tracker & Policy Monitor')).toBeVisible()
    await expect(page.getByText('Track votes, monitor financial items')).toBeVisible()
  })

  test('renders tab navigation with four tabs', async ({ page }) => {
    const tablist = page.locator('[role="tablist"]')
    await expect(tablist).toBeVisible()

    await expect(page.locator('[role="tab"]').filter({ hasText: 'Votes' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Members' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Financial' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Alerts' })).toBeVisible()
  })

  test('Votes tab is active by default', async ({ page }) => {
    const votesTab = page.locator('#vt-tab-votes')
    await expect(votesTab).toHaveAttribute('aria-selected', 'true')
  })

  test('Votes tab shows filter controls', async ({ page }) => {
    const memberFilter = page.locator('[aria-label="Filter by member name"]')
    await expect(memberFilter).toBeVisible()

    const outcomeFilter = page.locator('[aria-label="Filter by outcome"]')
    await expect(outcomeFilter).toBeVisible()

    const keywordFilter = page.locator('[aria-label="Filter by keyword in description"]')
    await expect(keywordFilter).toBeVisible()
  })

  test('Votes tab shows vote table with data', async ({ page }) => {
    const table = page.locator('table[aria-label="Vote records"]')
    await expect(table).toBeVisible({ timeout: 5000 })

    // Should display the mock vote data
    await expect(table).toContainText('Ordinance 0016-26')
    await expect(table).toContainText('2026-01-15')
    await expect(table).toContainText('passed')
  })

  test('vote outcome filter works', async ({ page }) => {
    const outcomeFilter = page.locator('[aria-label="Filter by outcome"]')
    await outcomeFilter.selectOption('passed')
    await expect(outcomeFilter).toHaveValue('passed')
  })

  test('member name filter accepts input', async ({ page }) => {
    const memberFilter = page.locator('[aria-label="Filter by member name"]')
    await memberFilter.fill('Brown')
    await expect(memberFilter).toHaveValue('Brown')
  })

  test('clicking Members tab shows member statistics', async ({ page }) => {
    await page.locator('#vt-tab-members').click()

    const membersPanel = page.locator('#vt-panel-members')
    await expect(membersPanel).toBeVisible()

    // Should show Most Active Movers
    await expect(membersPanel).toContainText('Most Active Movers')
    await expect(membersPanel).toContainText('Brown')
    await expect(membersPanel).toContainText('5 motions')
  })

  test('clicking a member card loads their voting record', async ({ page }) => {
    await page.locator('#vt-tab-members').click()

    // Click on Brown's card
    const memberCard = page.locator('.vt-member-card').filter({ hasText: 'Brown' })
    await expect(memberCard).toBeVisible({ timeout: 5000 })
    await memberCard.click()

    // Should show the member's voting record
    await expect(page.getByText('Voting Record: Brown')).toBeVisible({ timeout: 5000 })
  })

  test('clicking Financial tab shows financial items', async ({ page }) => {
    await page.locator('#vt-tab-financial').click()

    const financialPanel = page.locator('#vt-panel-financial')
    await expect(financialPanel).toBeVisible()
  })

  test('clicking Alerts tab shows alerts section', async ({ page }) => {
    await page.locator('#vt-tab-alerts').click()

    const alertsPanel = page.locator('#vt-panel-alerts')
    await expect(alertsPanel).toBeVisible()
  })

  test('tabs have correct ARIA attributes', async ({ page }) => {
    const votesTab = page.locator('#vt-tab-votes')
    await expect(votesTab).toHaveAttribute('role', 'tab')
    await expect(votesTab).toHaveAttribute('aria-selected', 'true')
    await expect(votesTab).toHaveAttribute('aria-controls', 'vt-panel-votes')

    const membersTab = page.locator('#vt-tab-members')
    await expect(membersTab).toHaveAttribute('aria-selected', 'false')
  })

  test('date filters are present', async ({ page }) => {
    const afterDate = page.locator('[aria-label="Filter votes after date"]')
    await expect(afterDate).toBeVisible()

    const beforeDate = page.locator('[aria-label="Filter votes before date"]')
    await expect(beforeDate).toBeVisible()
  })

  test('tab switching updates active state', async ({ page }) => {
    // Start on Votes
    await expect(page.locator('#vt-tab-votes')).toHaveClass(/vt-tab-active/)

    // Switch to Members
    await page.locator('#vt-tab-members').click()
    await expect(page.locator('#vt-tab-members')).toHaveClass(/vt-tab-active/)
    await expect(page.locator('#vt-tab-votes')).not.toHaveClass(/vt-tab-active/)
  })
})
