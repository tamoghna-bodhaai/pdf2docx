"use client";
import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

const OPTIONS = ["A","B","C","D","E","BLANK","MULTIPLE","UNCERTAIN"];

export default function StudentReportPage(){
  const { id, submissionId } = useParams() as { id:string, submissionId:string };
  const router = useRouter();
  const [exam, setExam] = useState<any>(null);
  const [sub, setSub] = useState<any>(null);
  const [deleting, setDeleting] = useState(false);
  const [editMode, setEditMode] = useState(false);
  const [draftAnswers, setDraftAnswers] = useState<Record<string,string>>({});
  const [draftName, setDraftName] = useState("");
  const [draftRoll, setDraftRoll] = useState("");
  const [saving, setSaving] = useState(false);

  const load = async ()=>{
    const [eRes,sRes] = await Promise.all([fetch(`/api/exams/${id}`), fetch(`/api/submissions/${submissionId}`)]);
    const e = await eRes.json(); setExam(e);
    const s = await sRes.json(); setSub(s);
    if(s.extractedAnswers) setDraftAnswers(s.extractedAnswers);
    if(s.studentName) setDraftName(s.studentName);
    if(s.rollNumber) setDraftRoll(s.rollNumber);
  };
  useEffect(()=>{ load(); },[id, submissionId]);

  const save = async ()=>{
    setSaving(true);
    const res = await fetch(`/api/submissions/${submissionId}`,{ method:"PATCH", headers:{ "Content-Type":"application/json"}, body: JSON.stringify({ extractedAnswers: draftAnswers, studentName: draftName, rollNumber: draftRoll })});
    const data = await res.json();
    setSub(data);
    setEditMode(false);
    setSaving(false);
  };

  if(!exam || !sub) return <div className="p-8 text-center text-sm text-slate-500">Loading...</div>;
  const maxMarks = exam.markingScheme && Array.isArray(exam.markingScheme) ? exam.markingScheme.reduce((s:number,a:any)=> s + (a.to - a.from +1)* a.marks,0) : exam.questionCount * exam.marksPerQuestion;
  const schemeSummary = exam.markingScheme && exam.markingScheme.length>1 ? exam.markingScheme.map((s:any)=>`Q${s.from}-${s.to}: +${s.marks}/-${s.negativeMarks}`).join(" • ") : `${exam.marksPerQuestion} marks each`;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <Link href={`/exam/${id}/results`} className="text-xs text-slate-500 hover:text-slate-700">← Results</Link>
            <h1 className="font-bold text-slate-900 truncate">{exam.name}</h1>
            <p className="text-xs text-slate-500">{exam.subject} • {schemeSummary} • Max {maxMarks}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={async()=>{
                if(!confirm(`Delete "${sub?.studentName || sub?.originalName || "this paper"}"? Frees backend space. Cannot be undone.`)) return;
                setDeleting(true);
                try{
                  const res = await fetch(`/api/submissions/${submissionId}`, { method: "DELETE" });
                  const data = await res.json().catch(()=>({}));
                  if(!res.ok) throw new Error(data.error || "Delete failed");
                  router.push(`/exam/${id}/scan`);
                }catch(e:any){ alert(e.message||"Delete failed"); setDeleting(false); }
              }}
              disabled={deleting}
              className="bg-white border border-slate-200 hover:bg-red-50 hover:border-red-200 hover:text-red-600 text-slate-500 px-3 py-2.5 rounded-full text-xs font-medium min-h-[36px] flex items-center disabled:opacity-50"
              title="Delete paper — frees backend space"
            >
              {deleting ? "…" : "🗑 Delete"}
            </button>
            <a href={`/api/submissions/${submissionId}/report`} className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2.5 rounded-full text-xs font-medium min-h-[36px] flex items-center">Download PDF</a>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-6 space-y-4 sm:space-y-6">
        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-slate-200 shadow-sm">
          {!editMode ? (
            <>
              <div className="flex justify-between items-start gap-3">
                <div className="min-w-0">
                  <p className="text-xs text-slate-500">Student</p>
                  <p className="font-bold text-lg text-slate-900 truncate">{sub.studentName || "Unknown"}</p>
                  <p className="text-sm text-slate-500">Roll: {sub.rollNumber || "-"}</p>
                </div>
                <button onClick={()=>setEditMode(true)} className="text-xs border border-slate-200 bg-white hover:bg-slate-50 px-4 py-2 rounded-full font-medium shrink-0 min-h-[32px]">Edit</button>
              </div>
              <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-2 sm:gap-3 text-center">
                <div className="bg-slate-50 rounded-xl p-3 border border-slate-100"><p className="text-lg font-bold text-slate-900">{sub.score} / {maxMarks}</p><p className="text-xs text-slate-500">Score</p></div>
                <div className="bg-emerald-50 rounded-xl p-3 border border-emerald-100"><p className="text-lg font-bold text-emerald-700">{sub.correct}</p><p className="text-xs text-slate-500">Correct</p></div>
                <div className="bg-red-50 rounded-xl p-3 border border-red-100"><p className="text-lg font-bold text-red-600">{sub.incorrect}</p><p className="text-xs text-slate-500">Incorrect</p></div>
                <div className="bg-slate-50 rounded-xl p-3 border border-slate-100"><p className="text-lg font-bold text-slate-900">{sub.blank}</p><p className="text-xs text-slate-500">Blank</p></div>
              </div>
              {sub.status==="REVIEW_REQUIRED" && <p className="text-xs bg-amber-50 text-amber-800 px-3 py-2.5 rounded-xl mt-3 border border-amber-200">⚠ Contains uncertain/multiple markings — please review.</p>}
            </>
          ) : (
            <div className="space-y-4">
              <h3 className="font-semibold text-slate-900">Edit Student & Answers</h3>
              <label className="block"><span className="text-xs font-medium text-slate-700">Student Name</span><input value={draftName} onChange={e=>setDraftName(e.target.value)} className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm mt-1 bg-white focus:ring-2 focus:ring-indigo-500 outline-none" /></label>
              <label className="block"><span className="text-xs font-medium text-slate-700">Roll Number</span><input value={draftRoll} onChange={e=>setDraftRoll(e.target.value)} className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm mt-1 bg-white focus:ring-2 focus:ring-indigo-500 outline-none" /></label>
              <div className="grid grid-cols-3 xs:grid-cols-4 sm:grid-cols-5 gap-2 max-h-64 overflow-y-auto pr-1">
                {Array.from({length: exam.questionCount}, (_,i)=>{
                  const q=String(i+1);
                  return (
                    <label key={q} className="border border-slate-200 rounded-xl p-2 bg-slate-50 text-center">
                      <span className="text-xs font-medium text-slate-600">Q{q}</span>
                      <select value={draftAnswers[q]||"BLANK"} onChange={e=>setDraftAnswers({...draftAnswers, [q]: e.target.value})} className="w-full mt-1 border border-slate-200 rounded-lg px-1 py-2 text-xs font-bold bg-white min-h-[36px]">
                        {OPTIONS.map(o=> <option key={o} value={o}>{o}</option>)}
                      </select>
                    </label>
                  );
                })}
              </div>
              <div className="flex gap-3">
                <button onClick={()=>setEditMode(false)} className="flex-1 border border-slate-200 bg-white rounded-xl py-3 text-sm font-medium min-h-[44px]">Cancel</button>
                <button onClick={save} disabled={saving} className="flex-1 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl py-3 text-sm disabled:opacity-50 min-h-[44px] font-medium">{saving?"Saving...":"Save & Recalculate"}</button>
              </div>
            </div>
          )}
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
            <h3 className="font-semibold text-sm text-slate-900">Question Details</h3>
            <span className="text-xs text-slate-500 bg-white border border-slate-200 px-2 py-1 rounded-full">{sub.details?.length || 0} questions</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-xs text-slate-500">
                <tr><th className="px-3 py-2.5 text-left font-medium">Q</th><th className="font-medium">Student</th><th className="font-medium">Correct</th><th className="font-medium">Result</th><th className="font-medium">Marks</th></tr>
              </thead>
              <tbody>
                {sub.details?.map((d:any)=>(
                  <tr key={d.question} className="border-t border-slate-100 text-center hover:bg-slate-50/50">
                    <td className="px-3 py-2.5 font-medium text-slate-900">{d.question}</td>
                    <td className="py-2.5 font-mono text-slate-700">{d.studentAnswer}</td>
                    <td className="py-2.5 font-mono text-slate-700">{d.correctAnswer}</td>
                    <td className={`py-2.5 text-xs font-medium ${d.result==="Correct"?"text-emerald-600": d.result==="Incorrect"?"text-red-600":"text-amber-600"}`}>{d.result}</td>
                    <td className="py-2.5 text-slate-700">{d.marks}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-white rounded-2xl p-4 border border-slate-200 shadow-sm">
          <p className="text-xs font-semibold text-slate-700">Sheet Image</p>
          <img src={sub.imageUrl} alt="sheet" className="mt-2 w-full max-h-96 object-contain bg-slate-100 rounded-xl border border-slate-100" onError={(e)=>{(e.target as HTMLImageElement).style.display='none'}} />
          <p className="text-xs text-slate-400 mt-2 truncate">{sub.originalName}</p>
        </div>
      </main>
    </div>
  );
}
