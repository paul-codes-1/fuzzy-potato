import { test, expect } from '@playwright/test'
import { installMockRoutes } from '../fixtures/mock-data.js'

test.describe('Onboarding Page', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    await page.goto('/onboarding')
  })

  test('renders the welcome message and plan selection (step 1)', async ({ page }) => {
    await expect(page.getByText('Welcome to CivicLens')).toBeVisible()
    await expect(page.getByText('Choose a plan')).toBeVisible()

    // Plan cards should be visible
    const planCards = page.locator('.ob-plan-card')
    const count = await planCards.count()
    expect(count).toBeGreaterThanOrEqual(2)
  })

  test('progress bar is visible with step indicators', async ({ page }) => {
    const progress = page.locator('.ob-progress')
    await expect(progress).toBeVisible()

    // Step dots
    const stepDots = page.locator('.ob-step-dot')
    const count = await stepDots.count()
    expect(count).toBeGreaterThanOrEqual(4)
  })

  test('selecting a plan highlights the card', async ({ page }) => {
    const planCard = page.locator('.ob-plan-card').first()
    await planCard.click()

    await expect(planCard).toHaveClass(/selected/)
  })

  test('Continue button advances to step 2 (city configuration)', async ({ page }) => {
    // Select a plan first
    await page.locator('.ob-plan-card').first().click()

    // Click Continue
    const continueBtn = page.locator('.ob-btn-next')
    await expect(continueBtn).toBeVisible()
    await continueBtn.click()

    // Step 2: Configure Your City
    await expect(page.getByText('Configure Your City')).toBeVisible()
    await expect(page.getByText('Granicus Host URL')).toBeVisible()
  })

  test('step 2 shows city name and Granicus host inputs', async ({ page }) => {
    // Navigate to step 2
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()

    // City name input
    const cityInput = page.locator('input[placeholder*="City of"]')
    await expect(cityInput).toBeVisible()

    // Granicus host input
    const hostInput = page.locator('input[placeholder*="granicus.com"]')
    await expect(hostInput).toBeVisible()

    // Verify button
    await expect(page.locator('.ob-detect-btn')).toBeVisible()
  })

  test('step 2 city name input accepts text', async ({ page }) => {
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()

    const cityInput = page.locator('input[placeholder*="City of"]')
    await cityInput.fill('City of Elk Grove')
    await expect(cityInput).toHaveValue('City of Elk Grove')
  })

  test('Back button returns to the previous step', async ({ page }) => {
    // Go to step 2
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()
    await expect(page.getByText('Configure Your City')).toBeVisible()

    // Click Back
    const backBtn = page.locator('.ob-btn-back')
    await expect(backBtn).toBeVisible()
    await backBtn.click()

    // Should be back on step 1
    await expect(page.getByText('Welcome to CivicLens')).toBeVisible()
  })

  test('Back button is not shown on step 1', async ({ page }) => {
    await expect(page.locator('.ob-btn-back')).not.toBeVisible()
  })

  test('navigating to step 3 shows account setup form', async ({ page }) => {
    // Step 1: select plan
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()

    // Step 2: fill city config
    await page.locator('input[placeholder*="City of"]').fill('City of Elk Grove')
    await page.locator('input[placeholder*="granicus.com"]').fill('elkgrovecity.granicus.com')
    await page.locator('.ob-btn-next').click()

    // Step 3: Account setup
    await expect(page.getByText('Set Up Your Account')).toBeVisible()

    // Organization name input
    await expect(page.locator('input[placeholder*="City of Elk Grove"]')).toBeVisible()

    // Admin email input
    await expect(page.locator('input[placeholder*="admin@"]')).toBeVisible()

    // Setup summary card should be visible
    await expect(page.getByText('Setup Summary')).toBeVisible()
  })

  test('step 3 shows a summary of selected plan and city', async ({ page }) => {
    // Go through steps 1 and 2
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()
    await page.locator('input[placeholder*="City of"]').fill('City of Elk Grove')
    await page.locator('input[placeholder*="granicus.com"]').fill('elkgrovecity.granicus.com')
    await page.locator('.ob-btn-next').click()

    // Summary card should show the city name
    const summaryCard = page.locator('.ob-summary-card')
    await expect(summaryCard).toBeVisible()
    await expect(summaryCard).toContainText('City of Elk Grove')
    await expect(summaryCard).toContainText('elkgrovecity.granicus.com')
  })

  test('step 3 Create Account button triggers tenant creation', async ({ page }) => {
    // Go through steps 1, 2, and fill step 3
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()
    await page.locator('input[placeholder*="City of"]').fill('City of Elk Grove')
    await page.locator('input[placeholder*="granicus.com"]').fill('elkgrovecity.granicus.com')
    await page.locator('.ob-btn-next').click()

    // Fill account form
    await page.locator('input[placeholder*="City of Elk Grove"]').fill('City of Elk Grove')
    await page.locator('input[placeholder*="admin@"]').fill('admin@elkgrovecity.org')

    // The button should say "Create Account" on step 3
    const createBtn = page.locator('.ob-btn-next')
    await expect(createBtn).toContainText('Create Account')
    await createBtn.click()

    // Should advance to step 4 (API Key)
    await expect(page.getByText('Your API Key')).toBeVisible({ timeout: 5000 })
  })

  test('step 4 shows the API key with copy button', async ({ page }) => {
    // Navigate through to step 4
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()
    await page.locator('input[placeholder*="City of"]').fill('Test City')
    await page.locator('input[placeholder*="granicus.com"]').fill('test.granicus.com')
    await page.locator('.ob-btn-next').click()
    await page.locator('input[placeholder*="City of Elk Grove"]').fill('Test Org')
    await page.locator('input[placeholder*="admin@"]').fill('admin@test.gov')
    await page.locator('.ob-btn-next').click()

    // API key page
    await expect(page.getByText('Your API Key')).toBeVisible({ timeout: 5000 })

    // API key code element should contain the mock key
    const apiKeyCode = page.locator('.ob-apikey-code')
    await expect(apiKeyCode).toBeVisible()

    // Copy button
    const copyBtn = page.locator('.ob-copy-btn')
    await expect(copyBtn).toBeVisible()

    // Warning about saving the key
    await expect(page.getByText('Save this key somewhere safe')).toBeVisible()
  })

  test('plan cards show pricing and features', async ({ page }) => {
    // At least one plan should show a price
    const planAmount = page.locator('.ob-plan-amount').first()
    await expect(planAmount).toBeVisible()

    // Features list
    const features = page.locator('.ob-plan-features li')
    const count = await features.count()
    expect(count).toBeGreaterThan(0)
  })

  test('Most Popular badge is shown on recommended plan', async ({ page }) => {
    const popularBadge = page.locator('.ob-plan-badge')
    const count = await popularBadge.count()
    // At least one plan should have the popular badge
    expect(count).toBeGreaterThanOrEqual(1)
    await expect(popularBadge.first()).toContainText('Most Popular')
  })

  test('progress bar step dots show completed state', async ({ page }) => {
    // Step 1 should be active
    const firstDot = page.locator('.ob-step-dot').first()
    await expect(firstDot).toHaveClass(/active/)

    // Advance to step 2
    await page.locator('.ob-plan-card').first().click()
    await page.locator('.ob-btn-next').click()

    // First dot should now be done
    await expect(firstDot).toHaveClass(/done/)

    // Second dot should be active
    const secondDot = page.locator('.ob-step-dot').nth(1)
    await expect(secondDot).toHaveClass(/active/)
  })

  test('future step dots are disabled', async ({ page }) => {
    // On step 1, steps 2+ should be disabled
    const thirdDot = page.locator('.ob-step-dot').nth(2)
    await expect(thirdDot).toBeDisabled()
  })
})
