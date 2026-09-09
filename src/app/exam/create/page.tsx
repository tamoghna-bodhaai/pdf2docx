"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

type Section = { from: number; to: number; marks: number; negativeMarks: number };

export default function CreateExamPage() {
  const router = useRouter();
  const [form, setForm] = useState({ name:"", subject:"", questionCount:"30", marksPerQuestion:"1", negativeMarks:"0" });
  const [mode, setMode] = useState<"uniform"|"variable">("uniform");
  const [sections, setSections] = useState<Section[]>([{ from:1, to:30, marks:1, negativeMarks:0 }]);
  const [qpFile, setQpFile] = useState<File | null>(null);
  const [akFile, setAkFile] = useState<File | null>(null);
  const [manualKey, setManualKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const qc = parseInt(form.questionCount,10) || 0;

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
    if (!form.name || !form.subject) { setError("Name and subject required"); return; }
    if(!qc || qc<1 || qc>200){ setError("Questions must be 1-200"); return; }
    if(mode==="variable"){
      const err = validateSections();
      if(err){ setError(err); return; }
    }
    setLoading(true);
    const fd = new FormData();
    fd.append("name", form.name);
    fd.append("subject", form.subject);
    fd.append("questionCount", form.questionCount);
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
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center gap-3">
          <Link href="/" className="text-sm text-slate-500 hover:text-slate-800 min-h-[32px] flex items-center px-2 -ml-2 rounded-lg active:bg-slate-100">← Home</Link>
          <span className="font-semibold text-slate-900">Create Exam</span>
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
        <form onSubmit={submit} className="bg-white rounded-2xl p-4 sm:p-6 shadow-sm border border-slate-200 space-y-5">
          <h1 className="text-lg sm:text-xl font-bold text-slate-900">Create Exam</h1>

          <div className="grid grid-cols-1 gap-4">
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Exam Name *</span>
              <input value={form.name} onChange={e=>setForm({...form, name:e.target.value})} placeholder="Physics Unit Test 1" className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none bg-white" required />
            </label>
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Subject *</span>
              <input value={form.subject} onChange={e=>setForm({...form, subject:e.target.value})} placeholder="Physics" className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none bg-white" required />
            </label>
            <label className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Total Questions *</span>
              <input type="number" min={1} max={200} value={form.questionCount} onChange={e=>setForm({...form, questionCount:e.target.value})} className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-indigo-500 outline-none" required />
            </label>

            {/* Marking Scheme Mode */}
            <div className="border border-slate-200 rounded-xl p-3 sm:p-4 bg-slate-50/50 space-y-3">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-semibold text-slate-800">Marking Scheme</span>
                <div className="flex bg-white border border-slate-200 rounded-full p-1">
                  <button type="button" onClick={()=>switchMode("uniform")} className={`px-3 sm:px-4 py-1.5 rounded-full text-xs font-medium transition ${mode==="uniform" ? "bg-indigo-600 text-white shadow" : "text-slate-600 hover:text-slate-900"}`}>Uniform</button>
                  <button type="button" onClick={()=>switchMode("variable")} className={`px-3 sm:px-4 py-1.5 rounded-full text-xs font-medium transition ${mode==="variable" ? "bg-indigo-600 text-white shadow" : "text-slate-600 hover:text-slate-900"}`}>Variable</button>
                </div>
              </div>

              {mode==="uniform" ? (
                <div className="grid grid-cols-2 gap-3">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-slate-700">Marks / Correct</span>
                    <input type="number" step="0.5" min={0} value={form.marksPerQuestion} onChange={e=>setForm({...form, marksPerQuestion:e.target.value})} className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium text-slate-700">Negative (deduction)</span>
                    <input type="number" step="0.25" min={0} value={form.negativeMarks} onChange={e=>setForm({...form, negativeMarks:e.target.value})} className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                    <span className="text-[11px] text-slate-400">0 = no negative</span>
                  </label>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-xs text-slate-500">Define sections that fully cover <b>1..{qc||"?"}</b> contiguously. Example: 1-10 +4/-0, 11-20 +5/-1.</p>
                  <div className="space-y-2">
                    {sections.map((sec, idx)=>(
                      <div key={idx} className="bg-white border border-slate-200 rounded-xl p-3 flex flex-col sm:flex-row gap-2 sm:gap-3 sm:items-end">
                        <div className="flex-1 grid grid-cols-4 gap-2">
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-slate-500">From</span>
                            <div className="w-full bg-slate-100 border border-slate-200 rounded-lg px-2 py-2 text-sm font-bold text-slate-700 text-center">{sec.from}</div>
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-slate-500">To *</span>
                            <input type="number" min={sec.from} max={qc} value={sec.to} onChange={e=> updateSectionTo(idx, parseInt(e.target.value)||sec.from)} className="w-full border border-slate-200 rounded-lg px-2 py-2 text-sm text-center font-medium bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-slate-500">+ Marks</span>
                            <input type="number" step="0.5" min={0} value={sec.marks} onChange={e=> updateSectionField(idx,"marks", parseFloat(e.target.value)||0)} className="w-full border border-slate-200 rounded-lg px-2 py-2 text-sm text-center bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                          </label>
                          <label className="space-y-1">
                            <span className="text-[11px] font-medium text-slate-500">- Neg</span>
                            <input type="number" step="0.25" min={0} value={sec.negativeMarks} onChange={e=> updateSectionField(idx,"negativeMarks", parseFloat(e.target.value)||0)} className="w-full border border-slate-200 rounded-lg px-2 py-2 text-sm text-center bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
                          </label>
                        </div>
                        <button type="button" onClick={()=>removeSection(idx)} disabled={sections.length<=1} className="text-xs border border-slate-200 bg-white hover:bg-red-50 hover:text-red-600 hover:border-red-200 disabled:opacity-30 rounded-lg px-3 py-2 font-medium shrink-0">Remove</button>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <button type="button" onClick={addSection} className="text-xs bg-white border border-indigo-200 text-indigo-700 hover:bg-indigo-50 px-3 py-2 rounded-full font-medium">+ Add Section</button>
                    <span className={`text-xs font-medium px-2.5 py-1 rounded-full border ${validationError ? "bg-red-50 text-red-700 border-red-200" : "bg-emerald-50 text-emerald-700 border-emerald-200"}`}>
                      {validationError ? `⚠ ${validationError}` : `✓ Full coverage • Max ${totalMarks} marks`}
                    </span>
                  </div>
                  {!validationError && (
                    <div className="flex flex-wrap gap-1.5">
                      {sections.map((s,i)=> (
                        <span key={i} className="text-[11px] bg-indigo-50 border border-indigo-100 text-indigo-700 px-2 py-1 rounded-full font-medium">Q{s.from}-{s.to}: +{s.marks}/-{s.negativeMarks}</span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            <label className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Upload Question Paper</span>
              <span className="text-xs text-slate-500 block">PDF / DOCX — stored as original (not parsed)</span>
              <input type="file" accept=".pdf,.docx,.doc" onChange={e=>setQpFile(e.target.files?.[0]||null)} className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-slate-50 file:mr-3 file:bg-white file:border file:border-slate-200 file:rounded-full file:px-3 file:py-1 file:text-xs" />
              {qpFile && <span className="text-xs text-emerald-600 font-medium">{qpFile.name}</span>}
            </label>

            <label className="space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Upload Answer Key</span>
              <span className="text-xs text-slate-500 block">PDF / DOCX — auto-extracted via LLM/regex</span>
              <input type="file" accept=".pdf,.docx,.doc,.txt" onChange={e=>setAkFile(e.target.files?.[0]||null)} className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm bg-slate-50 file:mr-3 file:bg-white file:border file:border-slate-200 file:rounded-full file:px-3 file:py-1 file:text-xs" />
              {akFile && <span className="text-xs text-emerald-600 font-medium">{akFile.name}</span>}
            </label>

            <div className="border-t border-slate-200 pt-4">
              <span className="text-sm font-medium text-slate-700">Or enter answer key manually</span>
              <p className="text-xs text-slate-500 mb-2 mt-1">Comma or line separated: <code className="bg-slate-100 px-1.5 py-0.5 rounded text-slate-700">B,D,A,C,B,...</code> or JSON <code className="bg-slate-100 px-1.5 py-0.5 rounded text-slate-700">{"{"}"1":"B","2":"D"{"}"}</code></p>
              <textarea value={manualKey} onChange={e=>setManualKey(e.target.value)} placeholder="B,D,A,C,C,B,A..." rows={3} className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm font-mono bg-white focus:ring-2 focus:ring-indigo-500 outline-none" />
            </div>
          </div>

          {error && <p className="text-sm text-red-600 bg-red-50 p-3 rounded-xl border border-red-200">{error}</p>}
          {validationError && mode==="variable" && <p className="text-xs text-amber-700 bg-amber-50 p-2.5 rounded-xl border border-amber-200">Fix marking scheme: {validationError}</p>}

          <button disabled={loading} className="w-full bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 disabled:opacity-50 text-white py-3.5 sm:py-3 rounded-xl font-medium transition min-h-[48px]">
            {loading ? "Creating..." : "Create Exam"}
          </button>
          <p className="text-xs text-slate-400 text-center">You can verify/edit the extracted answer key on the next screen.</p>
        </form>
      </main>
    </div>
  );
}
