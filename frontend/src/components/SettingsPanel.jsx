import { useState, useEffect, useRef } from 'react'
import axios from 'axios'

function SettingsPanel({ clientId }) {
    const [language, setLanguage] = useState('English')
    const [saving, setSaving] = useState(false)
    const [exporting, setExporting] = useState(false)
    const [importing, setImporting] = useState(false)
    const [importResult, setImportResult] = useState(null)
    const [loaded, setLoaded] = useState(false)
    const fileInputRef = useRef(null)

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

    const handleImportSession = async (e) => {
        const file = e.target.files?.[0]
        if (!file || !clientId || importing) return

        setImporting(true)
        setImportResult(null)

        try {
            const formData = new FormData()
            formData.append('file', file)

            const res = await axios.post(
                `/api/settings/import?client_id=${encodeURIComponent(clientId)}`,
                formData,
                { headers: { 'Content-Type': 'multipart/form-data' } }
            )

            setImportResult({
                success: true,
                posts: res.data.posts_imported,
                replies: res.data.replies_imported
            })

            setTimeout(() => window.location.reload(), 1500)
        } catch (err) {
            const msg = err.response?.data?.detail || err.message || 'Unknown error'
            setImportResult({ success: false, error: msg })
        } finally {
            setImporting(false)
            if (fileInputRef.current) fileInputRef.current.value = ''
        }
    }

    const handleExportSession = async () => {
        if (!clientId || exporting) return

        setExporting(true)
        try {
            const res = await axios.get(`/api/settings/export?client_id=${encodeURIComponent(clientId)}`, {
                responseType: 'blob'
            })

            const blob = new Blob([res.data], { type: 'application/json' })
            const contentDisposition = res.headers['content-disposition'] || ''
            const match = contentDisposition.match(/filename="(.+)"/)
            const filename = match?.[1] || `treadstone-session-${clientId}.json`

            const url = window.URL.createObjectURL(blob)
            const link = document.createElement('a')
            link.href = url
            link.download = filename
            document.body.appendChild(link)
            link.click()
            link.remove()
            window.URL.revokeObjectURL(url)
        } catch (err) {
            console.error('Failed to export session:', err)
        } finally {
            setExporting(false)
        }
    }

    if (!loaded) return null

    return (
        <div className="flex-1 overflow-auto bg-gray-50 p-8">
            <div className="max-w-2xl mx-auto">
                <h2 className="text-2xl font-bold text-gray-800 mb-1">Settings</h2>
                <p className="text-sm text-gray-500 mb-6">Manage your preferences.</p>

                {/* Language Setting */}
                <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    <div className="px-6 py-4 border-b border-gray-100">
                        <h3 className="text-base font-semibold text-gray-700 flex items-center gap-2">
                            🌐 Language / 언어
                        </h3>
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

                <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden mt-6">
                    <div className="px-6 py-4 border-b border-gray-100">
                        <h3 className="text-base font-semibold text-gray-700 flex items-center gap-2">
                            Export Session
                        </h3>
                        <p className="text-sm text-gray-400 mt-1">
                            Download this session as JSON while preserving the post and reply structure.
                        </p>
                    </div>

                    <div className="px-6 py-5">
                        <button
                            onClick={handleExportSession}
                            disabled={exporting}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                                exporting
                                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                                    : 'bg-blue-500 text-white hover:bg-blue-600'
                            }`}
                        >
                            {exporting ? 'Exporting...' : 'Download session JSON'}
                        </button>
                    </div>
                </div>

                <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden mt-6">
                    <div className="px-6 py-4 border-b border-gray-100">
                        <h3 className="text-base font-semibold text-gray-700 flex items-center gap-2">
                            📂 Import Session
                        </h3>
                        <p className="text-sm text-gray-400 mt-1">
                            Upload a previously exported Treadstone session JSON to replay it in the interface.
                            This replaces the current session.
                        </p>
                    </div>

                    <div className="px-6 py-5">
                        <input
                            type="file"
                            ref={fileInputRef}
                            accept=".json"
                            className="hidden"
                            onChange={handleImportSession}
                        />
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            disabled={importing}
                            className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                                importing
                                    ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                                    : 'bg-emerald-500 text-white hover:bg-emerald-600'
                            }`}
                        >
                            {importing ? 'Importing...' : 'Upload session JSON'}
                        </button>

                        {importResult && (
                            <div className={`mt-3 px-4 py-2.5 rounded-lg text-sm ${
                                importResult.success
                                    ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                                    : 'bg-red-50 text-red-700 border border-red-200'
                            }`}>
                                {importResult.success
                                    ? `Imported ${importResult.posts} posts and ${importResult.replies} replies. Reloading...`
                                    : `Import failed: ${importResult.error}`
                                }
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )
}

export default SettingsPanel
