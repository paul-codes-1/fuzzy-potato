import { useI18n } from '../i18n/I18nProvider'

export default function ModelSelector({ value, onChange, disabled }) {
  const { t } = useI18n()
  return (
    <div className="chat-model-selector">
      <label htmlFor="model-select">{t('chatMeetings.model')}</label>
      <select
        id="model-select"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
      >
        <option value="openai">GPT-4o</option>
        <option value="anthropic">Claude Sonnet</option>
      </select>
    </div>
  )
}