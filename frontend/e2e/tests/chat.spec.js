import { test, expect } from '@playwright/test'
import { installMockRoutes } from '../fixtures/mock-data.js'

const CHAT_RESPONSE = {
  content: 'The council discussed zoning changes on Newtown Pike during the January 15 meeting. The ordinance passed 8-0.',
  sources: [
    {
      clip_id: 7001,
      date: '2026-01-15',
      title: 'January 15 2026 Urban County Council meeting',
      meeting_body: 'Urban County Council',
      excerpt: 'Ordinance 0016-26 zoning change approved 8-0...',
    },
  ],
  model_used: 'gpt-4o',
}

test.describe('Chat Page', () => {
  test.beforeEach(async ({ page }) => {
    await installMockRoutes(page)
    // Mock chat endpoint
    await page.route('**/api/chat*', (route) =>
      route.fulfill({ json: CHAT_RESPONSE })
    )
    await page.goto('/chat')
  })

  test('renders empty state with suggested questions', async ({ page }) => {
    const emptyState = page.locator('.chat-empty-state')
    await expect(emptyState).toBeVisible()

    // Suggested question buttons should be present
    const suggestedBtns = page.locator('.chat-suggested-btn')
    const count = await suggestedBtns.count()
    expect(count).toBeGreaterThanOrEqual(1)
  })

  test('chat input and send button are present', async ({ page }) => {
    const textarea = page.locator('#chat-message-input')
    await expect(textarea).toBeVisible()

    const sendBtn = page.locator('.chat-send-btn')
    await expect(sendBtn).toBeVisible()
    await expect(sendBtn).toBeDisabled()
  })

  test('send button enables when user types a message', async ({ page }) => {
    const textarea = page.locator('#chat-message-input')
    await textarea.fill('Tell me about zoning changes')

    const sendBtn = page.locator('.chat-send-btn')
    await expect(sendBtn).toBeEnabled()
  })

  test('sending a message shows user message and assistant response', async ({ page }) => {
    const textarea = page.locator('#chat-message-input')
    await textarea.fill('What about zoning?')

    const sendBtn = page.locator('.chat-send-btn')
    await sendBtn.click()

    // The chat messages area should appear (replacing empty state)
    const chatLog = page.locator('[role="log"]')
    await expect(chatLog).toBeVisible({ timeout: 5000 })

    // User message should be visible
    await expect(chatLog).toContainText('What about zoning?')

    // Assistant response should appear
    await expect(chatLog).toContainText('council discussed zoning changes')
  })

  test('clicking a suggested question sends it', async ({ page }) => {
    const firstSuggested = page.locator('.chat-suggested-btn').first()
    const questionText = await firstSuggested.textContent()

    await firstSuggested.click()

    // Chat log should appear with the question
    const chatLog = page.locator('[role="log"]')
    await expect(chatLog).toBeVisible({ timeout: 5000 })
    await expect(chatLog).toContainText(questionText)
  })

  test('new chat button clears the conversation', async ({ page }) => {
    // Send a message first
    const textarea = page.locator('#chat-message-input')
    await textarea.fill('Hello')
    await page.locator('.chat-send-btn').click()

    // Wait for response
    const chatLog = page.locator('[role="log"]')
    await expect(chatLog).toBeVisible({ timeout: 5000 })

    // Click new chat
    const newChatBtn = page.locator('.chat-new-btn')
    await newChatBtn.click()

    // Empty state should return
    await expect(page.locator('.chat-empty-state')).toBeVisible()
  })

  test('model selector is visible', async ({ page }) => {
    const modelSelector = page.locator('.model-selector')
    await expect(modelSelector).toBeVisible()
  })

  test('filters toggle shows and hides filter panel', async ({ page }) => {
    const filtersToggle = page.locator('.chat-filters-toggle')
    await expect(filtersToggle).toBeVisible()

    // Panel should not be visible initially
    await expect(page.locator('#chat-filters-panel')).not.toBeVisible()

    // Click to show
    await filtersToggle.click()
    await expect(page.locator('#chat-filters-panel')).toBeVisible()

    // Verify filter controls exist
    const filterPanel = page.locator('#chat-filters-panel')
    await expect(filterPanel.locator('select')).toBeVisible()
    await expect(filterPanel.locator('input[type="date"]').first()).toBeVisible()

    // Click to hide
    await filtersToggle.click()
    await expect(page.locator('#chat-filters-panel')).not.toBeVisible()
  })

  test('filters toggle has correct aria-expanded attribute', async ({ page }) => {
    const toggle = page.locator('.chat-filters-toggle')
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')

    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  test('Ctrl+Enter sends the message', async ({ page }) => {
    const textarea = page.locator('#chat-message-input')
    await textarea.fill('Ctrl+Enter test')

    await textarea.press('Control+Enter')

    const chatLog = page.locator('[role="log"]')
    await expect(chatLog).toBeVisible({ timeout: 5000 })
    await expect(chatLog).toContainText('Ctrl+Enter test')
  })

  test('shows error and retry button when API fails', async ({ page }) => {
    // Override chat route to fail
    await page.route('**/api/chat*', (route) =>
      route.fulfill({ status: 500, json: { detail: 'Server error' } })
    )

    const textarea = page.locator('#chat-message-input')
    await textarea.fill('fail test')
    await page.locator('.chat-send-btn').click()

    const chatError = page.locator('.chat-error')
    await expect(chatError).toBeVisible({ timeout: 5000 })

    const retryBtn = page.locator('.chat-retry-btn')
    await expect(retryBtn).toBeVisible()
  })

  test('typing indicator appears while waiting for response', async ({ page }) => {
    // Add a delay to the chat response
    await page.route('**/api/chat*', async (route) => {
      await new Promise((r) => setTimeout(r, 500))
      route.fulfill({ json: CHAT_RESPONSE })
    })

    const textarea = page.locator('#chat-message-input')
    await textarea.fill('slow question')
    await page.locator('.chat-send-btn').click()

    // Typing indicator should appear
    const typingIndicator = page.locator('.chat-typing')
    await expect(typingIndicator).toBeVisible()

    // Then response should appear
    const chatLog = page.locator('[role="log"]')
    await expect(chatLog).toContainText('council discussed zoning changes', { timeout: 5000 })
  })
})
