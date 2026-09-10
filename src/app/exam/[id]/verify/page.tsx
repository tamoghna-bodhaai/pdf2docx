"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

export default function VerifyPage() {
  const { id } = useParams() as { id:string };
  const router = useRouter();
  const [exam, setExam] = useState<any>(null);
  const [keyJson, setKeyJson] = useState<Record<string,string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(()=>{
    fetch(`/api/exams/${id}`).then(r=>r.json()).then(d=>{
      setExam(d);
      if (d.answerKeyJson) setKeyJson(d.answerKeyJson);
      else {
        const init: Record<string,string>={};
        for(let i=1;i<=d.questionCount;i++) init[String(i)]="A";
        setKeyJson(init);
      }
      setLoading(false);
    });
  },[id]);

  const save = async () => {
    setSaving(true); setError("");
    try{
      const res = await fetch(`/api/exams/${id}/answer-key`,{method:"POST", headers:{ "Content-Type":"application/json"}, body: JSON.stringify({ answerKeyJson: keyJson })});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error);
      router.push(`/exam/${id}/scan`);
    }catch(e:any){ setError(e.message)} finally{setSaving(false)}
  };

  const [markingEdit, setMarkingEdit] = useState(false);
  const [sections, setSections] = useState<any[]>([]);
  const [markingSaving, setMarkingSaving] = useState(false);
  const [breadcrumb, setBreadcrumb] = useState<{ batch?: any; subject?: any } | null>(null);

  useEffect(()=>{
    if(!exam?.batchId && !exam?.subjectId) return;
    const load = async ()=>{
      try{
        const [b,s] = await Promise.all([
          exam.batchId ? fetch(`/api/batches/${exam.batchId}`).then(r=>r.json()).catch(()=>null) : null,
          exam.subjectId ? fetch(`/api/subjects/${exam.subjectId}`).then(r=>r.json()).catch(()=>null) : null,
        ]);
        setBreadcrumb({ batch: b?.id? b: null, subject: s?.id? s: null });
      }catch{}
    };
    load();
  },[exam?.batchId, exam?.subjectId]);

  useEffect(()=>{
    if(exam?.markingScheme){
      setSections(exam.markingScheme);
    } else if(exam){
      setSections([{from:1, to: exam.questionCount, marks: exam.marksPerQuestion||1, negativeMarks: exam.negativeMarks||0}]);
    }
  },[exam]);

  const computeMax = (scheme:any[])=> scheme.reduce((s,a)=> s + (a.to-a.from+1)*a.marks,0);
  const formatScheme = (scheme:any[])=> scheme.map(s=>`Q${s.from}-${s.to}: +${s.marks}/-${s.negativeMarks}`).join(" • ");

  const saveMarkingScheme = async ()=>{
    const qc = exam.questionCount;
    // validate
    const sorted=[...sections].sort((a,b)=>a.from-b.from);
    if(sorted[0].from!==1 || sorted[sorted.length-1].to!==qc){ setError(`Scheme must cover 1..${qc}`); return; }
    for(let i=0;i<sorted.length;i++){
      const s=sorted[i];
      if(s.from> s.to){ setError(`Section ${i+1}: from>to`); return; }
      if(i>0 && s.from !== sorted[i-1].to+1){ setError(`Gap/overlap at section ${i+1}`); return; }
    }
    setMarkingSaving(true); setError("");
    try{
      const res=await fetch(`/api/exams/${id}`,{method:"PATCH", headers:{ "Content-Type":"application/json"}, body: JSON.stringify({ markingScheme: sorted })});
      const data=await res.json();
      if(!res.ok) throw new Error(data.error);
      setExam(data);
      setMarkingEdit(false);
    }catch(e:any){ setError(e.message)} finally{setMarkingSaving(false)}
  };

  if(loading) return <div className="p-8 text-center text-sm text-stone-500">Loading...</div>;
  if(!exam) return <div className="p-8 text-center">Exam not found</div>;

  return (
    <div className="min-h-screen bg-[#eef3ee]">
      <header className="border-b border-stone-200 bg-white sticky top-0 z-10">
        <div className="max-w-2xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center gap-3">
          <Link href={breadcrumb?.subject ? `/batch/${exam.batchId}/subject/${exam.subjectId}` : breadcrumb?.batch ? `/batch/${exam.batchId}` : "/"} className="text-sm text-stone-500 hover:text-stone-800 min-h-[32px] flex items-center px-2 -ml-2 rounded-lg active:bg-stone-100">← Back</Link>
          <span className="font-semibold text-stone-900">Verify Answer Key</span>
          {breadcrumb?.batch && <span className="hidden sm:inline text-xs text-stone-400 ml-2 truncate">{breadcrumb.batch.name}{breadcrumb.subject?` › ${breadcrumb.subject.name}`:""}</span>}
        </div>
      </header>
      <main className="max-w-2xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
        <div className="bg-white rounded-2xl p-4 sm:p-6 shadow-sm border border-stone-200">
          <h1 className="font-bold text-stone-900 leading-tight">{exam.name} — {exam.subject}</h1>
          <p className="text-xs text-stone-500 mt-1">{exam.questionCount} questions • {exam.markingScheme ? `${formatScheme(exam.markingScheme)} • Max ${computeMax(exam.markingScheme)}` : `${exam.marksPerQuestion} marks each`}</p>

          {/* Marking Scheme Editor */}
          <div className="mt-4 border border-stone-200 rounded-xl p-3 bg-[#eef3ee]">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-stone-800">Marking Scheme</span>
              {!markingEdit ? <button onClick={()=>setMarkingEdit(true)} className="text-xs border border-stone-200 bg-white px-3 py-1.5 rounded-full font-medium hover:bg-[#eef3ee]">Edit</button> :
                <div className="flex gap-2"><button onClick={()=>setMarkingEdit(false)} className="text-xs border border-stone-200 bg-white px-3 py-1.5 rounded-full">Cancel</button><button onClick={saveMarkingScheme} disabled={markingSaving} className="text-xs bg-[#9a3412] text-white px-3 py-1.5 rounded-full disabled:opacity-50">{markingSaving?"Saving...":"Save"}</button></div>}
            </div>
            {!markingEdit ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {(exam.markingScheme||[{from:1,to:exam.questionCount,marks:exam.marksPerQuestion,negativeMarks:exam.negativeMarks}]).map((s:any,i:number)=>(
                  <span key={i} className="text-[11px] bg-white border border-stone-200 px-2 py-1 rounded-full font-medium">Q{s.from}-{s.to}: +{s.marks}/-{s.negativeMarks}</span>
                ))}
                <span className="text-[11px] text-stone-500 ml-1">Max {exam.markingScheme ? computeMax(exam.markingScheme): exam.questionCount*exam.marksPerQuestion} marks</span>
              </div>
            ) : (
              <div className="mt-3 space-y-2">
                {sections.map((sec, idx)=>(
                  <div key={idx} className="bg-white border border-stone-200 rounded-xl p-2 flex gap-2 items-end">
                    <div className="flex-1 grid grid-cols-4 gap-2">
                      <label className="space-y-1"><span className="text-[10px] text-stone-500">From</span><div className="bg-stone-100 border border-stone-200 rounded-lg px-2 py-1.5 text-xs text-center font-bold">{sec.from}</div></label>
                      <label className="space-y-1"><span className="text-[10px] text-stone-500">To</span><input type="number" min={sec.from} max={exam.questionCount} value={sec.to} onChange={e=>{
                        const v=parseInt(e.target.value)||sec.from;
                        setSections(prev=>{ const copy=[...prev.map(x=>({...x}))]; copy[idx].to=v; for(let i=idx+1;i<copy.length;i++) copy[i].from=copy[i-1].to+1; return copy; });
                      }} className="w-full border border-stone-200 rounded-lg px-1 py-1.5 text-xs text-center" /></label>
                      <label className="space-y-1"><span className="text-[10px] text-stone-500">+Marks</span><input type="number" step="0.5" value={sec.marks} onChange={e=>{ const v=parseFloat(e.target.value)||0; setSections(prev=>{ const c=[...prev.map(x=>({...x}))]; c[idx].marks=v; return c;});}} className="w-full border border-stone-200 rounded-lg px-1 py-1.5 text-xs text-center" /></label>
                      <label className="space-y-1"><span className="text-[10px] text-stone-500">-Neg</span><input type="number" step="0.25" value={sec.negativeMarks} onChange={e=>{ const v=parseFloat(e.target.value)||0; setSections(prev=>{ const c=[...prev.map(x=>({...x}))]; c[idx].negativeMarks=v; return c;});}} className="w-full border border-stone-200 rounded-lg px-1 py-1.5 text-xs text-center" /></label>
                    </div>
                    <button disabled={sections.length<=1} onClick={()=>{
                      setSections(prev=>{
                        const filtered=prev.filter((_,i)=>i!==idx);
                        for(let i=0;i<filtered.length;i++) filtered[i].from=i===0?1: filtered[i-1].to+1;
                        filtered[filtered.length-1].to=exam.questionCount;
                        return filtered;
                      });
                    }} className="text-xs border border-stone-200 rounded-lg px-2 py-1.5 disabled:opacity-30 bg-white">✕</button>
                  </div>
                ))}
                <button type="button" onClick={()=>{
                  const qc=exam.questionCount;
                  if(sections.length>=qc) return;
                  const copy=[...sections.map(s=>({...s}))];
                  const last=copy[copy.length-1];
                  const mid=Math.floor((last.from+last.to)/2);
                  const newMid = mid>=last.to? last.to-1:mid;
                  copy[copy.length-1].to=newMid;
                  copy.push({from:newMid+1, to:qc, marks:last.marks, negativeMarks:last.negativeMarks});
                  setSections(copy);
                }} className="text-xs bg-white border border-[#fecbb8] text-[#9a3412] px-3 py-1.5 rounded-full font-medium hover:bg-[#fff1e7]">+ Add Section</button>
                <p className="text-[11px] text-stone-500">Must fully cover 1..{exam.questionCount} contiguously. Saving will re-grade all already-graded submissions.</p>
              </div>
            )}
          </div>

          {!exam.answerKeyJson && <div className="mt-4 bg-amber-50 border border-amber-200 text-amber-800 text-xs p-3 rounded-xl">Extraction failed or no file uploaded. Please enter manually below.</div>}
          {exam.answerKeyJson && <p className="text-xs text-emerald-600 mt-3 font-medium">✓ Extracted {Object.keys(exam.answerKeyJson).length} answers — please verify and edit if needed.</p>}

          <h2 className="mt-6 font-semibold text-sm text-stone-900">Answer Key</h2>
          <div className="mt-3 grid grid-cols-3 xs:grid-cols-4 sm:grid-cols-5 gap-2 max-h-[45vh] sm:max-h-[50vh] overflow-y-auto pr-1">
            {Array.from({length: exam.questionCount}, (_,i)=>{
              const q = String(i+1);
              return (
                <label key={q} className="border border-stone-200 rounded-xl p-2 bg-[#eef3ee] flex flex-col gap-1">
                  <span className="text-xs font-medium text-stone-600">Q{q}</span>
                  <select value={keyJson[q]||"A"} onChange={e=>setKeyJson({...keyJson, [q]: e.target.value})} className="border border-stone-200 rounded-lg px-2 py-2 text-sm font-bold bg-white min-h-[36px]">
                    <option value="A">A</option><option value="B">B</option><option value="C">C</option><option value="D">D</option><option value="E">E</option>
                  </select>
                </label>
              );
            })}
          </div>

          <div className="mt-4">
            <span className="text-xs font-medium text-stone-700">Bulk edit (comma-separated)</span>
            <textarea
              value={Object.keys(keyJson).sort((a,b)=>+a-+b).map(k=>keyJson[k]).join(",")}
              onChange={e=>{
                const tokens = e.target.value.split(/[, \n;]+/).map(s=>s.trim().toUpperCase()).filter(s=>/^[A-E]$/.test(s));
                const nxt: Record<string,string>={};
                for(let i=0;i<exam.questionCount;i++) nxt[String(i+1)] = tokens[i] || keyJson[String(i+1)] || "A";
                setKeyJson(nxt);
              }}
              rows={2}
              className="w-full mt-1.5 border border-stone-200 rounded-xl px-3 py-2.5 text-xs font-mono bg-white focus:ring-2 focus:ring-[#9a3412] outline-none"
              placeholder="B,D,A,C,..."
            />
          </div>

          {error && <p className="mt-4 text-sm text-red-600 bg-red-50 p-2.5 rounded-xl border border-red-200">{error}</p>}

          <div className="mt-6 flex gap-3">
            <button onClick={save} disabled={saving} className="flex-1 bg-[#9a3412] hover:bg-[#7c2d12] active:bg-[#7c2d12] disabled:opacity-50 text-white py-3.5 rounded-xl font-medium min-h-[48px] transition">
              {saving? "Saving...":"Confirm & Start Scanning"}
            </button>
          </div>
          <Link href={`/exam/${id}/scan`} className="block text-center text-xs text-stone-500 mt-3 hover:underline hover:text-stone-700 py-2">Skip to scanning (keep current key)</Link>
        </div>
      </main>
    </div>
  );
}
