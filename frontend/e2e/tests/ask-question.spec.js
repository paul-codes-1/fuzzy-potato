import { test, expect } from '@playwright/test'
import { installMockRoutes, ASK_RESPONSE } from '../fixtures/mock-data.js'

test.describe('Ask Question Page', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    await page.goto('/ask')
  })

  test('renders the ask form with correct elements', async ({ page }) => {
    const form = page.locator('form[aria-label*="question"]')
    await expect(form).toBeVisible()

    const input = page.locator('#ask-question-input')
    await expect(input).toBeVisible()
    await expect(input).toHaveAttribute('type', 'text')

    const submitBtn = page.locator('button.ask-button')
    await expect(submitBtn).toBeVisible()
    await expect(submitBtn).toBeDisabled()
  })

  test('submit button is disabled when input is empty', async ({ page }) => {
    const submitBtn = page.locator('button.ask-button')
    await expect(submitBtn).toBeDisabled()
  })

  test('submit button enables when user types a question', async ({ page }) => {
    const input = page.locator('#ask-question-input')
    await input.fill('What happened at the last council meeting?')

    const submitBtn = page.locator('button.ask-button')
    await expect(submitBtn).toBeEnabled()
  })

  test('submitting a question shows the answer and sources', async ({ page }) => {
    const input = page.locator('#ask-question-input')
    await input.fill('What happened with the zoning change?')

    const submitBtn = page.locator('button.ask-button')
    await submitBtn.click()

    // Wait for answer to render
    const answerDiv = page.locator('.ask-answer')
    await expect(answerDiv).toBeVisible({ timeout: 5000 })

    // The answer text should contain part of the mock response
    await expect(answerDiv).toContainText('approved')

    // Sources section should appear
    const sources = page.locator('.ask-sources')
    await expect(sources).toBeVisible()
    await expect(sources).toContainText('Sources')
  })

  test('source card shows meeting title, date, and timestamp', async ({ page }) => {
    const input = page.locator('#ask-question-input')
    await input.fill('zoning change')
    await page.locator('button.ask-button').click()

    const sourceCard = page.locator('.ask-source-card').first()
    await expect(sourceCard).toBeVisible({ timeout: 5000 })

    // Title link to meeting detail
    const titleLink = sourceCard.locator('.ask-source-title')
    await expect(titleLink).toContainText('January 15 2026 Urban County Council meeting')
    await expect(titleLink).toHaveAttribute('href', /\/meeting\/7001/)

    // Date badge
    await expect(sourceCard.locator('.ask-source-date')).toContainText('2026-01-15')

    // Timestamp badge
    const timestampBadge = sourceCard.locator('.ask-timestamp-badge')
    await expect(timestampBadge).toBeVisible()
  })

  test('filter controls are visible and interactive', async ({ page }) => {
    // Meeting body filter
    const bodySelect = page.locator('.ask-filters select')
    await expect(bodySelect).toBeVisible()
    await bodySelect.selectOption('Council')
    await expect(bodySelect).toHaveValue('Council')

    // Date after filter
    const dateAfter = page.locator('.ask-filters input[type="date"]').first()
    await expect(dateAfter).toBeVisible()
    await dateAfter.fill('2026-01-01')
    await expect(dateAfter).toHaveValue('2026-01-01')

    // Date before filter
    const dateBefore = page.locator('.ask-filters input[type="date"]').last()
    await expect(dateBefore).toBeVisible()
    await dateBefore.fill('2026-12-31')
    await expect(dateBefore).toHaveValue('2026-12-31')
  })

  test('submitting via Enter key works', async ({ page }) => {
    const input = page.locator('#ask-question-input')
    await input.fill('zoning')
    await input.press('Enter')

    const answerDiv = page.locator('.ask-answer')
    await expect(answerDiv).toBeVisible({ timeout: 5000 })
  })

  test('shows error state when API fails', async ({ page }) => {
    // Override the ask route to return an error
    await page.route('**/api/ask*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Internal server error' } })
    )

    const input = page.locator('#ask-question-input')
    await input.fill('some question')
    await page.locator('button.ask-button').click()

    const errorDiv = page.locator('.ask-error')
    await expect(errorDiv).toBeVisible({ timeout: 5000 })
  })

  test('coverage note is displayed', async ({ page }) => {
    const coverageNote = page.locator('.ask-coverage-note')
    await expect(coverageNote).toBeVisible()
  })

  test('loading spinner appears during request', async ({ page }) => {
    // Add a delay to the response so we can see the spinner
    await page.route('**/api/ask*', async (route) => {
      await new Promise((r) => setTimeout(r, 500))
      route.fulfill({ json: ASK_RESPONSE })
    })

    const input = page.locator('#ask-question-input')
    await input.fill('zoning')
    await page.locator('button.ask-button').click()

    // Spinner should appear
    const loading = page.locator('.ask-loading')
    await expect(loading).toBeVisible()

    // Then answer should appear
    await expect(page.locator('.ask-answer')).toBeVisible({ timeout: 5000 })
  })
})
