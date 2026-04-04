export default function ModelSelector({ value, onChange, disabled }) {
  return (
    <div className="chat-model-selector">
      <label htmlFor="model-select">Model:</label>
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