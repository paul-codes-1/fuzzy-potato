import { useI18n } from '../i18n/I18nProvider'

/**
 * Compact language switcher for header placement.
 * Shows flag + native language name. Persists choice to localStorage.
 */
export default function LanguageSwitcher() {
  const { locale, setLocale, locales } = useI18n()
  const current = locales[locale] || locales.en

  return (
    <div className="language-switcher">
      <select
        className="language-select"
        value={locale}
        onChange={(e) => setLocale(e.target.value)}
        aria-label="Select language"
      >
        {Object.entries(locales).map(([code, info]) => (
          <option key={code} value={code}>
            {info.flag} {info.nativeName}
          </option>
        ))}
      </select>
      <span className="language-current" aria-hidden="true">
        {current.flag}
      </span>
    </div>
  )
}
