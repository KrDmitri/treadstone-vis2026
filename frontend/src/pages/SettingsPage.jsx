import { useState, useEffect } from 'react'
import axios from 'axios'
import { getClientId } from '../utils/clientId'

function SettingsPage() {
  const [language, setLanguage] = useState('English')
  const [saving, setSaving] = useState(false)
  const [loaded, setLoaded] = useState(false)

  const clientId = getClientId()

  // Load current settings on mount
  useEffect(() => {
    const loadSettings = async () => {
      try {
        const res = await axios.get(`/api/settings?client_id=${encodeURIComponent(clientId)}`)
        setLanguage(res.data.language || 'English')
      } catch (err) {
        console.error('Failed to load settings:', err)
      } finally {
        setLoaded(true)
      }
    }
    loadSettings()
  }, [clientId])

  const handleLanguageChange = async (newLang) => {
    setLanguage(newLang)
    setSaving(true)
    try {
      await axios.put(`/api/settings?client_id=${encodeURIComponent(clientId)}`, {
        language: newLang
      })
    } catch (err) {
      console.error('Failed to save settings:', err)
    } finally {
      setTimeout(() => setSaving(false), 500)
    }
  }

  if (!loaded) return null

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-2xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-800 mb-2">Settings</h1>
        <p className="text-gray-500 mb-8">Manage your preferences.</p>

        {/* Language Setting */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100">
            <h2 className="text-lg font-semibold text-gray-700 flex items-center gap-2">
              🌐 Language / 언어
            </h2>
            <p className="text-sm text-gray-400 mt-1">
              Choose the language for agent responses, suggestions, and recommendations.
            </p>
          </div>

          <div className="px-6 py-5 flex flex-col gap-3">
            {[
              { value: 'Auto', label: 'Auto-detect', desc: 'Automatically matches the language of your message' },
              { value: 'English', label: 'English', desc: 'Agents always respond in English' },
              { value: 'Korean', label: '한국어', desc: '에이전트가 항상 한국어로 응답합니다' },
            ].map((opt) => (
              <button
                key={opt.value}
                onClick={() => handleLanguageChange(opt.value)}
                className={`flex items-center gap-4 px-4 py-3 rounded-lg border-2 transition-all text-left ${language === opt.value
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                  }`}
              >
                <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${language === opt.value ? 'border-blue-500' : 'border-gray-300'
                  }`}>
                  {language === opt.value && (
                    <div className="w-2.5 h-2.5 rounded-full bg-blue-500" />
                  )}
                </div>
                <div>
                  <div className="font-medium text-gray-800">{opt.label}</div>
                  <div className="text-sm text-gray-400">{opt.desc}</div>
                </div>
              </button>
            ))}
          </div>

          {saving && (
            <div className="px-6 py-2 text-sm text-blue-500 bg-blue-50 border-t border-blue-100">
              ✓ Saved
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default SettingsPage
