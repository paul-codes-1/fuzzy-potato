/**
 * CivicLens Embeddable Widget v2
 * Drop-in AI-powered meeting Q&A for government websites.
 *
 * Usage:
 *   <script src="https://cdn.civiclens.ai/widget.js"
 *     data-api-key="mra_..."
 *     data-api-url="https://api.civiclens.ai"
 *     data-theme="light"
 *     data-position="bottom-right"
 *     data-accent-color="#1a56db"
 *     data-welcome-message="Ask anything about city council meetings."
 *     data-title="Meeting Q&A"
 *     data-suggested-questions="What was discussed at the last council meeting?|How was the budget allocated?|Were there any contentious votes recently?">
 *   </script>
 *
 * MIT License - CivicLens 2026
 */
(function () {
  'use strict';

  // Prevent double-initialization
  if (window.__civiclens_loaded) return;
  window.__civiclens_loaded = true;

  // ---------------------------------------------------------------------------
  // Configuration from script tag data attributes
  // ---------------------------------------------------------------------------
  var script = document.currentScript || (function () {
    var scripts = document.getElementsByTagName('script');
    return scripts[scripts.length - 1];
  })();

  var CONFIG = {
    apiKey:         script.getAttribute('data-api-key') || '',
    apiUrl:         (script.getAttribute('data-api-url') || '').replace(/\/+$/, ''),
    theme:          script.getAttribute('data-theme') || 'light',
    position:       script.getAttribute('data-position') || 'bottom-right',
    accentColor:    script.getAttribute('data-accent-color') || '#1a56db',
    welcomeMessage: script.getAttribute('data-welcome-message') || 'Ask a question about city council meetings, votes, budgets, or any public discussion.',
    title:          script.getAttribute('data-title') || 'Meeting Q&A',
    suggestedQuestions: (script.getAttribute('data-suggested-questions') || 'What was discussed at the last council meeting?|How was the budget allocated this year?|Were there any contentious votes recently?').split('|').slice(0, 3),
  };

  // Derive colors from accent
  function hexToRgb(hex) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return { r: r, g: g, b: b };
  }

  var accent = hexToRgb(CONFIG.accentColor);
  var accentHover = 'rgb(' + Math.max(0, accent.r - 20) + ',' + Math.max(0, accent.g - 20) + ',' + Math.max(0, accent.b - 20) + ')';
  var accentLight = 'rgba(' + accent.r + ',' + accent.g + ',' + accent.b + ',0.08)';
  var accentLighter = 'rgba(' + accent.r + ',' + accent.g + ',' + accent.b + ',0.04)';
  var accentSoft = 'rgba(' + accent.r + ',' + accent.g + ',' + accent.b + ',0.12)';

  var isDark = CONFIG.theme === 'dark';

  // ---------------------------------------------------------------------------
  // CSS (injected into Shadow DOM)
  // ---------------------------------------------------------------------------
  var CSS = /* css */ '\
    :host { all: initial; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; font-size: 14px; line-height: 1.5; }\
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }\
    \
    .cl-container { position: fixed; z-index: 2147483647; ' + (CONFIG.position === 'bottom-left' ? 'left: 20px;' : 'right: 20px;') + ' bottom: 20px; display: flex; flex-direction: column; align-items: ' + (CONFIG.position === 'bottom-left' ? 'flex-start' : 'flex-end') + '; }\
    \
    /* -- Floating button -- */\
    .cl-fab { width: 60px; height: 60px; border-radius: 50%; border: none; cursor: pointer; display: flex; align-items: center; justify-content: center; background: ' + CONFIG.accentColor + '; color: #fff; box-shadow: 0 4px 20px rgba(0,0,0,0.2); transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), box-shadow 0.3s ease, background 0.2s ease; position: relative; }\
    .cl-fab:hover { transform: scale(1.08); box-shadow: 0 6px 28px rgba(0,0,0,0.25); background: ' + accentHover + '; }\
    .cl-fab:focus-visible { outline: 3px solid ' + CONFIG.accentColor + '; outline-offset: 3px; }\
    .cl-fab svg { width: 26px; height: 26px; fill: currentColor; transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.2s ease; }\
    .cl-fab[aria-expanded="true"] .cl-fab-icon-chat { transform: rotate(90deg) scale(0); opacity: 0; position: absolute; }\
    .cl-fab[aria-expanded="true"] .cl-fab-icon-close { transform: rotate(0deg) scale(1); opacity: 1; }\
    .cl-fab[aria-expanded="false"] .cl-fab-icon-chat { transform: rotate(0deg) scale(1); opacity: 1; }\
    .cl-fab[aria-expanded="false"] .cl-fab-icon-close { transform: rotate(-90deg) scale(0); opacity: 0; position: absolute; }\
    \
    /* -- Notification badge on fab -- */\
    .cl-fab-badge { position: absolute; top: -2px; right: -2px; width: 18px; height: 18px; background: #ef4444; border-radius: 50%; border: 2px solid #fff; display: none; }\
    .cl-fab-badge[data-show="true"] { display: block; animation: cl-badgePop 0.3s cubic-bezier(0.34, 1.56, 0.64, 1); }\
    @keyframes cl-badgePop { from { transform: scale(0); } to { transform: scale(1); } }\
    \
    /* -- Chat panel -- */\
    .cl-panel { width: 400px; max-width: calc(100vw - 40px); max-height: min(600px, calc(100vh - 100px)); border-radius: 16px; overflow: hidden; margin-bottom: 12px; box-shadow: 0 16px 64px rgba(0,0,0,0.16), 0 2px 8px rgba(0,0,0,0.08); flex-direction: column; background: ' + (isDark ? '#1e1e2e' : '#ffffff') + '; color: ' + (isDark ? '#cdd6f4' : '#1e293b') + '; border: 1px solid ' + (isDark ? '#313244' : '#e2e8f0') + '; \
      transform-origin: bottom ' + (CONFIG.position === 'bottom-left' ? 'left' : 'right') + '; \
      transition: transform 0.3s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.25s ease; \
      transform: scale(0.9) translateY(12px); opacity: 0; pointer-events: none; display: flex; visibility: hidden; }\
    .cl-panel[data-open="true"] { transform: scale(1) translateY(0); opacity: 1; pointer-events: auto; visibility: visible; }\
    \
    /* -- Header -- */\
    .cl-header { display: flex; align-items: center; gap: 10px; padding: 16px 18px; background: ' + CONFIG.accentColor + '; color: #fff; flex-shrink: 0; }\
    .cl-header-icon { width: 22px; height: 22px; flex-shrink: 0; }\
    .cl-header-title { font-size: 15px; font-weight: 600; flex: 1; }\
    .cl-close { background: rgba(255,255,255,0.15); border: none; color: #fff; cursor: pointer; padding: 6px; border-radius: 8px; display: flex; align-items: center; justify-content: center; transition: background 0.15s; }\
    .cl-close:hover { background: rgba(255,255,255,0.25); }\
    .cl-close:focus-visible { outline: 2px solid #fff; outline-offset: 2px; }\
    .cl-close svg { width: 16px; height: 16px; }\
    \
    /* -- Messages area -- */\
    .cl-messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; scroll-behavior: smooth; }\
    .cl-messages::-webkit-scrollbar { width: 5px; }\
    .cl-messages::-webkit-scrollbar-track { background: transparent; }\
    .cl-messages::-webkit-scrollbar-thumb { background: ' + (isDark ? '#45475a' : '#cbd5e1') + '; border-radius: 3px; }\
    \
    /* -- Welcome -- */\
    .cl-welcome { text-align: center; padding: 20px 12px 8px; color: ' + (isDark ? '#a6adc8' : '#64748b') + '; font-size: 13px; line-height: 1.6; }\
    .cl-welcome-icon { font-size: 28px; margin-bottom: 8px; display: block; }\
    .cl-welcome strong { display: block; font-size: 15px; color: ' + (isDark ? '#cdd6f4' : '#1e293b') + '; margin-bottom: 4px; }\
    \
    /* -- Suggested questions -- */\
    .cl-suggestions { display: flex; flex-direction: column; gap: 6px; padding: 0 12px 12px; }\
    .cl-suggestion { background: ' + (isDark ? '#313244' : '#f8fafc') + '; border: 1px solid ' + (isDark ? '#45475a' : '#e2e8f0') + '; border-radius: 10px; padding: 10px 14px; font-size: 13px; color: ' + (isDark ? '#cdd6f4' : '#475569') + '; cursor: pointer; text-align: left; transition: background 0.15s, border-color 0.15s, transform 0.15s; font-family: inherit; line-height: 1.4; }\
    .cl-suggestion:hover { background: ' + accentLighter + '; border-color: ' + accentSoft + '; transform: translateX(3px); }\
    .cl-suggestion:focus-visible { outline: 2px solid ' + CONFIG.accentColor + '; outline-offset: 1px; }\
    .cl-suggestion::before { content: "\\2192  "; color: ' + CONFIG.accentColor + '; font-weight: 600; }\
    \
    /* -- Bubbles -- */\
    .cl-msg { max-width: 90%; padding: 10px 14px; border-radius: 14px; font-size: 14px; line-height: 1.55; word-wrap: break-word; animation: cl-msgIn 0.25s ease; }\
    @keyframes cl-msgIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }\
    .cl-msg-user { align-self: flex-end; background: ' + CONFIG.accentColor + '; color: #fff; border-bottom-right-radius: 4px; }\
    .cl-msg-bot { align-self: flex-start; background: ' + (isDark ? '#313244' : '#f1f5f9') + '; color: ' + (isDark ? '#cdd6f4' : '#1e293b') + '; border-bottom-left-radius: 4px; }\
    .cl-msg-bot p { margin: 0 0 8px 0; }\
    .cl-msg-bot p:last-child { margin-bottom: 0; }\
    .cl-msg-bot ul, .cl-msg-bot ol { margin: 4px 0 8px 18px; }\
    .cl-msg-bot li { margin-bottom: 2px; }\
    .cl-msg-bot strong { font-weight: 600; }\
    .cl-msg-bot a { color: ' + CONFIG.accentColor + '; text-decoration: underline; text-underline-offset: 2px; }\
    .cl-msg-bot a:hover { text-decoration: none; }\
    .cl-msg-bot code { background: ' + (isDark ? '#1e1e2e' : '#e2e8f0') + '; padding: 1px 5px; border-radius: 4px; font-size: 13px; font-family: "SF Mono", Menlo, monospace; }\
    \
    /* -- Sources -- */\
    .cl-sources { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }\
    .cl-sources-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: ' + (isDark ? '#a6adc8' : '#94a3b8') + '; font-weight: 600; margin-bottom: 2px; }\
    .cl-source { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: ' + (isDark ? '#1e1e2e' : '#fff') + '; border: 1px solid ' + (isDark ? '#45475a' : '#e2e8f0') + '; border-radius: 8px; text-decoration: none; color: inherit; font-size: 12px; transition: border-color 0.15s, background 0.15s; }\
    .cl-source:hover { border-color: ' + CONFIG.accentColor + '; background: ' + accentLighter + '; }\
    .cl-source:focus-visible { outline: 2px solid ' + CONFIG.accentColor + '; outline-offset: 1px; }\
    .cl-source-info { flex: 1; min-width: 0; }\
    .cl-source-title { font-weight: 600; color: ' + CONFIG.accentColor + '; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; font-size: 12px; }\
    .cl-source-meta { color: ' + (isDark ? '#a6adc8' : '#94a3b8') + '; font-size: 11px; }\
    .cl-source-ts { flex-shrink: 0; background: ' + accentLight + '; color: ' + CONFIG.accentColor + '; font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 10px; font-variant-numeric: tabular-nums; }\
    \
    /* -- Typing indicator -- */\
    .cl-typing { display: flex; align-items: center; gap: 8px; padding: 10px 14px; align-self: flex-start; background: ' + (isDark ? '#313244' : '#f1f5f9') + '; border-radius: 14px; border-bottom-left-radius: 4px; animation: cl-msgIn 0.25s ease; }\
    .cl-typing-dots { display: flex; gap: 3px; }\
    .cl-typing-dot { width: 7px; height: 7px; border-radius: 50%; background: ' + (isDark ? '#585b70' : '#94a3b8') + '; animation: cl-typingBounce 1.4s infinite ease-in-out; }\
    .cl-typing-dot:nth-child(2) { animation-delay: 0.16s; }\
    .cl-typing-dot:nth-child(3) { animation-delay: 0.32s; }\
    @keyframes cl-typingBounce { 0%, 60%, 100% { transform: translateY(0); opacity: 0.4; } 30% { transform: translateY(-5px); opacity: 1; } }\
    .cl-typing-text { font-size: 12px; color: ' + (isDark ? '#6c7086' : '#94a3b8') + '; }\
    \
    /* -- Error -- */\
    .cl-error { padding: 10px 14px; background: ' + (isDark ? '#45273a' : '#fef2f2') + '; color: ' + (isDark ? '#f38ba8' : '#dc2626') + '; border-radius: 10px; font-size: 13px; align-self: flex-start; max-width: 92%; animation: cl-msgIn 0.25s ease; }\
    \
    /* -- Input area -- */\
    .cl-input-area { display: flex; align-items: flex-end; gap: 8px; padding: 12px 14px; border-top: 1px solid ' + (isDark ? '#313244' : '#e2e8f0') + '; background: ' + (isDark ? '#181825' : '#fafafa') + '; flex-shrink: 0; }\
    .cl-input { flex: 1; resize: none; border: 1px solid ' + (isDark ? '#45475a' : '#d1d5db') + '; border-radius: 12px; padding: 10px 14px; font-size: 14px; font-family: inherit; line-height: 1.4; background: ' + (isDark ? '#1e1e2e' : '#fff') + '; color: ' + (isDark ? '#cdd6f4' : '#1e293b') + '; min-height: 42px; max-height: 100px; outline: none; transition: border-color 0.15s, box-shadow 0.15s; }\
    .cl-input::placeholder { color: ' + (isDark ? '#6c7086' : '#94a3b8') + '; }\
    .cl-input:focus { border-color: ' + CONFIG.accentColor + '; box-shadow: 0 0 0 3px ' + accentLight + '; }\
    .cl-send { width: 42px; height: 42px; border-radius: 12px; border: none; background: ' + CONFIG.accentColor + '; color: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center; flex-shrink: 0; transition: background 0.15s, opacity 0.15s, transform 0.15s; }\
    .cl-send:hover:not(:disabled) { background: ' + accentHover + '; transform: scale(1.05); }\
    .cl-send:disabled { opacity: 0.4; cursor: not-allowed; }\
    .cl-send:focus-visible { outline: 3px solid ' + CONFIG.accentColor + '; outline-offset: 2px; }\
    .cl-send svg { width: 18px; height: 18px; }\
    \
    /* -- Footer -- */\
    .cl-footer { text-align: center; padding: 8px 14px 10px; font-size: 11px; color: ' + (isDark ? '#6c7086' : '#94a3b8') + '; flex-shrink: 0; border-top: 1px solid ' + (isDark ? '#2a2a3c' : '#f1f5f9') + '; }\
    .cl-footer a { color: ' + (isDark ? '#89b4fa' : CONFIG.accentColor) + '; text-decoration: none; font-weight: 600; transition: color 0.15s; }\
    .cl-footer a:hover { text-decoration: underline; }\
    .cl-footer a:focus-visible { outline: 2px solid ' + CONFIG.accentColor + '; outline-offset: 1px; border-radius: 2px; }\
    \
    /* -- Mobile: full-screen overlay -- */\
    @media (max-width: 480px) {\
      .cl-container { left: 0 !important; right: 0 !important; bottom: 0 !important; top: 0 !important; align-items: stretch !important; padding: 0 !important; }\
      .cl-panel { width: 100% !important; max-width: 100% !important; max-height: 100vh !important; height: 100vh !important; border-radius: 0 !important; margin-bottom: 0 !important; border: none !important; transform-origin: bottom center !important; }\
      .cl-panel[data-open="true"] ~ .cl-fab { display: none; }\
      .cl-fab { position: fixed; ' + (CONFIG.position === 'bottom-left' ? 'left: 16px;' : 'right: 16px;') + ' bottom: 16px; }\
    }\
    \
    /* -- Reduced motion -- */\
    @media (prefers-reduced-motion: reduce) {\
      *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }\
    }\
  ';

  // ---------------------------------------------------------------------------
  // SVG icons
  // ---------------------------------------------------------------------------
  var ICON_CHAT = '<svg class="cl-fab-icon-chat" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/><path d="M7 9h2v2H7zm4 0h2v2h-2zm4 0h2v2h-2z"/></svg>';
  var ICON_CLOSE_FAB = '<svg class="cl-fab-icon-close" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';
  var ICON_CLOSE = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';
  var ICON_SEND = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>';
  var ICON_BUILDING = '<svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M12 2L2 7v1h20V7L12 2zm0 2.26L18.18 7H5.82L12 4.26zM2 22h20v-2H2v2zm2-3h2v-5H4v5zm4 0h2v-5H8v5zm4 0h2v-5h-2v5zm4 0h2v-5h-2v5zm4 0h2v-5h-2v5z"/></svg>';
  var ICON_PLAY = '<svg viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" width="12" height="12"><path fill="currentColor" d="M4 2l10 6-10 6z"/></svg>';

  // ---------------------------------------------------------------------------
  // Build DOM inside Shadow Root
  // ---------------------------------------------------------------------------
  var host = document.createElement('div');
  host.id = 'civiclens-widget';
  document.body.appendChild(host);

  var shadow = host.attachShadow({ mode: 'open' });

  var style = document.createElement('style');
  style.textContent = CSS;
  shadow.appendChild(style);

  var container = document.createElement('div');
  container.className = 'cl-container';
  container.setAttribute('role', 'complementary');
  container.setAttribute('aria-label', 'CivicLens Meeting Q&A Widget');

  // Panel
  var panel = document.createElement('div');
  panel.className = 'cl-panel';
  panel.setAttribute('data-open', 'false');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', CONFIG.title);

  // Header
  var header = document.createElement('div');
  header.className = 'cl-header';
  header.innerHTML = '<span class="cl-header-icon">' + ICON_BUILDING + '</span><span class="cl-header-title">' + escapeHtml(CONFIG.title) + '</span>';
  var closeBtn = document.createElement('button');
  closeBtn.className = 'cl-close';
  closeBtn.setAttribute('aria-label', 'Close chat');
  closeBtn.innerHTML = ICON_CLOSE;
  header.appendChild(closeBtn);
  panel.appendChild(header);

  // Messages
  var messages = document.createElement('div');
  messages.className = 'cl-messages';
  messages.setAttribute('role', 'log');
  messages.setAttribute('aria-live', 'polite');
  messages.setAttribute('aria-label', 'Conversation');

  // Welcome message
  var welcome = document.createElement('div');
  welcome.className = 'cl-welcome';
  welcome.innerHTML = '<span class="cl-welcome-icon" aria-hidden="true">' + ICON_BUILDING + '</span><strong>' + escapeHtml(CONFIG.title) + '</strong>' + escapeHtml(CONFIG.welcomeMessage);
  messages.appendChild(welcome);

  // Suggested questions
  var suggestionsContainer = document.createElement('div');
  suggestionsContainer.className = 'cl-suggestions';
  for (var sq = 0; sq < CONFIG.suggestedQuestions.length; sq++) {
    var sqBtn = document.createElement('button');
    sqBtn.className = 'cl-suggestion';
    sqBtn.textContent = CONFIG.suggestedQuestions[sq].trim();
    sqBtn.setAttribute('type', 'button');
    sqBtn.setAttribute('aria-label', 'Ask: ' + CONFIG.suggestedQuestions[sq].trim());
    suggestionsContainer.appendChild(sqBtn);
  }
  messages.appendChild(suggestionsContainer);
  panel.appendChild(messages);

  // Input area
  var inputArea = document.createElement('div');
  inputArea.className = 'cl-input-area';

  var textarea = document.createElement('textarea');
  textarea.className = 'cl-input';
  textarea.setAttribute('placeholder', 'Ask about meetings...');
  textarea.setAttribute('aria-label', 'Type your question');
  textarea.setAttribute('rows', '1');
  textarea.setAttribute('maxlength', '2000');

  var sendBtn = document.createElement('button');
  sendBtn.className = 'cl-send';
  sendBtn.setAttribute('aria-label', 'Send question');
  sendBtn.disabled = true;
  sendBtn.innerHTML = ICON_SEND;

  inputArea.appendChild(textarea);
  inputArea.appendChild(sendBtn);
  panel.appendChild(inputArea);

  // Footer
  var footer = document.createElement('div');
  footer.className = 'cl-footer';
  footer.innerHTML = 'Powered by <a href="https://civiclens.ai" target="_blank" rel="noopener noreferrer">CivicLens</a>';
  panel.appendChild(footer);

  container.appendChild(panel);

  // FAB
  var fab = document.createElement('button');
  fab.className = 'cl-fab';
  fab.setAttribute('aria-label', 'Open Meeting Q&A');
  fab.setAttribute('aria-expanded', 'false');
  fab.setAttribute('aria-controls', 'civiclens-panel');
  fab.innerHTML = ICON_CHAT + ICON_CLOSE_FAB;

  // Notification badge
  var badge = document.createElement('span');
  badge.className = 'cl-fab-badge';
  badge.setAttribute('data-show', 'false');
  badge.setAttribute('aria-hidden', 'true');
  fab.appendChild(badge);

  container.appendChild(fab);

  shadow.appendChild(container);

  // ---------------------------------------------------------------------------
  // Conversation state
  // ---------------------------------------------------------------------------
  var conversationHistory = []; // { role, content } pairs for /chat
  var isLoading = false;

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------
  function escapeHtml(str) {
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function formatTimestamp(seconds) {
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return mins + ':' + (secs < 10 ? '0' : '') + secs;
  }

  function simpleMarkdown(text) {
    // Lightweight markdown: bold, italic, inline code, links, paragraphs, lists
    var html = escapeHtml(text);

    // Inline code (before bold to avoid conflicts)
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');

    // Links [text](url)
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');

    // Bare URLs
    html = html.replace(/(^|[^"=])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');

    // Citations like [Clip 123, 4:30]
    html = html.replace(/\[Clip (\d+),\s*(\d+:\d{2})\]/g, '<strong>[Clip $1, $2]</strong>');

    // Split into paragraphs
    var paragraphs = html.split(/\n\n+/);
    var result = '';
    for (var i = 0; i < paragraphs.length; i++) {
      var p = paragraphs[i].trim();
      if (!p) continue;
      // Detect list items
      var lines = p.split('\n');
      var isList = true;
      for (var j = 0; j < lines.length; j++) {
        if (lines[j].trim() && !/^[-*]\s/.test(lines[j].trim()) && !/^\d+\.\s/.test(lines[j].trim())) {
          isList = false;
          break;
        }
      }
      if (isList && lines.length > 0) {
        var isOrdered = /^\d+\.\s/.test(lines[0].trim());
        result += isOrdered ? '<ol>' : '<ul>';
        for (var k = 0; k < lines.length; k++) {
          var li = lines[k].trim().replace(/^[-*]\s/, '').replace(/^\d+\.\s/, '');
          if (li) result += '<li>' + li + '</li>';
        }
        result += isOrdered ? '</ol>' : '</ul>';
      } else {
        result += '<p>' + p.replace(/\n/g, '<br>') + '</p>';
      }
    }
    return result;
  }

  function scrollToBottom() {
    messages.scrollTop = messages.scrollHeight;
  }

  function removeWelcome() {
    if (welcome.parentNode) {
      welcome.parentNode.removeChild(welcome);
    }
    if (suggestionsContainer.parentNode) {
      suggestionsContainer.parentNode.removeChild(suggestionsContainer);
    }
  }

  function addUserBubble(text) {
    removeWelcome();
    var bubble = document.createElement('div');
    bubble.className = 'cl-msg cl-msg-user';
    bubble.setAttribute('role', 'log');
    bubble.textContent = text;
    messages.appendChild(bubble);
    scrollToBottom();
  }

  function addBotBubble(html, sources) {
    var bubble = document.createElement('div');
    bubble.className = 'cl-msg cl-msg-bot';
    bubble.innerHTML = html;

    // Sources
    if (sources && sources.length > 0) {
      var srcContainer = document.createElement('div');
      srcContainer.className = 'cl-sources';
      var label = document.createElement('div');
      label.className = 'cl-sources-label';
      label.textContent = 'Sources';
      srcContainer.appendChild(label);

      for (var i = 0; i < Math.min(sources.length, 5); i++) {
        var src = sources[i];
        var card = document.createElement('a');
        card.className = 'cl-source';
        card.href = src.granicus_url || '#';
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
        card.setAttribute('aria-label', 'Source: ' + (src.title || 'Meeting clip') + (src.timestamp != null ? ' at ' + formatTimestamp(src.timestamp) : ''));

        var info = document.createElement('div');
        info.className = 'cl-source-info';

        var title = document.createElement('span');
        title.className = 'cl-source-title';
        title.textContent = src.title || 'Meeting Clip ' + (src.clip_id || '');
        info.appendChild(title);

        var meta = document.createElement('span');
        meta.className = 'cl-source-meta';
        meta.textContent = (src.date || '') + (src.meeting_body ? ' \u00B7 ' + src.meeting_body : '');
        info.appendChild(meta);

        card.appendChild(info);

        if (src.timestamp != null) {
          var ts = document.createElement('span');
          ts.className = 'cl-source-ts';
          ts.innerHTML = ICON_PLAY + ' ' + formatTimestamp(src.timestamp);
          card.appendChild(ts);
        }

        srcContainer.appendChild(card);
      }
      bubble.appendChild(srcContainer);
    }

    messages.appendChild(bubble);
    scrollToBottom();
  }

  function showTyping() {
    var loader = document.createElement('div');
    loader.className = 'cl-typing';
    loader.id = 'cl-loader';
    loader.setAttribute('role', 'status');
    loader.setAttribute('aria-label', 'CivicLens is thinking');
    var dotsWrap = document.createElement('div');
    dotsWrap.className = 'cl-typing-dots';
    dotsWrap.innerHTML = '<div class="cl-typing-dot"></div><div class="cl-typing-dot"></div><div class="cl-typing-dot"></div>';
    loader.appendChild(dotsWrap);
    var typingText = document.createElement('span');
    typingText.className = 'cl-typing-text';
    typingText.textContent = 'Searching meetings...';
    loader.appendChild(typingText);
    messages.appendChild(loader);
    scrollToBottom();

    // Rotate typing text
    var phrases = ['Searching meetings...', 'Analyzing transcripts...', 'Preparing answer...'];
    var phraseIdx = 0;
    loader._interval = setInterval(function () {
      phraseIdx = (phraseIdx + 1) % phrases.length;
      typingText.textContent = phrases[phraseIdx];
    }, 2200);
  }

  function hideTyping() {
    var loader = shadow.getElementById('cl-loader');
    if (loader) {
      if (loader._interval) clearInterval(loader._interval);
      if (loader.parentNode) loader.parentNode.removeChild(loader);
    }
  }

  function showError(msg) {
    var el = document.createElement('div');
    el.className = 'cl-error';
    el.setAttribute('role', 'alert');
    el.textContent = msg;
    messages.appendChild(el);
    scrollToBottom();
  }

  // ---------------------------------------------------------------------------
  // API call
  // ---------------------------------------------------------------------------
  function sendQuestion(text) {
    if (isLoading) return;
    isLoading = true;
    sendBtn.disabled = true;
    textarea.disabled = true;

    addUserBubble(text);

    // Add to conversation history
    conversationHistory.push({ role: 'user', content: text });

    showTyping();

    var endpoint = CONFIG.apiUrl + '/api/v1/widget/ask';
    var body;

    // Use chat endpoint for multi-turn, ask for first message
    if (conversationHistory.length > 1) {
      endpoint = CONFIG.apiUrl + '/api/v1/widget/chat';
      body = JSON.stringify({ messages: conversationHistory });
    } else {
      body = JSON.stringify({ question: text });
    }

    var xhr = new XMLHttpRequest();
    xhr.open('POST', endpoint, true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.setRequestHeader('X-API-Key', CONFIG.apiKey);
    xhr.timeout = 60000;

    xhr.onload = function () {
      hideTyping();
      isLoading = false;
      sendBtn.disabled = false;
      textarea.disabled = false;
      textarea.focus();

      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          var data = JSON.parse(xhr.responseText);
          var answer = data.answer || data.response || 'No answer returned.';
          conversationHistory.push({ role: 'assistant', content: answer });
          addBotBubble(simpleMarkdown(answer), data.sources || []);
        } catch (e) {
          showError('Failed to parse response.');
        }
      } else if (xhr.status === 401) {
        showError('Invalid API key. Please check your widget configuration.');
      } else if (xhr.status === 429) {
        showError('Rate limit reached. Please try again later.');
      } else {
        showError('Something went wrong (error ' + xhr.status + '). Please try again.');
      }
    };

    xhr.onerror = function () {
      hideTyping();
      isLoading = false;
      sendBtn.disabled = false;
      textarea.disabled = false;
      showError('Network error. Please check your connection.');
    };

    xhr.ontimeout = function () {
      hideTyping();
      isLoading = false;
      sendBtn.disabled = false;
      textarea.disabled = false;
      showError('Request timed out. Please try again.');
    };

    xhr.send(body);
  }

  // ---------------------------------------------------------------------------
  // Event handlers
  // ---------------------------------------------------------------------------

  // Toggle panel
  function openPanel() {
    panel.setAttribute('data-open', 'true');
    fab.setAttribute('aria-expanded', 'true');
    fab.setAttribute('aria-label', 'Close Meeting Q&A');
    textarea.focus();
  }

  function closePanel() {
    panel.setAttribute('data-open', 'false');
    fab.setAttribute('aria-expanded', 'false');
    fab.setAttribute('aria-label', 'Open Meeting Q&A');
    fab.focus();
  }

  function togglePanel() {
    var isOpen = panel.getAttribute('data-open') === 'true';
    if (isOpen) {
      closePanel();
    } else {
      openPanel();
    }
  }

  fab.addEventListener('click', togglePanel);
  closeBtn.addEventListener('click', function () { closePanel(); });

  // Suggested question clicks
  suggestionsContainer.addEventListener('click', function (e) {
    var btn = e.target.closest('.cl-suggestion');
    if (btn && !isLoading) {
      sendQuestion(btn.textContent.trim());
    }
  });

  // Close on Escape (works from anywhere in the shadow DOM)
  shadow.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel.getAttribute('data-open') === 'true') {
      e.preventDefault();
      closePanel();
    }
  });

  // Also close if Escape is pressed on the host document and panel is open
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel.getAttribute('data-open') === 'true') {
      closePanel();
    }
  });

  // Send on button click
  sendBtn.addEventListener('click', function () {
    var text = textarea.value.trim();
    if (text) {
      textarea.value = '';
      textarea.style.height = 'auto';
      sendQuestion(text);
    }
  });

  // Send on Enter (shift+enter for newline)
  textarea.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      var text = textarea.value.trim();
      if (text && !isLoading) {
        textarea.value = '';
        textarea.style.height = 'auto';
        sendQuestion(text);
      }
    }
  });

  // Enable/disable send button
  textarea.addEventListener('input', function () {
    sendBtn.disabled = !textarea.value.trim() || isLoading;
    // Auto-resize textarea
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 100) + 'px';
  });

  // Trap focus within panel when open (accessibility)
  panel.addEventListener('keydown', function (e) {
    if (e.key !== 'Tab') return;
    var focusable = panel.querySelectorAll('button:not([disabled]), textarea:not([disabled]), a[href]');
    if (focusable.length === 0) return;
    var first = focusable[0];
    var last = focusable[focusable.length - 1];
    if (e.shiftKey && shadow.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && shadow.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });

})();
