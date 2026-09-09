"use client";
import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

interface Submission {
  id: string;
  studentName: string | null;
  rollNumber: string | null;
  status: string;
  score: number | null;
  imageUrl: string;
  error?: string;
  originalName?: string;
}

const MAX_SHEETS = 100;

export default function ScanPage() {
  const { id } = useParams() as { id:string };
  const [exam, setExam] = useState<any>(null);
  const [subs, setSubs] = useState<Submission[]>([]);
  const [previews, setPreviews] = useState<{ file: File; url: string }[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [llmConfig, setLlmConfig] = useState<{mode:string, model?:string, message?:string} | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  const fetchData = async () => {
    const [eRes, sRes] = await Promise.all([fetch(`/api/exams/${id}`), fetch(`/api/exams/${id}/submissions`)]);
    const e = await eRes.json(); setExam(e);
    const s = await sRes.json(); setSubs(Array.isArray(s)?s:[]);
  };

  useEffect(()=>{
    fetchData();
    fetch("/api/config").then(r=>r.json()).then(setLlmConfig).catch(()=>{});
    const iv = setInterval(fetchData, 2000);
    return ()=> clearInterval(iv);
  },[id]);

  const [dragActive, setDragActive] = useState(false);
  const dragCounterRef = useRef(0);

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCounterRef.current++;
    if (e.dataTransfer?.types?.includes("Files")) setDragActive(true);
  };
  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    if (e.dataTransfer?.types?.includes("Files")) setDragActive(true);
  };
  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setDragActive(false);
  };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation();
    dragCounterRef.current = 0;
    setDragActive(false);
    if (subs.length >= MAX_SHEETS) { alert(`Maximum ${MAX_SHEETS} sheets reached`); return; }
    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      addFiles(files);
      // also check if we exceed remaining
      if (subs.length + files.length > MAX_SHEETS) {
        // addFiles will trim and alert; extra guard not needed
      }
    }
  };

  const addFiles = (files: FileList | File[] | null) => {
    if (!files) return;
    const arr = Array.from(files as any) as File[];
    // allow images (including HEIC on iPhone), pdf
    const valid = arr.filter(f => {
      const name = f.name.toLowerCase();
      return f.type.startsWith("image/") || f.type === "application/pdf" || /\.(jpg|jpeg|png|pdf|heic|heif|webp)$/i.test(name);
    });
    if (valid.length === 0 && arr.length > 0) {
      // Fallback: accept any file if filter was too strict (e.g. unknown mime on mobile)
      arr.forEach(f => valid.push(f));
    }
    const withUrls = valid.map(f => {
      const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
      const isHeic = f.name.toLowerCase().endsWith(".heic") || f.name.toLowerCase().endsWith(".heif") || f.type === "image/heic" || f.type === "image/heif";
      // HEIC may not preview in browser — show placeholder
      if (isPdf || isHeic) return { file: f, url: "" };
      try { return { file: f, url: URL.createObjectURL(f) }; } catch { return { file: f, url: "" }; }
    });
    setPreviews(prev => {
      const combined = [...prev, ...withUrls];
      if (combined.length > 50) {
        alert("You can batch up to 50 files at once. Extra files trimmed.");
        return combined.slice(0, 50);
      }
      return combined;
    });
  };

  const removePreview = (idx: number) => {
    setPreviews(prev => {
      const copy = [...prev];
      const removed = copy.splice(idx, 1)[0];
      if (removed?.url) URL.revokeObjectURL(removed.url);
      return copy;
    });
  };

  const clearAll = () => {
    previews.forEach(p => p.url && URL.revokeObjectURL(p.url));
    setPreviews([]);
    if (fileInputRef.current) fileInputRef.current.value="";
    if (cameraInputRef.current) cameraInputRef.current.value="";
  };

  const [updatingSheetType, setUpdatingSheetType] = useState(false);
  const updateSheetType = async (newType: string) => {
    setUpdatingSheetType(true);
    try {
      const res = await fetch(`/api/exams/${id}`, { method:"PATCH", headers:{ "Content-Type":"application/json"}, body: JSON.stringify({ sheetType: newType })});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error || "Failed");
      setExam(data);
    } catch(e:any){ alert(e.message); }
    finally { setUpdatingSheetType(false); }
  };

  const submitBatch = async () => {
    if (previews.length === 0) return;
    if (subs.length >= MAX_SHEETS) { alert(`Maximum ${MAX_SHEETS} sheets reached`); return; }
    if (subs.length + previews.length > MAX_SHEETS) {
      alert(`Only ${MAX_SHEETS - subs.length} slots remaining (you selected ${previews.length})`);
      return;
    }
    setUploading(true);
    setUploadProgress(`Uploading ${previews.length} sheet${previews.length>1?'s':''}...`);
    const fd = new FormData();
    previews.forEach(p => fd.append("file", p.file));
    // Pass exam sheetType so vision uses correct prompt (bubble / handwritten / auto)
    if (exam?.sheetType) fd.append("sheetType", exam.sheetType);
    try{
      const res = await fetch(`/api/exams/${id}/submissions`, {method:"POST", body: fd});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error);
      clearAll();
      await fetchData();
      setUploadProgress(`✓ Uploaded ${Array.isArray(data)?data.length:1} sheet${(Array.isArray(data)?data.length:1)>1?'s':''} — processing in background`);
      setTimeout(()=> setUploadProgress(""), 3000);
    }catch(e:any){ alert(e.message); setUploadProgress(""); }
    finally{ setUploading(false)}
  };

  if(!exam) return <div className="p-8 text-center text-sm text-slate-500">Loading...</div>;

  const remaining = MAX_SHEETS - subs.length;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-3 sm:px-4 py-3 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="font-bold text-sm text-slate-900 truncate">{exam.name}</p>
            {(() => {
              const max = exam.markingScheme ? exam.markingScheme.reduce((s:number,a:any)=> s + (a.to-a.from+1)*a.marks,0) : exam.questionCount * exam.marksPerQuestion;
              const schemeShort = exam.markingScheme && exam.markingScheme.length>1 ? exam.markingScheme.map((s:any)=>`Q${s.from}-${s.to} +${s.marks}/-${s.negativeMarks}`).join(" • ") : `${exam.marksPerQuestion}×${exam.questionCount}=${max}`;
              return <p className="text-xs text-slate-500 truncate">{exam.questionCount} Q • Max {max} • {schemeShort} • {subs.length}/{MAX_SHEETS} sheets</p>;
            })()}
            <div className="flex items-center gap-1.5 mt-1">
              <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium ${exam.sheetType==="handwritten"?"bg-amber-50 text-amber-700 border-amber-200": exam.sheetType==="auto"?"bg-indigo-50 text-indigo-700 border-indigo-200":"bg-slate-100 text-slate-600 border-slate-200"}`}>
                {exam.sheetType==="handwritten" ? "✍️ Handwritten list (1.a 2.b)" : exam.sheetType==="auto" ? "🔀 Auto (bubble + handwritten)" : "⭕ Bubble OMR"}
              </span>
              <select
                value={exam.sheetType || "bubble"}
                onChange={e=>updateSheetType(e.target.value)}
                disabled={updatingSheetType}
                className="text-[10px] border border-slate-200 rounded-full px-1.5 py-0.5 bg-white text-slate-600 disabled:opacity-50"
                title="Change sheet type (same grading, different Vision prompt)"
              >
                <option value="auto">Auto</option>
                <option value="bubble">Bubble</option>
                <option value="handwritten">Handwritten</option>
              </select>
            </div>
          </div>
          <Link href={`/exam/${id}/results`} className="text-xs bg-slate-900 hover:bg-black text-white px-4 py-2 rounded-full font-medium shrink-0 min-h-[36px] flex items-center">View Results</Link>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-3 sm:px-4 py-4 sm:py-6 space-y-4 sm:space-y-6">
        {llmConfig && (
          <div className={`rounded-xl px-4 py-3 text-xs border ${llmConfig.mode==="mock" ? "bg-amber-50 border-amber-200 text-amber-900" : "bg-emerald-50 border-emerald-200 text-emerald-900"}`}>
            <span className="font-bold">{llmConfig.mode==="mock" ? "⚠️ MOCK MODE — Demo data" : "✓ Live Vision LLM"}</span>
            <span className="ml-2">{llmConfig.message}</span>
            {llmConfig.mode==="mock" && <span className="block mt-1 text-amber-700">Your uploaded image is IGNORED — name/answers are random. Set <code className="bg-amber-100 px-1 rounded">OPENROUTER_API_KEY</code> + <code className="bg-amber-100 px-1 rounded">MOCK_VISION=false</code> in <code className="bg-amber-100 px-1 rounded">.env</code> and restart <code className="bg-amber-100 px-1 rounded">npm run dev</code>.</span>}
            {llmConfig.mode==="live" && llmConfig.model && <span className="block mt-1 opacity-80">Model: <code className="bg-emerald-100 px-1 rounded">{llmConfig.model}</code></span>}
          </div>
        )}

        {/* Progress — drag & drop enabled */}
        <div
          onDragEnter={handleDragEnter}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`relative bg-white rounded-2xl p-4 sm:p-5 border shadow-sm transition ${dragActive ? "border-indigo-400 ring-2 ring-indigo-300 bg-indigo-50/40" : "border-slate-200"}`}
        >
          {dragActive && (
            <div className="absolute inset-0 z-20 bg-indigo-500/10 backdrop-blur-[1px] rounded-2xl border-2 border-dashed border-indigo-400 flex flex-col items-center justify-center pointer-events-none">
              <span className="text-2xl">📥</span>
              <span className="text-sm font-semibold text-indigo-700 mt-1">Drop sheets here</span>
              <span className="text-xs text-indigo-600">JPG, PNG, PDF, HEIC — up to 50 at once</span>
            </div>
          )}
          <div className="flex justify-between items-center gap-3">
            <h2 className="font-semibold text-sm text-slate-900">Scan Student Sheets</h2>
            <span className={`text-xs px-2.5 py-1 rounded-full font-medium border ${remaining<10?"bg-amber-50 text-amber-700 border-amber-200":"bg-slate-100 text-slate-600 border-slate-200"}`}>{subs.length}/{MAX_SHEETS} uploaded • {remaining} left</span>
          </div>
          {exam.sheetType==="handwritten" && <p className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">✍️ Handwritten mode — students write <code className="bg-amber-100 px-1 rounded">1.a 2.b 3.c</code> / <code className="bg-amber-100 px-1 rounded">1:a 2:c</code> on plain paper. Same Vision pipeline, handwritten prompt. Upload photo of the list.</p>}
          {exam.sheetType==="auto" && <p className="mt-2 text-xs text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg px-3 py-2">🔀 Auto mode — bubble OMR <em>or</em> handwritten list (<code className="bg-indigo-100 px-1 rounded">1.a 2.b</code>) both work. Vision detects per photo.</p>}
          {exam.sheetType==="bubble" && <p className="mt-2 text-[11px] text-slate-500">⭕ Bubble OMR mode — fill circles. Switch to Handwritten above if students write <code className="bg-slate-100 px-1 rounded">1.a 2.b</code> on paper.</p>}

          {previews.length === 0 ? (
            <div className="mt-4 space-y-3">
              {/* Hidden inputs — triggered via buttons (label+hidden fails on iOS) */}
              <input ref={cameraInputRef} type="file" accept="image/*,image/heic,image/heif" capture="environment" className="hidden" tabIndex={-1} onChange={e=> { addFiles(e.target.files); e.currentTarget.value=""; }} />
              <input ref={fileInputRef} type="file" accept="image/*,image/heic,image/heif,application/pdf,.jpg,.jpeg,.png,.pdf,.heic,.heif,.webp" multiple className="hidden" tabIndex={-1} onChange={e=> { addFiles(e.target.files); e.currentTarget.value=""; }} />
              <div className="grid grid-cols-2 gap-3">
                <button type="button" onClick={()=> cameraInputRef.current?.click()} className="bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white rounded-xl py-5 sm:py-6 flex flex-col items-center justify-center cursor-pointer transition min-h-[88px] w-full">
                  <span className="text-lg">📷</span>
                  <span className="text-sm font-medium mt-1">Take Photo</span>
                  <span className="text-[11px] opacity-80">Camera</span>
                </button>
                <button type="button" onClick={()=> fileInputRef.current?.click()} className="border-2 border-dashed border-slate-300 hover:border-indigo-300 rounded-xl py-5 sm:py-6 flex flex-col items-center justify-center cursor-pointer hover:bg-slate-50 transition bg-white min-h-[88px] w-full">
                  <span className="text-lg">📁</span>
                  <span className="text-sm font-medium mt-1 text-slate-700">Upload Files</span>
                  <span className="text-[11px] text-slate-500">JPG PNG PDF HEIC — batch</span>
                </button>
              </div>

              {/* Desktop big drop zone — also a real drop target (outer card handles drops, this is visual cue) */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="hidden sm:flex border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-xl py-6 flex-col items-center justify-center cursor-pointer hover:bg-indigo-50/30 transition bg-slate-50/50 w-full"
              >
                <span className="text-sm font-medium text-slate-600">Drag & drop sheets here or click to browse</span>
                <span className="text-xs text-slate-400 mt-1">Up to 50 at once — bubble OMR or handwritten list (1.a 2.b) — background processing</span>
              </button>

              <p className="text-xs text-slate-400 text-center leading-relaxed">Batch supported — select many sheets at once. Each is processed in background; you don&apos;t need to wait.</p>
              <p className="text-[11px] text-amber-600 text-center bg-amber-50 border border-amber-200 rounded-lg py-2 px-3 sm:hidden">If picker doesn&apos;t open, use Upload Files → allow camera/gallery permission.</p>
            </div>
          ) : (
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-700">{previews.length} sheet{previews.length>1?'s':''} selected</p>
                <button onClick={clearAll} className="text-xs text-slate-500 hover:text-slate-700 underline">Clear all</button>
              </div>
              <p className="text-[11px] text-slate-500 bg-slate-50 border border-dashed border-slate-200 rounded-lg px-3 py-2 text-center">
                You can drag & drop more sheets anywhere on this card — or tap <span className="font-medium">Add more</span>
              </p>

              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 sm:gap-3 max-h-[50vh] overflow-y-auto pr-1">
                {previews.map((p, idx) => (
                  <div key={idx} className="border border-slate-200 rounded-xl overflow-hidden bg-slate-50 relative group">
                    {p.file.type === "application/pdf" || p.file.name.toLowerCase().endsWith(".pdf") || !p.url ? (
                      <div className="py-8 sm:py-10 text-center bg-slate-100">
                        <p className="text-lg">{p.file.name.toLowerCase().endsWith(".pdf") ? "📄" : "🖼️"}</p>
                        <p className="text-xs font-medium text-slate-700 px-2 truncate">{p.file.name}</p>
                        <p className="text-[11px] text-slate-400">{(p.file.size/1024).toFixed(0)} KB { !p.url ? "• preview N/A" : ""}</p>
                      </div>
                    ) : (
                      <img src={p.url} alt={`preview ${idx+1}`} className="w-full h-28 sm:h-32 object-cover" />
                    )}
                    <div className="px-2 py-1.5 bg-white border-t border-slate-100">
                      <p className="text-xs font-medium text-slate-700 truncate">{p.file.name}</p>
                      <p className="text-[11px] text-slate-400">{(p.file.size/1024).toFixed(0)} KB</p>
                    </div>
                    <button onClick={()=>removePreview(idx)} className="absolute top-1.5 right-1.5 w-7 h-7 bg-black/60 hover:bg-black/80 text-white rounded-full flex items-center justify-center text-xs backdrop-blur">✕</button>
                    <span className="absolute top-1.5 left-1.5 bg-white/90 backdrop-blur text-xs font-bold px-1.5 py-0.5 rounded-full border border-slate-200">#{idx+1}</span>
                  </div>
                ))}
                {/* Add more tile */}
                <button type="button" onClick={()=> fileInputRef.current?.click()} className="border-2 border-dashed border-slate-300 rounded-xl flex flex-col items-center justify-center cursor-pointer hover:bg-slate-50 min-h-[120px] sm:min-h-[140px] bg-white w-full">
                  <span className="text-xl text-slate-400">+</span>
                  <span className="text-xs text-slate-600 font-medium">Add more</span>
                </button>
              </div>

              {uploadProgress && <p className="text-xs text-center font-medium text-indigo-600 bg-indigo-50 border border-indigo-100 rounded-lg py-2">{uploadProgress}</p>}

              <div className="flex gap-2 sm:gap-3">
                <button onClick={clearAll} className="flex-1 border border-slate-200 bg-white hover:bg-slate-50 rounded-xl py-3 text-sm font-medium min-h-[44px]">Clear</button>
                <button onClick={submitBatch} disabled={uploading} className="flex-[2] bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 disabled:opacity-50 text-white rounded-xl py-3 text-sm font-bold min-h-[44px] transition">
                  {uploading ? "Uploading..." : `Upload ${previews.length} & Process →`}
                </button>
              </div>
              <p className="text-xs text-slate-400 text-center">Processing happens in background. You can continue adding.</p>
            </div>
          )}
        </div>

        {/* Uploaded papers status */}
        <div className="bg-white rounded-2xl p-4 sm:p-5 border border-slate-200 shadow-sm">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-sm text-slate-900">Uploaded Papers</h3>
            <span className="text-xs text-slate-500">{subs.length} total</span>
          </div>
          {subs.length===0 ? <p className="text-xs text-slate-400 mt-3 text-center py-6 border border-dashed border-slate-200 rounded-xl">No papers yet — upload above</p> :
            <div className="mt-3 space-y-2 max-h-[50vh] overflow-y-auto pr-1">
              {subs.map((s,idx)=>(
                <div key={s.id} className="flex items-center justify-between border border-slate-200 rounded-xl px-3 py-2.5 bg-white hover:bg-slate-50 transition">
                  <div className="flex items-center gap-3 min-w-0 flex-1">
                    <span className="text-xs font-bold w-7 h-7 rounded-full bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0">{idx+1}</span>
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-slate-900 truncate">{s.studentName || `Student ${idx+1}`}</p>
                      <p className="text-xs text-slate-500 truncate">{s.rollNumber ? `Roll ${s.rollNumber}` : s.originalName || s.imageUrl.split("/").pop()}</p>
                    </div>
                  </div>
                  <div className="text-right shrink-0 ml-2">
                    {s.status==="COMPLETED" && <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 px-2.5 py-1 rounded-full font-medium">✓ {s.score!==null?`${s.score}`: ""}</span>}
                    {s.status==="REVIEW_REQUIRED" && <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 px-2.5 py-1 rounded-full font-medium">⚠ Review</span>}
                    {s.status==="PROCESSING" && <span className="text-xs bg-blue-50 text-blue-700 border border-blue-200 px-2.5 py-1 rounded-full animate-pulse font-medium">Processing...</span>}
                    {s.status==="UPLOADED" && <span className="text-xs bg-slate-100 text-slate-600 border border-slate-200 px-2.5 py-1 rounded-full">Queued</span>}
                    {s.status==="FAILED" && <span className="text-xs bg-red-50 text-red-700 border border-red-200 px-2.5 py-1 rounded-full font-medium" title={(s as any).error || ""}>Failed</span>}
                  </div>
                </div>
              ))}
            </div>
          }
          <div className="mt-4 flex gap-2 sm:gap-3">
            <button onClick={()=>fetchData()} className="flex-1 border border-slate-200 bg-white hover:bg-slate-50 rounded-xl py-3 text-sm font-medium min-h-[44px]">Refresh</button>
            <Link href={`/exam/${id}/results`} className="flex-1 bg-slate-900 hover:bg-black text-white rounded-xl py-3 text-sm text-center font-medium min-h-[44px] flex items-center justify-center">View Results</Link>
          </div>
          <p className="text-xs text-slate-400 text-center mt-2">Polling every 2s • Up to {MAX_SHEETS} sheets per exam</p>
        </div>

        <div className="flex gap-2 text-xs pb-4">
          <Link href="/" className="text-slate-500 hover:text-slate-700 hover:underline py-2">← Home</Link>
          <span className="text-slate-300 py-2">|</span>
          <Link href={`/exam/${id}/verify`} className="text-slate-500 hover:text-slate-700 hover:underline py-2">Edit Answer Key</Link>
        </div>
      </main>
    </div>
  );
}
