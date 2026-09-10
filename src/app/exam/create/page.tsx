"use client";
import { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

type Section = { from: number; to: number; marks: number; negativeMarks: number };

function CreateExamInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [form, setForm] = useState({ name:"", subject:"", questionCount:"30", marksPerQuestion:"1", negativeMarks:"0" });
  const [sheetType, setSheetType] = useState<"bubble"|"handwritten"|"auto">("auto");
  const [uncertainMarking, setUncertainMarking] = useState<"zero"|"negative">("zero");
  const [mode, setMode] = useState<"uniform"|"variable">("uniform");
  const [sections, setSections] = useState<Section[]>([{ from:1, to:30, marks:1, negativeMarks:0 }]);
  const [batches, setBatches] = useState<any[]>([]);
  const [subjects, setSubjects] = useState<any[]>([]);
  const [batchId, setBatchId] = useState<string>(searchParams.get("batchId") || "");
  const [subjectId, setSubjectId] = useState<string>(searchParams.get("subjectId") || "");
  const [showNewSubject, setShowNewSubject] = useState(false);
  const [newSubjectName, setNewSubjectName] = useState("");
  const [newSubjectCode, setNewSubjectCode] = useState("");
  const [qpFile, setQpFile] = useState<File | null>(null);
  const [akFile, setAkFile] = useState<File | null>(null);
  const [akTab, setAkTab] = useState<"doc"|"photo">("photo");
  const [akPreviewUrl, setAkPreviewUrl] = useState<string | null>(null);
  const [manualKey, setManualKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const qc = parseInt(form.questionCount,10) || 0;

  // Load batches
  useEffect(()=>{
    fetch("/api/batches").then(r=>r.json()).then(d=> { if(Array.isArray(d)) setBatches(d); }).catch(()=>{});
  }, []);
  // Load subjects when batch changes
  useEffect(()=>{
    if(!batchId){ setSubjects([]); return; }
    fetch(`/api/subjects?batchId=${batchId}`).then(r=>r.json()).then(d=> { if(Array.isArray(d)) setSubjects(d); }).catch(()=>{});
  }, [batchId]);
  // If subjectId query came in but batch not set, fetch subject to infer batch
  useEffect(()=>{
    const qSub = searchParams.get("subjectId");
    const qBatch = searchParams.get("batchId");
    if(qSub && !qBatch){
      fetch(`/api/subjects/${qSub}`).then(r=>r.json()).then(s=> { if(s?.batchId){ setBatchId(s.batchId); } }).catch(()=>{});
    }
  }, []);

  const createSubjectInline = async ()=>{
    if(!batchId || !newSubjectName.trim()) return;
    try{
      const res = await fetch("/api/subjects", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ batchId, name:newSubjectName.trim(), code:newSubjectCode.trim()||null })});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error||"Failed");
      setSubjects(prev=>[data, ...prev]);
      setSubjectId(data.id);
      setNewSubjectName(""); setNewSubjectCode(""); setShowNewSubject(false);
    } catch(e:any){ setError(e.message); }
  };

  // Keep sections in sync with questionCount when in variable mode
  useEffect(()=>{
    if (mode !== "variable") return;
    if (!qc || qc<1) return;
    setSections(prev=>{
      if (prev.length===0) return [{ from:1,to:qc,marks:1,negativeMarks:0 }];
      const copy=[...prev];
      // ensure from chain is correct
      for(let i=0;i<copy.length;i++){
        copy[i].from = i===0?1: copy[i-1].to+1;
      }
      // adjust last to qc
      if (copy[copy.length-1].to !== qc) copy[copy.length-1].to = qc;
      // if overflow (last from > qc) trim
      // remove sections that start beyond qc
      let filtered = copy.filter(s=> s.from <= qc);
      if (filtered.length===0) filtered=[{ from:1,to:qc,marks:1,negativeMarks:0 }];
      else {
        filtered[filtered.length-1].to = qc;
        // ensure no to exceeds qc
        for(let i=0;i<filtered.length;i++) if(filtered[i].to>qc) filtered[i].to=qc;
      }
      return filtered;
    });
  },[qc, mode]);

  const switchMode = (m:"uniform"|"variable")=>{
    if(m==="variable" && mode==="uniform"){
      const q = parseInt(form.questionCount,10)||30;
      setSections([{ from:1,to:q, marks: parseFloat(form.marksPerQuestion)||1, negativeMarks: parseFloat(form.negativeMarks)||0 }]);
    }
    setMode(m);
  };

  const updateSectionTo = (idx:number, val:number)=>{
    setSections(prev=>{
      const copy=[...prev.map(s=>({...s}))];
      copy[idx].to = val;
      // recompute from for subsequent
      for(let i=idx+1;i<copy.length;i++) copy[i].from = copy[i-1].to+1;
      return copy;
    });
  };
  const updateSectionField = (idx:number, field:"marks"|"negativeMarks", val:number)=>{
    setSections(prev=>{
      const copy=[...prev.map(s=>({...s}))];
      (copy[idx] as any)[field]=val;
      return copy;
    });
  };
  const addSection = ()=>{
    if(!qc) return;
    if(sections.length >= qc){
      setError(`Maximum ${qc} sections (one per question)`);
      return;
    }
    setError("");
    setSections(prev=>{
      const copy=[...prev.map(s=>({...s}))];
      const last = copy[copy.length-1];
      if(last.to - last.from < 1){
        // need space: shrink last by 1 if possible and append
        if(last.to <= last.from){
          setError("Adjust To of last section to make space before adding");
          return prev;
        }
      }
      // split last section in half
      const mid = Math.floor((last.from + last.to)/2);
      // ensure mid < last.to
      const newMid = mid >= last.to ? last.to-1 : mid;
      const newLast = { ...last, to: newMid };
      const newSec: Section = { from: newMid+1, to: qc, marks: last.marks, negativeMarks: last.negativeMarks };
      return [...copy.slice(0,-1), newLast, newSec];
    });
  };
  const removeSection = (idx:number)=>{
    if(sections.length<=1) return;
    setSections(prev=>{
      const filtered = prev.filter((_,i)=> i!==idx);
      // recompute from chain
      for(let i=0;i<filtered.length;i++) filtered[i].from = i===0?1: filtered[i-1].to+1;
      filtered[filtered.length-1].to = qc;
      return filtered;
    });
  };

  const validateSections = (): string|null=>{
    if(mode!=="variable") return null;
    if(sections.length===0) return "Add at least one section";
    const sorted=[...sections].sort((a,b)=>a.from-b.from);
    if(sorted[0].from!==1) return `First section must start at 1`;
    if(sorted[sorted.length-1].to!==qc) return `Last section must end at ${qc}`;
    for(let i=0;i<sorted.length;i++){
      const s=sorted[i];
      if(s.from> s.to) return `Section ${i+1}: from > to`;
      if(s.marks<0 || isNaN(s.marks)) return `Section ${i+1}: marks must be >=0`;
      if(s.negativeMarks<0 || isNaN(s.negativeMarks)) return `Section ${i+1}: negative must be >=0`;
      if(i>0 && s.from !== sorted[i-1].to+1) {
        if(s.from <= sorted[i-1].to) return `Overlap between ${sorted[i-1].from}-${sorted[i-1].to} and ${s.from}-${s.to}`;
        return `Gap ${sorted[i-1].to+1}..${s.from-1} not covered`;
      }
      if(s.to>qc || s.from<1) return `Section ${i+1} out of bounds 1-${qc}`;
    }
    return null;
  };
  const validationError = validateSections();
  const totalMarks = sections.reduce((sum,s)=> sum + (s.to - s.from +1)* s.marks,0);
  // For display: if contiguous valid else show partial sum

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    // subject derived from subjectId if chosen
    let finalSubject = form.subject;
    if(subjectId){
      const chosen = subjects.find(s=>s.id===subjectId);
      if(chosen) finalSubject = chosen.name;
    }
    if (!form.name || !finalSubject) { setError("Name and subject required — pick a subject or enter one"); return; }
    if(!qc || qc<1 || qc>200){ setError("Questions must be 1-200"); return; }
    if(mode==="variable"){
      const err = validateSections();
      if(err){ setError(err); return; }
    }
    setLoading(true);
    const fd = new FormData();
    fd.append("name", form.name);
    fd.append("subject", finalSubject);
    if(batchId) fd.append("batchId", batchId);
    if(subjectId) fd.append("subjectId", subjectId);
    fd.append("questionCount", form.questionCount);
    fd.append("sheetType", sheetType);
    fd.append("uncertainMarking", uncertainMarking);
    if(mode==="uniform"){
      fd.append("marksPerQuestion", form.marksPerQuestion);
      fd.append("negativeMarks", form.negativeMarks);
    } else {
      // send markingScheme + also legacy for compat
      fd.append("markingScheme", JSON.stringify(sections));
      fd.append("marksPerQuestion", String(sections[0].marks));
      fd.append("negativeMarks", String(sections[0].negativeMarks));
    }
    if (qpFile) fd.append("questionPaper", qpFile);
    if (akFile) fd.append("answerKey", akFile);
    if (manualKey.trim()) fd.append("manualAnswerKey", manualKey.trim());
    try {
      const res = await fetch("/api/exams", { method:"POST", body: fd });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed");
      router.push(`/exam/${data.id}/verify`);
    } catch (err:any) {
      setError(err.message);
    } finally { setLoading(false); }
  };

  return (
    <div className="min-h-screen bg-[#eef3ee]">
      <header className="border-b border-stone-200 bg-white sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center gap-3">
          <Link href="/" className="text-sm text-stone-500 hover:text-stone-800 min-h-[32px] flex items-center px-2 -ml-2 rounded-lg active:bg-stone-100">← Home</Link>
          <span className="font-semibold text-stone-900">Create Exam</span>
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
        <form onSubmit={submit} className="bg-white rounded-2xl p-4 sm:p-6 shadow-sm border border-stone-200 space-y-5">
          <h1 className="text-lg sm:text-xl font-bold text-stone-900">Create Exam</h1>
          {searchParams.get("batchId") || searchParams.get("subjectId") ? (
            <p className="text-xs text-[#9a3412] bg-[#fff1e7] border border-[#fecbb8] rounded-lg px-3 py-2">Scoped creation — batch/subject prefilled from previous page. <Link href="/" className="underline">Change</Link></p>
          ) : null}

          <div className="grid grid-cols-1 gap-4">
            {/* Batch / Subject hierarchy */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="space-y-1.5">
                <span className="text-sm font-medium text-stone-700">Batch / Class</span>
                <select value={batchId} onChange={e=>{ setBatchId(e.target.value); setSubjectId(""); }} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-[#9a3412] outline-none">
                  <option value="">Unassigned (legacy)</option>
                  {batches.map((b:any)=>(<option key={b.id} value={b.id}>{b.name}{b.academicYear?` — ${b.academicYear}`:""}</option>))}
                </select>
                <span className="text-[11px] text-stone-400"><Link href="/" className="text-[#9a3412] hover:underline">+ New Batch</Link> on home</span>
              </label>
              <label className="space-y-1.5">
                <span className="text-sm font-medium text-stone-700">Subject *</span>
                {batchId ? (
                  <div className="space-y-2">
                    <select value={subjectId} onChange={e=>setSubjectId(e.target.value)} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" required={!!batchId}>
                      <option value="">Select subject…</option>
                      {subjects.map((s:any)=>(<option key={s.id} value={s.id}>{s.name}{s.code?` (${s.code})`:""}</option>))}
                    </select>
                    {!showNewSubject ? (
                      <button type="button" onClick={()=>setShowNewSubject(true)} className="text-xs text-[#9a3412] hover:underline">+ New subject in this batch</button>
                    ) : (
                      <div className="flex gap-2 items-end">
                        <input value={newSubjectName} onChange={e=>setNewSubjectName(e.target.value)} placeholder="New subject" className="flex-1 border border-stone-200 rounded-lg px-2 py-2 text-sm bg-white outline-none" />
                        <input value={newSubjectCode} onChange={e=>setNewSubjectCode(e.target.value)} placeholder="Code" className="w-24 border border-stone-200 rounded-lg px-2 py-2 text-sm bg-white outline-none" />
                        <button type="button" onClick={createSubjectInline} className="text-xs bg-[#9a3412] text-white px-3 py-2 rounded-full">Add</button>
                        <button type="button" onClick={()=>setShowNewSubject(false)} className="text-xs border border-stone-200 px-3 py-2 rounded-full">Cancel</button>
                      </div>
                    )}
                  </div>
                ) : (
                  <input value={form.subject} onChange={e=>setForm({...form, subject:e.target.value})} placeholder="Physics — or pick a Batch first" className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm focus:ring-2 focus:ring-[#9a3412] focus:border-[#9a3412] outline-none bg-white" required={!subjectId} />
                )}
              </label>
            </div>
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-stone-700">Exam Name *</span>
              <input value={form.name} onChange={e=>setForm({...form, name:e.target.value})} placeholder="Physics Unit Test 1" className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm focus:ring-2 focus:ring-[#9a3412] focus:border-[#9a3412] outline-none bg-white" required />
            </label>
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-stone-700">Total Questions *</span>
              <input type="number" min={1} max={200} value={form.questionCount} onChange={e=>setForm({...form, questionCount:e.target.value})} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" required />
            </label>

            {/* Sheet Type — bubble vs handwritten uses same Vision pipeline, different prompt */}
            <div className="border border-stone-200 rounded-xl p-3 sm:p-4 bg-white space-y-2">
              <span className="text-sm font-semibold text-stone-800">Answer Sheet Format</span>
              <p className="text-xs text-stone-500">Same Vision grading pipeline — only the extraction prompt changes. Handwritten = plain paper list like <code className="bg-stone-100 px-1 rounded">1.a 2.b 3.c</code>. Upload a photo either way.</p>
              <div className="grid grid-cols-3 gap-2">
                {([
                  { id:"auto", label:"Auto (Both)", desc:"Detect" },
                  { id:"bubble", label:"Bubble OMR", desc:"Circles" },
                  { id:"handwritten", label:"Handwritten", desc:"1.a 2.b" },
                ] as const).map(o=>(
                  <button
                    key={o.id}
                    type="button"
                    onClick={()=>setSheetType(o.id)}
                    className={`border rounded-xl px-2 py-2.5 text-center transition ${sheetType===o.id ? "bg-[#9a3412] text-white border-[#9a3412] shadow" : "bg-[#eef3ee] border-stone-200 hover:border-[#fecbb8] hover:bg-white text-stone-700"}`}
                  >
                    <span className="block text-xs font-semibold leading-none">{o.label}</span>
                    <span className={`block text-[11px] mt-1 ${sheetType===o.id?"text-[#ffe4d6]":"text-stone-500"}`}>{o.desc}</span>
                  </button>
                ))}
              </div>
              {sheetType==="handwritten" && <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5">Students write options by hand (e.g. <code className="bg-amber-100 px-1 rounded">1. a  2. c  3. b</code>, <code className="bg-amber-100 px-1 rounded">1:a 2:b</code>, <code className="bg-amber-100 px-1 rounded">Q1 - A</code>) — upload the photo. Lower/upper case both ok.</p>}
              {sheetType==="auto" && <p className="text-[11px] text-stone-500">Auto detects bubble OMR vs handwritten list per photo. Best for mixed classes.</p>}
            </div>

            {/* Uncertain / Multiple scoring */}
            <div className="border border-stone-200 rounded-xl p-3 sm:p-4 bg-white space-y-2">
              <span className="text-sm font-semibold text-stone-800">Uncertain / Multiple Marks</span>
              <p className="text-xs text-stone-500">How to score <code className="bg-stone-100 px-1 rounded">UNCERTAIN</code> / <code className="bg-stone-100 px-1 rounded">MULTIPLE</code> (faint, double-mark, illegible). BLANK always 0.</p>
              <div className="grid grid-cols-2 gap-2">
                {([
                  { id:"zero", label:"0 marks", desc:"Default — no penalty" },
                  { id:"negative", label:"- Negative", desc:"Use -Neg from scheme" },
                ] as const).map(o=>(
                  <button
                    key={o.id}
                    type="button"
                    onClick={()=>setUncertainMarking(o.id)}
                    className={`border rounded-xl px-3 py-2.5 text-left transition ${uncertainMarking===o.id ? "bg-[#9a3412] text-white border-[#9a3412] shadow" : "bg-[#eef3ee] border-stone-200 hover:border-[#fecbb8] hover:bg-white text-stone-700"}`}
                  >
                    <span className="block text-xs font-semibold leading-none">{o.label}</span>
                    <span className={`block text-[11px] mt-1 ${uncertainMarking===o.id?"text-[#ffe4d6]":"text-stone-500"}`}>{o.desc}</span>
                  </button>
                ))}
              </div>
              <p className="text-[11px] text-stone-400">Exam-wide. Change anytime in Verify → re-grades all submissions.</p>
            </div>

            {/* Marking Scheme Mode */}
            <div className="border border-stone-200 rounded-xl p-3 sm:p-4 bg-[#eef3ee]/50 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-semibold text-stone-800">Marking Scheme</span>
                <div className="flex bg-white border border-stone-200 rounded-full p-1">
                  <button type="button" onClick={()=>switchMode("uniform")} className={`px-3 sm:px-4 py-1.5 rounded-full text-xs font-medium transition ${mode==="uniform" ? "bg-[#9a3412] text-white shadow" : "text-stone-600 hover:text-stone-900"}`}>Uniform</button>
                  <button type="button" onClick={()=>switchMode("variable")} className={`px-3 sm:px-4 py-1.5 rounded-full text-xs font-medium transition ${mode==="variable" ? "bg-[#9a3412] text-white shadow" : "text-stone-600 hover:text-stone-900"}`}>Variable</button>
                </div>
              </div>

              {mode==="uniform" ? (
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-stone-700">Marks / Correct</span>
                    <input type="number" step="0.5" min={0} value={form.marksPerQuestion} onChange={e=>setForm({...form, marksPerQuestion:e.target.value})} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-stone-700">Negative (deduction)</span>
                    <input type="number" step="0.25" min={0} value={form.negativeMarks} onChange={e=>setForm({...form, negativeMarks:e.target.value})} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
                    <span className="text-[11px] text-stone-400">0 = no negative</span>
                  </label>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-stone-500">Define sections that fully cover <b>1..{qc||"?"}</b> contiguously. Example: 1-10 +4/-0, 11-20 +5/-1.</p>
                  <div className="space-y-2">
                    {sections.map((sec, idx)=>(
                      <div key={idx} className="bg-white border border-stone-200 rounded-xl p-3 flex flex-col sm:flex-row gap-2 sm:gap-3 sm:items-end">
                        <div className="flex-1 grid grid-cols-4 gap-2">
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-stone-500">From</span>
                            <div className="w-full bg-stone-100 border border-stone-200 rounded-lg px-2 py-2 text-sm font-bold text-stone-700 text-center">{sec.from}</div>
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-stone-500">To *</span>
                            <input type="number" min={sec.from} max={qc} value={sec.to} onChange={e=> updateSectionTo(idx, parseInt(e.target.value)||sec.from)} className="w-full border border-stone-200 rounded-lg px-2 py-2 text-sm text-center font-medium bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-stone-500">+ Marks</span>
                            <input type="number" step="0.5" min={0} value={sec.marks} onChange={e=> updateSectionField(idx,"marks", parseFloat(e.target.value)||0)} className="w-full border border-stone-200 rounded-lg px-2 py-2 text-sm text-center bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-stone-500">- Neg</span>
                            <input type="number" step="0.25" min={0} value={sec.negativeMarks} onChange={e=> updateSectionField(idx,"negativeMarks", parseFloat(e.target.value)||0)} className="w-full border border-stone-200 rounded-lg px-2 py-2 text-sm text-center bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
                          </label>
                        </div>
                        <button type="button" onClick={()=>removeSection(idx)} disabled={sections.length<=1} className="text-xs border border-stone-200 bg-white hover:bg-red-50 hover:text-red-600 hover:border-red-200 disabled:opacity-30 rounded-lg px-3 py-2 font-medium shrink-0">Remove</button>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <button type="button" onClick={addSection} className="text-xs bg-white border border-[#fecbb8] text-[#9a3412] hover:bg-[#fff1e7] px-3 py-2 rounded-full font-medium">+ Add Section</button>
                    <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${validationError ? "bg-red-50 text-red-700 border-red-200" : "bg-emerald-50 text-emerald-700 border-emerald-200"}`}>
                      {validationError ? `⚠ ${validationError}` : `✓ Full coverage • Max ${totalMarks} marks`}
                    </span>
                  </div>
                  {!validationError && (
                    <div className="flex flex-wrap gap-1.5">
                      {sections.map((s,i)=> (
                        <span key={i} className="text-[11px] bg-[#fff1e7] border border-[#fecbb8] text-[#9a3412] px-2 py-1 rounded-full font-medium">Q{s.from}-{s.to}: +{s.marks}/-{s.negativeMarks}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Question Paper — optional */}
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-stone-700">Question Paper <span className="text-xs font-normal text-stone-400">(Optional)</span></span>
              <span className="text-xs text-stone-500 block">Reference only — not used for grading. Skip if you only have the answer key. PDF / DOCX</span>
              <input type="file" accept=".pdf,.docx,.doc" onChange={e=>setQpFile(e.target.files?.[0]||null)} className="w-full border border-stone-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-[#eef3ee] file:mr-3 file:bg-white file:border file:border-stone-200 file:rounded-full file:px-3 file:py-1 file:text-xs" />
              {qpFile && <span className="text-xs text-emerald-600 font-medium">{qpFile.name}</span>}
              {!qpFile && <span className="text-[11px] text-stone-400">You can create the exam without this — grading only needs the answer key.</span>}
            </label>

            {/* Answer Key — unified: Doc or Photo (VLM) */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-stone-700">Answer Key <span className="text-red-500">*</span> <span className="text-xs font-normal text-stone-400">— Doc or Photo</span></span>
                <span className="text-[11px] text-stone-400">{akFile ? `✓ ${akFile.name}` : manualKey.trim() ? "Manual" : "Required"}</span>
              </div>
              <p className="text-xs text-stone-500">Choose one: upload a document <em>or</em> take a photo. Photo uses Vision LLM (same pipeline as sheets) — e.g. <code className="bg-stone-100 px-1 rounded">1. B 2. A …</code> or handwritten <code className="bg-stone-100 px-1 rounded">1.a 2.b</code>. You’ll verify on next screen.</p>
              <div className="flex bg-stone-100 border border-stone-200 rounded-full p-1 w-fit">
                <button type="button" onClick={()=>setAkTab("photo")} className={`px-4 py-1.5 rounded-full text-xs font-medium transition ${akTab==="photo" ? "bg-[#9a3412] text-white shadow" : "text-stone-600 hover:text-stone-900"}`}>📷 Photo (VLM)</button>
                <button type="button" onClick={()=>setAkTab("doc")} className={`px-4 py-1.5 rounded-full text-xs font-medium transition ${akTab==="doc" ? "bg-[#9a3412] text-white shadow" : "text-stone-600 hover:text-stone-900"}`}>📄 Document</button>
              </div>

              {akTab==="photo" ? (
                <div className="border border-stone-200 rounded-xl p-3 bg-[#eef3ee] space-y-3">
                  <p className="text-[11px] text-stone-600">Take a picture of the printed/handwritten key — VLM extracts A-D per question. Supports JPG/PNG/WEBP/HEIC.</p>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="bg-[#9a3412] hover:bg-[#7c2d12] text-white rounded-xl py-3 flex flex-col items-center justify-center cursor-pointer transition min-h-[72px]">
                      <span className="text-sm">📷</span>
                      <span className="text-xs font-medium">Take Photo</span>
                      <input type="file" accept="image/*,image/heic,image/heif" capture="environment" className="hidden" onChange={e=>{
                        const f=e.target.files?.[0]||null;
                        if(f){ setAkFile(f); try{ setAkPreviewUrl(URL.createObjectURL(f)); }catch{ setAkPreviewUrl(null); } }
                        e.currentTarget.value="";
                      }} />
                    </label>
                    <label className="border-2 border-dashed border-stone-300 hover:border-[#fecbb8] bg-white rounded-xl py-3 flex flex-col items-center justify-center cursor-pointer hover:bg-[#eef3ee] transition min-h-[72px]">
                      <span className="text-sm">🖼️</span>
                      <span className="text-xs font-medium text-stone-700">Upload Image</span>
                      <span className="text-[11px] text-stone-400">JPG PNG HEIC</span>
                      <input type="file" accept="image/*,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif" className="hidden" onChange={e=>{
                        const f=e.target.files?.[0]||null;
                        if(f){ setAkFile(f); try{ setAkPreviewUrl(URL.createObjectURL(f)); }catch{ setAkPreviewUrl(null); } }
                        e.currentTarget.value="";
                      }} />
                    </label>
                  </div>
                  {akFile && (
                    <div className="flex gap-3 items-center bg-white border border-stone-200 rounded-xl p-2">
                      {akPreviewUrl && !akFile.name.toLowerCase().endsWith(".pdf") ? (
                        <img src={akPreviewUrl} alt="key preview" className="w-16 h-16 object-cover rounded-lg border border-stone-200" />
                      ) : (
                        <div className="w-16 h-16 bg-stone-100 rounded-lg flex items-center justify-center text-lg border border-stone-200">🖼️</div>
                      )}
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium text-stone-800 truncate">{akFile.name}</p>
                        <p className="text-[11px] text-stone-400">{(akFile.size/1024).toFixed(0)} KB • VLM extraction</p>
                      </div>
                      <button type="button" onClick={()=>{ setAkFile(null); if(akPreviewUrl) URL.revokeObjectURL(akPreviewUrl); setAkPreviewUrl(null); }} className="text-xs border border-stone-200 bg-white px-3 py-1.5 rounded-full hover:bg-red-50 hover:text-red-600 hover:border-red-200">Remove</button>
                    </div>
                  )}
                </div>
              ) : (
                <div className="border border-stone-200 rounded-xl p-3 bg-[#eef3ee] space-y-2">
                  <p className="text-[11px] text-stone-600">PDF / DOCX / TXT — extracted via LLM + regex fallback (no vision needed).</p>
                  <input type="file" accept=".pdf,.docx,.doc,.txt" onChange={e=>{
                    const f=e.target.files?.[0]||null;
                    if(f){ setAkFile(f); if(akPreviewUrl) { URL.revokeObjectURL(akPreviewUrl); setAkPreviewUrl(null); } }
                  }} className="w-full border border-stone-200 rounded-xl px-3 py-2.5 text-sm bg-white file:mr-3 file:bg-stone-900 file:text-white file:border-0 file:rounded-full file:px-3 file:py-1 file:text-xs" />
                  {akFile && <span className="text-xs text-emerald-600 font-medium block">{akFile.name} • doc extraction</span>}
                </div>
              )}
              {akFile && <p className="text-[11px] text-emerald-600">Selected — will be sent as <code className="bg-emerald-50 px-1 rounded border border-emerald-200">{akFile.name.split('.').pop()?.toLowerCase()}</code> and extracted before verify.</p>}
            </div>

            <div className="border-t border-stone-200 pt-4">
              <span className="text-sm font-medium text-stone-700">Or enter answer key manually</span>
              <p className="text-xs text-stone-500 mb-2 mt-1">Comma or line separated: <code className="bg-stone-100 px-1.5 py-0.5 rounded text-stone-700">B,D,A,C,B,...</code> or JSON <code className="bg-stone-100 px-1.5 py-0.5 rounded text-stone-700">{"{"}"1":"B","2":"D"{"}"}</code></p>
              <textarea value={manualKey} onChange={e=>setManualKey(e.target.value)} placeholder="B,D,A,C,C,B,A..." rows={3} className="w-full border border-stone-200 rounded-xl px-3 py-2.5 text-sm font-mono bg-white focus:ring-2 focus:ring-[#9a3412] outline-none" />
            </div>
          </div>

          {error && <p className="text-sm text-red-600 bg-red-50 p-3 rounded-xl border border-red-200">{error}</p>}
          {validationError && mode==="variable" && <p className="text-xs text-amber-700 bg-amber-50 p-2.5 rounded-xl border border-amber-200">Fix marking scheme: {validationError}</p>}

          <button disabled={loading} className="w-full bg-[#9a3412] hover:bg-[#7c2d12] active:bg-[#7c2d12] disabled:opacity-50 text-white py-3.5 sm:py-3 rounded-xl font-medium transition min-h-[48px]">
            {loading ? "Creating..." : "Create Exam"}
          </button>
          <p className="text-xs text-stone-400 text-center">You can verify/edit the extracted answer key on the next screen.</p>
        </form>
      </main>
    </div>
  );
}

export default function CreateExamPage(){
  return <Suspense fallback={<div className="min-h-screen bg-[#eef3ee] p-8 text-sm text-stone-500">Loading…</div>}><CreateExamInner /></Suspense>;
}
