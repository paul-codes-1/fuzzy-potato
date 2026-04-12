import { test, expect } from '@playwright/test'
import { installMockRoutes } from '../fixtures/mock-data.js'

test.describe('Global Search', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    await page.goto('/')
    // Wait for the page to fully load
    await expect(page.locator('.meeting-card').first()).toBeVisible()
  })

  test('search input is present in the header', async ({ page }) => {
    const input = page.locator('.gs-input')
    await expect(input).toBeVisible()
    await expect(input).toHaveAttribute('role', 'combobox')
    await expect(input).toHaveAttribute('aria-haspopup', 'listbox')
  })

  test('typing in search bar opens dropdown with results', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    // Wait for the dropdown to appear
    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Verify autocomplete suggestions section appears
    await expect(page.locator('.gs-section-label').filter({ hasText: 'Suggestions' })).toBeVisible()

    // Verify results section appears
    await expect(page.locator('.gs-section-label').filter({ hasText: 'Results' })).toBeVisible()
  })

  test('autocomplete suggestions are categorized', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Check suggestion items show category labels
    const suggestionCats = page.locator('.gs-suggestion-cat')
    await expect(suggestionCats.first()).toBeVisible()
    // Our mock data has 'topic' and 'meeting_body' categories
    await expect(page.getByText('Topic')).toBeVisible()
    await expect(page.getByText('Body')).toBeVisible()
  })

  test('search results show category badges', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Result items should have category badges
    const badges = page.locator('.gs-badge')
    await expect(badges.first()).toBeVisible()
  })

  test('keyboard ArrowDown navigates through dropdown items', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Press ArrowDown to highlight first item
    await page.keyboard.press('ArrowDown')
    let activeItem = page.locator('.gs-item--active')
    await expect(activeItem).toBeVisible()

    // Press again to move to second item
    await page.keyboard.press('ArrowDown')
    activeItem = page.locator('.gs-item--active')
    await expect(activeItem).toBeVisible()
  })

  test('keyboard ArrowUp moves highlight back', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Move down twice, then up once
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowUp')

    const activeItem = page.locator('.gs-item--active')
    await expect(activeItem).toBeVisible()
  })

  test('Escape closes the dropdown', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    await page.keyboard.press('Escape')
    await expect(dropdown).not.toBeVisible()
  })

  test('clicking a result navigates to meeting detail', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Click on a result item
    const resultItem = page.locator('.gs-item--result').first()
    await resultItem.click()

    // Should navigate to the meeting detail page
    await expect(page).toHaveURL(/\/meeting\/7001/)
  })

  test('Enter on highlighted result navigates to meeting', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Navigate past suggestions to a result and press Enter
    // Suggestions: 2 items. Results: 2 items. We need to go to index 2 for first result.
    await page.keyboard.press('ArrowDown') // suggestion 1
    await page.keyboard.press('ArrowDown') // suggestion 2
    await page.keyboard.press('ArrowDown') // result 1
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/\/meeting\/7001/)
  })

  test('clear button clears the search and closes dropdown', async ({ page }) => {
    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // Click clear button
    const clearBtn = page.locator('.gs-clear')
    await clearBtn.click()

    await expect(input).toHaveValue('')
    // Dropdown should close or show no results
    await expect(page.locator('.gs-item--result')).not.toBeVisible()
  })

  test('no results message for unknown query', async ({ page }) => {
    // Override search route to return empty
    await page.route('**/api/v1/search?*', (route) =>
      route.fulfill({ json: { results: [], total: 0, took_ms: 5 } })
    )
    await page.route('**/api/v1/search/autocomplete?*', (route) =>
      route.fulfill({ json: { suggestions: [] } })
    )

    const input = page.locator('.gs-input')
    await input.click()
    await input.fill('xyznonexistent')

    await expect(page.locator('.gs-item--empty')).toBeVisible({ timeout: 5000 })
    await expect(page.getByText(/no results found/i)).toBeVisible()
  })

  test('dropdown is accessible with correct ARIA attributes', async ({ page }) => {
    const input = page.locator('.gs-input')
    await expect(input).toHaveAttribute('aria-autocomplete', 'list')
    await expect(input).toHaveAttribute('aria-controls', 'gs-dropdown')

    // Before typing, aria-expanded should be false
    await expect(input).toHaveAttribute('aria-expanded', 'false')

    await input.click()
    await input.fill('zoning')

    const dropdown = page.locator('.gs-dropdown')
    await expect(dropdown).toBeVisible({ timeout: 5000 })

    // After dropdown opens, aria-expanded should be true
    await expect(input).toHaveAttribute('aria-expanded', 'true')

    // Dropdown items should have role="option"
    const options = dropdown.locator('[role="option"]')
    const count = await options.count()
    expect(count).toBeGreaterThan(0)
  })
})
