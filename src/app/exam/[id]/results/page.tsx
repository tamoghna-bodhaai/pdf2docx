"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

export default function ResultsPage() {
  const { id } = useParams() as { id:string };
  const [exam, setExam] = useState<any>(null);
  const [subs, setSubs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const handleDelete = async (subId: string, label: string) => {
    if (!confirm(`Delete "${label}"? Frees backend storage. Cannot be undone.`)) return;
    setDeletingId(subId);
    try {
      const res = await fetch(`/api/submissions/${subId}`, { method: "DELETE" });
      const data = await res.json().catch(()=>({}));
      if (!res.ok) throw new Error(data.error || "Delete failed");
      await load();
    } catch (e:any) { alert(e.message || "Delete failed"); }
    finally { setDeletingId(null); }
  };

  const load = async ()=>{
    const [eRes,sRes] = await Promise.all([fetch(`/api/exams/${id}`), fetch(`/api/exams/${id}/submissions`)]);
    setExam(await eRes.json());
    const s = await sRes.json();
    setSubs(Array.isArray(s)?s:[]);
    setLoading(false);
  };
  useEffect(()=>{ load(); const iv=setInterval(load,2500); return()=>clearInterval(iv); },[id]);

  if(loading) return <div className="p-8 text-center text-sm text-slate-500">Loading...</div>;
  if(!exam) return <div className="p-8 text-center">Exam not found</div>;

  const getMaxMarks = (ex:any)=> {
    if(ex.markingScheme && Array.isArray(ex.markingScheme) && ex.markingScheme.length>0){
      return ex.markingScheme.reduce((s:number,a:any)=> s + (a.to - a.from +1)* a.marks,0);
    }
    return ex.questionCount * ex.marksPerQuestion;
  };
  const formatScheme = (ex:any)=>{
    if(ex.markingScheme && ex.markingScheme.length>0){
      if(ex.markingScheme.length===1) return `${ex.markingScheme[0].marks} marks each`;
      return ex.markingScheme.map((s:any)=>`Q${s.from}-${s.to}: +${s.marks}/-${s.negativeMarks}`).join(" • ");
    }
    return `${ex.marksPerQuestion} marks each`;
  };
  const maxMarks = getMaxMarks(exam);
  const completed = subs.filter(s=>s.status==="COMPLETED"||s.status==="REVIEW_REQUIRED");

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <Link href="/" className="inline-flex items-center gap-1 text-sm font-semibold text-slate-700 hover:text-slate-900 bg-slate-100 hover:bg-slate-200 border border-slate-200 rounded-full px-4 py-2 mb-1.5 transition" title="Back to home">← Home</Link>
            <h1 className="font-bold text-slate-900 truncate leading-tight">{exam.name}</h1>
            <p className="text-xs text-slate-500 truncate">{exam.subject} • {exam.questionCount} questions • {formatScheme(exam)} • Max {maxMarks} • {subs.length} sheets</p>
          </div>
          <Link href={`/exam/${id}/scan`} className="bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white px-4 py-2.5 rounded-full text-xs font-medium shrink-0 min-h-[36px] flex items-center">Add Papers</Link>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-6">
        <div className="bg-white rounded-2xl p-4 sm:p-5 border border-slate-200 shadow-sm">
          <h2 className="font-semibold text-slate-900">Results</h2>
          <p className="text-xs text-slate-500">Students Evaluated: {completed.length} / {subs.length}</p>
          {subs.length===0 ? <p className="text-sm text-slate-400 text-center py-8">No submissions yet. <Link href={`/exam/${id}/scan`} className="text-indigo-600 underline">Scan papers</Link></p> :
            <div className="mt-4 divide-y divide-slate-100">
              {subs.map(s=>(
                <div key={s.id} className="py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-slate-900 truncate">{s.studentName || "Unknown"}</p>
                    <p className="text-xs text-slate-500 truncate">Roll: {s.rollNumber || "-"} • <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium border ${s.status==="COMPLETED"?"bg-emerald-50 text-emerald-700 border-emerald-200": s.status==="REVIEW_REQUIRED"?"bg-amber-50 text-amber-700 border-amber-200": s.status==="FAILED"?"bg-red-50 text-red-700 border-red-200": s.status==="PROCESSING"?"bg-blue-50 text-blue-700 border-blue-200":"bg-slate-100 text-slate-600 border-slate-200"}`}>{s.status}</span></p>
                    {s.status==="COMPLETED"||s.status==="REVIEW_REQUIRED" ? (
                      <p className="text-xs mt-1 text-slate-600"><span className="font-bold text-slate-900">{s.score} / {maxMarks}</span> • Correct {s.correct} • Incorrect {s.incorrect} • Blank {s.blank}</p>
                    ) : s.status==="FAILED" ? <p className="text-xs text-red-600 mt-1 line-clamp-2">{s.error || "Failed"}</p> : <p className="text-xs text-slate-400 mt-1">Processing...</p>}
                    {s.uncertainQuestions?.length>0 && <p className="text-xs text-amber-600">{s.uncertainQuestions.length} uncertain — needs review</p>}
                  </div>
                  <div className="flex flex-row sm:flex-col gap-2 shrink-0">
                    {(s.status==="COMPLETED"||s.status==="REVIEW_REQUIRED") && <Link href={`/exam/${id}/student/${s.id}`} className="text-xs bg-slate-900 hover:bg-black text-white px-4 py-2 rounded-full text-center font-medium min-h-[32px] flex items-center justify-center">View</Link>}
                    {s.status==="FAILED" && <button onClick={async()=>{ await fetch(`/api/submissions/${s.id}`,{method:"PATCH", headers:{ "Content-Type":"application/json"}, body: JSON.stringify({retry:true})}); load();}} className="text-xs bg-red-600 hover:bg-red-700 text-white px-4 py-2 rounded-full font-medium min-h-[32px]">Retry</button>}
                    {(s.status==="COMPLETED"||s.status==="REVIEW_REQUIRED") && <a href={`/api/submissions/${s.id}/report`} className="text-xs border border-slate-200 bg-white hover:bg-slate-50 px-4 py-2 rounded-full text-center font-medium min-h-[32px] flex items-center justify-center">PDF</a>}
                    <button onClick={()=> handleDelete(s.id, s.studentName || s.originalName || "this paper")} disabled={deletingId===s.id} title="Delete paper — frees backend space" className="text-xs border border-slate-200 bg-white hover:bg-red-50 hover:border-red-200 hover:text-red-600 text-slate-500 px-3 py-2 rounded-full text-center font-medium min-h-[32px] flex items-center justify-center disabled:opacity-50">
                      {deletingId===s.id ? "…" : "🗑 Delete"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          }
        </div>

        {completed.length>0 && (
          <div className="mt-6 bg-indigo-50 rounded-xl p-4 border border-indigo-100">
            <p className="text-xs font-semibold text-indigo-700">Metrics</p>
            <p className="text-xs text-slate-600 mt-1">Avg score: {(completed.reduce((a,b)=>a+(b.score||0),0)/completed.length).toFixed(1)} / {maxMarks}</p>
            <p className="text-xs text-slate-600">Flagged for review: {subs.filter(s=>s.status==="REVIEW_REQUIRED").length} / {subs.length}</p>
          </div>
        )}
      </main>
    </div>
  );
}
