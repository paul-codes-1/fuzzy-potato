import { test, expect } from '@playwright/test'
import { installMockRoutes } from '../fixtures/mock-data.js'

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
  })

  test('home page loads with correct title', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveTitle(/CivicLens/)
  })

  test('home page renders meeting list', async ({ page }) => {
    await page.goto('/')
    // Wait for the meeting cards to appear from mock data
    await expect(page.locator('.meeting-card').first()).toBeVisible()
    // Verify at least one meeting title is shown
    await expect(page.getByText('January 15 2026 Urban County Council meeting')).toBeVisible()
  })

  test('header nav links are visible', async ({ page }) => {
    await page.goto('/')
    const nav = page.locator('nav[aria-label="Main navigation"]')
    await expect(nav).toBeVisible()

    await expect(nav.getByText('Browse')).toBeVisible()
    await expect(nav.getByText('Chat')).toBeVisible()
    await expect(nav.getByText('Votes')).toBeVisible()
    await expect(nav.getByText('Analytics')).toBeVisible()
  })

  test('navigate to /ask via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /ask/i }).first().click()
    await expect(page).toHaveURL(/\/ask/)
    // The ask page has a form
    await expect(page.locator('form[aria-label*="question"]')).toBeVisible()
  })

  test('navigate to /chat via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /chat/i }).first().click()
    await expect(page).toHaveURL(/\/chat/)
  })

  test('navigate to /votes via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /votes/i }).first().click()
    await expect(page).toHaveURL(/\/votes/)
  })

  test('navigate to /analytics via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /analytics/i }).first().click()
    await expect(page).toHaveURL(/\/analytics/)
  })

  test('navigate to /onboarding via Get Started CTA', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /get started/i }).click()
    await expect(page).toHaveURL(/\/onboarding/)
    await expect(page.getByText('Welcome to CivicLens')).toBeVisible()
  })

  test('navigate to /highlights via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /highlights/i }).first().click()
    await expect(page).toHaveURL(/\/highlights/)
  })

  test('navigate to /digest via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /digest/i }).first().click()
    await expect(page).toHaveURL(/\/digest/)
  })

  test('navigate to /reports via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /reports/i }).first().click()
    await expect(page).toHaveURL(/\/reports/)
  })

  test('navigate to /whats-new via nav link', async ({ page }) => {
    await page.goto('/')
    await page.getByRole('link', { name: /what.*new/i }).first().click()
    await expect(page).toHaveURL(/\/whats-new/)
  })

  test('skip-to-content link works', async ({ page }) => {
    await page.goto('/')
    // The skip link should exist in the DOM
    const skipLink = page.locator('a.skip-link')
    await expect(skipLink).toHaveAttribute('href', '#main-content')

    // Focus the skip link via Tab key
    await page.keyboard.press('Tab')
    // The skip link should now be focused (it becomes visible on focus via CSS)
    await expect(skipLink).toBeFocused()

    // Click it and verify main-content gets focus
    await skipLink.click()
    await expect(page.locator('#main-content')).toBeFocused()
  })

  test('language switcher is visible in header', async ({ page }) => {
    await page.goto('/')
    const switcher = page.locator('.language-switcher')
    await expect(switcher).toBeVisible()

    // It should contain a select element
    const select = switcher.locator('select[aria-label="Select language"]')
    await expect(select).toBeVisible()

    // It should have at least English as an option
    const options = select.locator('option')
    await expect(options.first()).toBeVisible()
  })

  test('language switcher changes locale', async ({ page }) => {
    await page.goto('/')
    const select = page.locator('select[aria-label="Select language"]')

    // Select Spanish
    await select.selectOption('es')

    // Verify the select value changed
    await expect(select).toHaveValue('es')
  })

  test('no console errors on page load', async ({ page }) => {
    const errors = []
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        errors.push(msg.text())
      }
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    // Filter out expected noise (service worker, favicon)
    const realErrors = errors.filter(
      (e) => !e.includes('sw.js') && !e.includes('favicon') && !e.includes('manifest')
    )
    expect(realErrors).toHaveLength(0)
  })

  test('footer is visible with content', async ({ page }) => {
    await page.goto('/')
    const footer = page.locator('footer[role="contentinfo"]')
    await expect(footer).toBeVisible()
  })
})
