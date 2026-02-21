import { useState, useCallback, useRef } from "react";

const OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions";

const MODELS = [
  { id: "openai/chatgpt-4o-latest", label: "GPT-4o" },
  { id: "google/gemini-2.0-flash-exp:free", label: "Gemini Flash (Free)" },
  { id: "meta-llama/llama-3.3-8b-instruct:free", label: "Llama 3.3 (Free)" },
  { id: "qwen/qwen2.5-vl-32b-instruct:free", label: "Qwen 2.5 VL (Free)" },
];

const PROMPT = `You are analyzing a handwritten museum tour note. Extract ALL information from this form and return ONLY valid JSON with no markdown formatting, no backticks, no preamble.

The form has these fields: Date, Time, Group, Public/Private, Age/Grade Level, Number in group, Tour Topic, Docent(s), Comments, One thing you learned.

Return JSON in this EXACT format:
{
  "fields": {
    "Date": {"value": "", "confidence": 1.0},
    "Day of Week": {"value": "", "confidence": 1.0},
    "Tour Type": {"value": "", "confidence": 1.0},
    "Tour Time": {"value": "", "confidence": 1.0},
    "# Attended": {"value": "", "confidence": 1.0},
    "Age/Grade Level": {"value": "", "confidence": 1.0},
    "Region From": {"value": "", "confidence": 1.0},
    "Tour Topic": {"value": "", "confidence": 1.0},
    "Docent": {"value": "", "confidence": 1.0},
    "Comments": {"value": "", "confidence": 1.0}
  }
}

confidence is a float 0.0-1.0 representing how confident you are in the transcription. Use lower confidence for:
- Illegible or ambiguous handwriting (0.3-0.5)
- Partially readable words (0.5-0.7)
- Readable but uncertain characters like numbers that could be misread (0.7-0.9)
- Clear and unambiguous text (0.9-1.0)
- Empty/blank fields should have confidence 1.0 with empty string value

For "Tour Type": use "Public 3 in 30" or "Public Drop In" or whatever is indicated.
For "Day of Week": determine from date if possible.
For "Region From": this is the Group field.
For "# Attended": this is Number in group.
Combine Comments and "One thing you learned" into Comments.

Return ONLY the JSON object. No other text.`;

function confidenceColor(c) {
  if (c >= 0.9) return "#22c55e";
  if (c >= 0.7) return "#eab308";
  if (c >= 0.5) return "#f97316";
  return "#ef4444";
}

function confidenceLabel(c) {
  if (c >= 0.9) return "High";
  if (c >= 0.7) return "Medium";
  if (c >= 0.5) return "Low";
  return "Very Low";
}

function ConfidenceBadge({ confidence }) {
  const color = confidenceColor(confidence);
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        fontWeight: 600,
        color,
        background: `${color}18`,
        border: `1px solid ${color}40`,
        borderRadius: 4,
        padding: "1px 6px",
        fontFamily: "'JetBrains Mono', monospace",
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          display: "inline-block",
        }}
      />
      {Math.round(confidence * 100)}%
    </span>
  );
}

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function getMimeType(file) {
  if (file.type) return file.type;
  const ext = file.name.split(".").pop().toLowerCase();
  const map = { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", heic: "image/heic" };
  return map[ext] || "image/jpeg";
}

async function processImage(file, apiKey, model) {
  const base64 = await fileToBase64(file);
  const mime = getMimeType(file);

  const res = await fetch(OPENROUTER_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages: [
        {
          role: "user",
          content: [
            { type: "text", text: PROMPT },
            { type: "image_url", image_url: { url: `data:${mime};base64,${base64}` } },
          ],
        },
      ],
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(`API ${res.status}: ${err?.error?.message || res.statusText}`);
  }

  const data = await res.json();
  let text = data.choices?.[0]?.message?.content || "";
  text = text.trim();
  if (text.includes("```json")) text = text.split("```json")[1].split("```")[0];
  else if (text.includes("```")) text = text.split("```")[1].split("```")[0];

  return JSON.parse(text.trim());
}

function resultsToCSV(results) {
  const fields = ["Date", "Day of Week", "Tour Type", "Tour Time", "# Attended", "Age/Grade Level", "Region From", "Tour Topic", "Docent", "Comments"];
  const header = fields.join(",");
  const rows = results.map((r) => {
    return fields
      .map((f) => {
        const val = r.fields?.[f]?.value || "";
        return `"${val.replace(/"/g, '""')}"`;
      })
      .join(",");
  });
  return header + "\n" + rows.join("\n");
}

function downloadCSV(results, filename = "tour_notes.csv") {
  const csv = resultsToCSV(results);
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

const FIELD_ORDER = ["Date", "Day of Week", "Tour Type", "Tour Time", "# Attended", "Age/Grade Level", "Region From", "Tour Topic", "Docent", "Comments"];

export default function App() {
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState(MODELS[0].id);
  const [files, setFiles] = useState([]);
  const [results, setResults] = useState([]);
  const [processing, setProcessing] = useState(false);
  const [currentFile, setCurrentFile] = useState("");
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [error, setError] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [selectedResult, setSelectedResult] = useState(null);
  const inputRef = useRef();

  const handleFiles = useCallback((fileList) => {
    const imageExts = ["jpg", "jpeg", "png", "webp", "heic", "heif"];
    const images = Array.from(fileList).filter((f) => {
      const ext = f.name.split(".").pop().toLowerCase();
      return imageExts.includes(ext) || f.type.startsWith("image/");
    });
    setFiles(images);
    setResults([]);
    setError("");
    setSelectedResult(null);
  }, []);

  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragOver(false);
      if (e.dataTransfer.items) {
        const allFiles = [];
        const traverse = async (entry) => {
          if (entry.isFile) {
            return new Promise((res) => entry.file((f) => { allFiles.push(f); res(); }));
          } else if (entry.isDirectory) {
            const reader = entry.createReader();
            const entries = await new Promise((res) => reader.readEntries(res));
            for (const e of entries) await traverse(e);
          }
        };
        const entries = Array.from(e.dataTransfer.items)
          .map((i) => i.webkitGetAsEntry?.())
          .filter(Boolean);
        Promise.all(entries.map(traverse)).then(() => handleFiles(allFiles));
      } else {
        handleFiles(e.dataTransfer.files);
      }
    },
    [handleFiles]
  );

  const run = async () => {
    if (!apiKey) { setError("Enter your OpenRouter API key."); return; }
    if (!files.length) { setError("Drop some images first."); return; }
    setError("");
    setProcessing(true);
    setResults([]);
    setSelectedResult(null);
    setProgress({ done: 0, total: files.length });

    const allResults = [];
    for (let i = 0; i < files.length; i++) {
      setCurrentFile(files[i].name);
      setProgress({ done: i, total: files.length });
      try {
        const result = await processImage(files[i], apiKey, model);
        result._source_file = files[i].name;
        result._preview = URL.createObjectURL(files[i]);
        allResults.push(result);
      } catch (e) {
        allResults.push({
          fields: FIELD_ORDER.reduce((acc, f) => { acc[f] = { value: "", confidence: 0 }; return acc; }, {}),
          _source_file: files[i].name,
          _error: e.message,
          _preview: URL.createObjectURL(files[i]),
        });
      }
      setResults([...allResults]);
    }
    setProgress({ done: files.length, total: files.length });
    setProcessing(false);
    setCurrentFile("");
    if (allResults.length === 1) setSelectedResult(0);
  };

  const avgConfidence = (result) => {
    const vals = Object.values(result.fields || {}).filter((f) => f.value).map((f) => f.confidence);
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0c0c0f",
        color: "#e8e6e1",
        fontFamily: "'DM Sans', 'Helvetica Neue', sans-serif",
      }}
    >
      <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Instrument+Serif&display=swap" rel="stylesheet" />

      <header style={{ borderBottom: "1px solid #1e1e24", padding: "20px 32px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontFamily: "'Instrument Serif', serif", fontWeight: 400, letterSpacing: "-0.01em" }}>
            Tour Note Digitizer
          </h1>
          <span style={{ fontSize: 12, color: "#666", fontFamily: "'JetBrains Mono', monospace" }}>v1.0</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            style={{
              background: "#16161a",
              color: "#ccc",
              border: "1px solid #2a2a30",
              borderRadius: 6,
              padding: "6px 10px",
              fontSize: 13,
              fontFamily: "'JetBrains Mono', monospace",
              cursor: "pointer",
            }}
          >
            {MODELS.map((m) => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <input
            type="password"
            placeholder="OpenRouter API Key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            style={{
              background: "#16161a",
              color: "#ccc",
              border: "1px solid #2a2a30",
              borderRadius: 6,
              padding: "6px 12px",
              fontSize: 13,
              width: 220,
              fontFamily: "'JetBrains Mono', monospace",
            }}
          />
        </div>
      </header>

      <main style={{ maxWidth: 1100, margin: "0 auto", padding: "32px 24px" }}>
        {/* Drop Zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          style={{
            border: `2px dashed ${dragOver ? "#8b5cf6" : "#2a2a30"}`,
            borderRadius: 12,
            padding: files.length ? "20px 24px" : "60px 24px",
            textAlign: "center",
            cursor: "pointer",
            transition: "all 0.2s",
            background: dragOver ? "#8b5cf608" : "#12121a",
            marginBottom: 24,
          }}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept="image/*,.heic,.heif"
            style={{ display: "none" }}
            onChange={(e) => handleFiles(e.target.files)}
          />
          {files.length === 0 ? (
            <>
              <div style={{ fontSize: 40, marginBottom: 12, opacity: 0.4 }}>⬆</div>
              <div style={{ fontSize: 15, color: "#888" }}>
                Drop images or a folder here, or click to browse
              </div>
              <div style={{ fontSize: 12, color: "#555", marginTop: 6 }}>
                JPG, PNG, WebP, HEIC supported
              </div>
            </>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 14, color: "#aaa" }}>
                {files.length} image{files.length !== 1 ? "s" : ""} ready
              </span>
              <button
                onClick={(e) => { e.stopPropagation(); run(); }}
                disabled={processing}
                style={{
                  background: processing ? "#333" : "#8b5cf6",
                  color: "#fff",
                  border: "none",
                  borderRadius: 8,
                  padding: "10px 28px",
                  fontSize: 14,
                  fontWeight: 600,
                  cursor: processing ? "default" : "pointer",
                  opacity: processing ? 0.6 : 1,
                  fontFamily: "'DM Sans', sans-serif",
                }}
              >
                {processing ? `Processing ${progress.done + 1}/${progress.total}…` : "Process"}
              </button>
            </div>
          )}
        </div>

        {error && (
          <div style={{ background: "#2a1015", border: "1px solid #5c2030", borderRadius: 8, padding: "10px 16px", fontSize: 13, color: "#f87171", marginBottom: 20 }}>
            {error}
          </div>
        )}

        {processing && currentFile && (
          <div style={{ marginBottom: 20 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, color: "#666", marginBottom: 6, fontFamily: "'JetBrains Mono', monospace" }}>
              <span>{currentFile}</span>
              <span>{progress.done}/{progress.total}</span>
            </div>
            <div style={{ background: "#1a1a22", borderRadius: 4, height: 4, overflow: "hidden" }}>
              <div style={{ background: "#8b5cf6", height: "100%", width: `${(progress.done / progress.total) * 100}%`, transition: "width 0.3s", borderRadius: 4 }} />
            </div>
          </div>
        )}

        {/* Results */}
        {results.length > 0 && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h2 style={{ margin: 0, fontSize: 16, fontWeight: 500, color: "#aaa" }}>
                Results ({results.length})
              </h2>
              <button
                onClick={() => downloadCSV(results)}
                style={{
                  background: "#1a1a22",
                  color: "#8b5cf6",
                  border: "1px solid #2a2a30",
                  borderRadius: 6,
                  padding: "6px 16px",
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: "pointer",
                  fontFamily: "'DM Sans', sans-serif",
                }}
              >
                ↓ Download CSV
              </button>
            </div>

            {/* Single result detail view */}
            {selectedResult !== null && results[selectedResult] && (
              <div style={{ background: "#14141c", border: "1px solid #222230", borderRadius: 10, marginBottom: 20, overflow: "hidden" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 16px", borderBottom: "1px solid #1e1e28" }}>
                  <span style={{ fontSize: 13, fontFamily: "'JetBrains Mono', monospace", color: "#888" }}>
                    {results[selectedResult]._source_file}
                  </span>
                  <button onClick={() => setSelectedResult(null)} style={{ background: "none", border: "none", color: "#666", cursor: "pointer", fontSize: 18 }}>×</button>
                </div>
                <div style={{ display: "flex", gap: 0 }}>
                  {results[selectedResult]._preview && (
                    <div style={{ width: 280, minHeight: 200, borderRight: "1px solid #1e1e28", display: "flex", alignItems: "center", justifyContent: "center", padding: 12, background: "#0e0e14" }}>
                      <img src={results[selectedResult]._preview} alt="" style={{ maxWidth: "100%", maxHeight: 360, borderRadius: 6 }} />
                    </div>
                  )}
                  <div style={{ flex: 1, padding: "12px 20px" }}>
                    {results[selectedResult]._error ? (
                      <div style={{ color: "#f87171", fontSize: 13 }}>Error: {results[selectedResult]._error}</div>
                    ) : (
                      <table style={{ width: "100%", borderCollapse: "collapse" }}>
                        <tbody>
                          {FIELD_ORDER.map((field) => {
                            const f = results[selectedResult].fields?.[field];
                            if (!f) return null;
                            const isLow = f.value && f.confidence < 0.7;
                            return (
                              <tr key={field} style={{ borderBottom: "1px solid #1a1a24" }}>
                                <td style={{ padding: "8px 8px 8px 0", fontSize: 12, color: "#666", fontWeight: 600, width: 130, verticalAlign: "top", fontFamily: "'JetBrains Mono', monospace" }}>
                                  {field}
                                </td>
                                <td style={{ padding: "8px 0", fontSize: 14, color: isLow ? confidenceColor(f.confidence) : "#ddd", background: isLow ? `${confidenceColor(f.confidence)}08` : "transparent" }}>
                                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                    <span>{f.value || "—"}</span>
                                    {f.value && <ConfidenceBadge confidence={f.confidence} />}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Result cards grid */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
              {results.map((r, i) => {
                const avg = avgConfidence(r);
                return (
                  <div
                    key={i}
                    onClick={() => setSelectedResult(i)}
                    style={{
                      background: selectedResult === i ? "#1a1a28" : "#14141c",
                      border: `1px solid ${selectedResult === i ? "#8b5cf640" : "#222230"}`,
                      borderRadius: 8,
                      padding: 14,
                      cursor: "pointer",
                      transition: "all 0.15s",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                      <span style={{ fontSize: 12, fontFamily: "'JetBrains Mono', monospace", color: "#777", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "70%" }}>
                        {r._source_file}
                      </span>
                      {r._error ? (
                        <span style={{ fontSize: 11, color: "#f87171", fontWeight: 600 }}>Error</span>
                      ) : (
                        <ConfidenceBadge confidence={avg} />
                      )}
                    </div>
                    {!r._error && (
                      <div style={{ fontSize: 13, color: "#aaa", lineHeight: 1.5 }}>
                        <span style={{ color: "#ddd" }}>{r.fields?.Date?.value || "No date"}</span>
                        {r.fields?.["Tour Topic"]?.value && <span> · {r.fields["Tour Topic"].value}</span>}
                        {r.fields?.Docent?.value && <span style={{ display: "block", fontSize: 12, color: "#666", marginTop: 2 }}>Docent: {r.fields.Docent.value}</span>}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
      </main>
    </div>
  );
}