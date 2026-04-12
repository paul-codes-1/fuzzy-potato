import { test, expect } from '@playwright/test'
import { installMockRoutes, SAMPLE_CLIP_ID } from '../fixtures/mock-data.js'

test.describe('Meeting Detail Page', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    await page.goto(`/meeting/${SAMPLE_CLIP_ID}`)
  })

  test('renders the meeting title and metadata', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('January 15 2026 Urban County Council meeting')

    const meta = page.locator('.meeting-meta')
    await expect(meta).toContainText('January')
    await expect(meta).toContainText('Urban County Council')
    await expect(meta).toContainText('9,500 words')
  })

  test('back link navigates to meeting list', async ({ page }) => {
    const backLink = page.locator('.back-link')
    await expect(backLink).toBeVisible()
    await expect(backLink).toContainText('Back to all meetings')
    await expect(backLink).toHaveAttribute('href', '/')
  })

  test('tab bar renders with correct tabs', async ({ page }) => {
    const tablist = page.locator('[role="tablist"]')
    await expect(tablist).toBeVisible()

    await expect(page.locator('[role="tab"]').filter({ hasText: 'Overview' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Transcript' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Agenda' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Official Minutes' })).toBeVisible()
    await expect(page.locator('[role="tab"]').filter({ hasText: 'Changes' })).toBeVisible()
  })

  test('Overview tab shows meeting information from extracted facts', async ({ page }) => {
    // Overview should be the default active tab
    const overviewTab = page.locator('#tab-overview')
    await expect(overviewTab).toHaveAttribute('aria-selected', 'true')

    const panel = page.locator('#tabpanel-overview')
    await expect(panel).toBeVisible()

    // Meeting Info section
    await expect(panel).toContainText('Meeting Information')
    await expect(panel).toContainText('Urban County Council')
    await expect(panel).toContainText('Mayor Linda Gorton')
    await expect(panel).toContainText('Council Chambers')
  })

  test('Overview tab shows attendance information', async ({ page }) => {
    const panel = page.locator('#tabpanel-overview')
    await expect(panel).toContainText('Attendance')
    await expect(panel).toContainText('Present (8)')
    await expect(panel).toContainText('Beasley')
    await expect(panel).toContainText('Absent')
    await expect(panel).toContainText('McCurn')
  })

  test('Overview tab shows votes and decisions', async ({ page }) => {
    const panel = page.locator('#tabpanel-overview')
    await expect(panel).toContainText('Votes')
    await expect(panel).toContainText('Ordinance 0016-26')
    await expect(panel).toContainText('passed')
    await expect(panel).toContainText('Ayes: 8')
    await expect(panel).toContainText('Nays: 0')
  })

  test('Overview tab shows financial items', async ({ page }) => {
    const panel = page.locator('#tabpanel-overview')
    await expect(panel).toContainText('Budget')
    await expect(panel).toContainText('$18,040,000')
    await expect(panel).toContainText('appropriation')
  })

  test('Overview tab shows public comments', async ({ page }) => {
    const panel = page.locator('#tabpanel-overview')
    await expect(panel).toContainText('Public Comments')
    await expect(panel).toContainText('John Smith')
    await expect(panel).toContainText('Zoning concerns')
  })

  test('clicking Transcript tab shows transcript segments', async ({ page }) => {
    await page.locator('#tab-transcript').click()

    const panel = page.locator('#tabpanel-transcript')
    await expect(panel).toBeVisible()

    // Timestamp links should be visible
    const timestampLinks = panel.locator('.timestamp-link')
    const count = await timestampLinks.count()
    expect(count).toBeGreaterThan(0)

    // Transcript text should be present
    await expect(panel).toContainText('Urban County Council meeting is called to order')
  })

  test('clicking Agenda tab shows agenda text', async ({ page }) => {
    await page.locator('#tab-agenda').click()

    const panel = page.locator('#tabpanel-agenda')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('URBAN COUNTY COUNCIL')
    await expect(panel).toContainText('Ordinance 0016-26')
  })

  test('clicking Official Minutes tab shows minutes text', async ({ page }) => {
    const minutesTab = page.locator('[role="tab"]').filter({ hasText: 'Official Minutes' })
    await minutesTab.click()

    const panel = page.locator('#tabpanel-minutes')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('OFFICIAL MINUTES')
    await expect(panel).toContainText('John Smith spoke regarding concerns')
    await expect(panel).toContainText('Vote: 8-0. PASSED')
  })

  test('tabs have correct ARIA attributes', async ({ page }) => {
    const overviewTab = page.locator('#tab-overview')
    await expect(overviewTab).toHaveAttribute('aria-selected', 'true')
    await expect(overviewTab).toHaveAttribute('aria-controls', 'tabpanel-overview')
    await expect(overviewTab).toHaveAttribute('role', 'tab')

    const transcriptTab = page.locator('#tab-transcript')
    await expect(transcriptTab).toHaveAttribute('aria-selected', 'false')
  })

  test('Download Files section shows available file links', async ({ page }) => {
    await expect(page.getByText('Download Files')).toBeVisible()

    // Transcript download link should be present
    await expect(page.getByText('Transcript (.txt)')).toBeVisible()
  })

  test('video embed section is visible', async ({ page }) => {
    await expect(page.getByText('Watch Meeting Video')).toBeVisible()

    // Granicus link should be present
    const granicusLink = page.locator('a').filter({ hasText: 'Open video on Granicus' })
    await expect(granicusLink).toBeVisible()
    await expect(granicusLink).toHaveAttribute('href', new RegExp(`clip/${SAMPLE_CLIP_ID}`))
  })

  test('transcript timestamp hint is shown', async ({ page }) => {
    await page.locator('#tab-transcript').click()
    await expect(page.getByText('Click a timestamp to jump to that point')).toBeVisible()
  })

  test('shows not found state for missing meeting', async ({ page }) => {
    // Navigate to a non-existent meeting (no mock route for 9999)
    await page.route('**/data/clips/9999/metadata.json', (route) =>
      route.fulfill({ status: 404, body: 'Not found' })
    )
    await page.goto('/meeting/9999')

    await expect(page.getByText('Meeting not found')).toBeVisible()
    await expect(page.getByText('Back to all meetings')).toBeVisible()
  })
})
