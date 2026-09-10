"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

interface Subject { id:string; batchId:string; name:string; code?:string|null; createdAt:string; }
interface Batch { id:string; name:string; }

export default function SubjectPage() {
  const { batchId, subjectId } = useParams<{ batchId:string; subjectId:string }>();
  const [batch, setBatch] = useState<Batch|null>(null);
  const [subject, setSubject] = useState<Subject|null>(null);
  const [exams, setExams] = useState<any[]>([]);
  const [analytics, setAnalytics] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string|null>(null);

  const load = async ()=>{
    setLoading(true);
    try{
      const [bRes, sRes, eRes] = await Promise.all([
        fetch(`/api/batches/${batchId}`).then(r=>r.json()),
        fetch(`/api/subjects/${subjectId}`).then(r=>r.json()),
        fetch(`/api/exams?subjectId=${subjectId}`).then(r=>r.json()),
      ]);
      setBatch(bRes?.id?bRes:null);
      setSubject(sRes?.id?sRes:null);
      setExams(Array.isArray(eRes)?eRes:[]);
      // analytics
      try{
        const a = await fetch(`/api/analytics/subject/${subjectId}`).then(r=>r.json());
        if(a && !a.error) setAnalytics(a);
      }catch{}
    } finally{ setLoading(false); }
  };
  useEffect(()=>{ load(); }, [batchId, subjectId]);

  const deleteExam = async (id:string, name:string)=>{
    if(!confirm(`Delete exam "${name}"?`)) return;
    setDeletingId(id);
    try{
      const res = await fetch(`/api/exams/${id}`, { method:"DELETE" });
      const data = await res.json().catch(()=>({}));
      if(!res.ok) throw new Error(data.error||"Failed");
      setExams(prev=>prev.filter(e=>e.id!==id));
    } catch(e:any){ alert(e.message);} finally{ setDeletingId(null); }
  };

  if(loading) return <div className="min-h-screen bg-slate-50 p-8 text-sm text-slate-500">Loading…</div>;
  if(!subject) return <div className="min-h-screen bg-slate-50 p-8"><p className="text-sm text-red-600">Subject not found</p><Link href={`/batch/${batchId}`} className="text-sm text-indigo-600">← Back</Link></div>;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 flex items-center gap-2">
          <Link href={`/batch/${batchId}`} className="text-sm text-slate-500 hover:text-slate-800 min-h-[32px] flex items-center px-2 -ml-2 rounded-lg active:bg-slate-100">← {batch?.name || "Batch"}</Link>
          <span className="font-semibold text-slate-900 truncate">{subject.name}</span>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-6 space-y-6">
        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-slate-200 shadow-sm">
          <h1 className="text-lg font-bold text-slate-900">{subject.name}</h1>
          {subject.code && <p className="text-xs text-slate-500 mt-1">Code: {subject.code}</p>}
          <p className="text-xs text-slate-400 mt-2">{exams.length} exams • {analytics ? `${analytics.totalSubmissions ?? 0} sheets` : ""}</p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Link href={`/exam/create?batchId=${batchId}&subjectId=${subjectId}`} className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-3 rounded-full text-sm font-medium min-h-[44px] flex items-center justify-center">+ Create Exam</Link>
          <Link href={`/batch/${batchId}`} className="border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 px-6 py-3 rounded-full text-sm font-medium min-h-[44px] flex items-center">View Batch</Link>
        </div>

        {analytics && (
          <div className="bg-white rounded-2xl p-4 sm:p-6 border border-slate-200 shadow-sm">
            <h2 className="font-semibold text-slate-900 text-sm mb-3">Analytics — Batch-wise / Student-wise</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
              <div className="bg-slate-50 rounded-xl p-3 border border-slate-200">
                <p className="text-xs text-slate-500">Exams</p>
                <p className="text-lg font-bold text-slate-900">{analytics.examCount ?? exams.length}</p>
              </div>
              <div className="bg-slate-50 rounded-xl p-3 border border-slate-200">
                <p className="text-xs text-slate-500">Sheets</p>
                <p className="text-lg font-bold text-slate-900">{analytics.totalSubmissions ?? 0}</p>
              </div>
              <div className="bg-indigo-50 rounded-xl p-3 border border-indigo-200">
                <p className="text-xs text-indigo-700">Avg Score</p>
                <p className="text-lg font-bold text-indigo-900">{analytics.avgScore != null ? analytics.avgScore.toFixed(1) : "—"}</p>
              </div>
              <div className="bg-emerald-50 rounded-xl p-3 border border-emerald-200">
                <p className="text-xs text-emerald-700">Students</p>
                <p className="text-lg font-bold text-emerald-900">{analytics.distinctStudents ?? 0}</p>
              </div>
            </div>
            {analytics.topStudents?.length>0 && (
              <div className="mt-4">
                <p className="text-xs font-semibold text-slate-700 mb-2">Top Students (avg across exams in this subject)</p>
                <div className="border border-slate-200 rounded-xl divide-y">
                  {analytics.topStudents.slice(0,5).map((s:any)=>(
                    <div key={s.studentId} className="px-3 py-2 flex items-center justify-between text-sm">
                      <span><span className="font-mono text-xs bg-slate-100 border rounded px-1 py-0.5 mr-2">{s.studentCode}</span>{s.name}</span>
                      <span className="font-semibold text-slate-900">{s.avgScore?.toFixed(1)} avg • {s.count} exams</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {analytics.qWise && (
              <div className="mt-4">
                <p className="text-xs font-semibold text-slate-700 mb-2">Q-wise % Correct (subject aggregate)</p>
                <div className="grid grid-cols-5 sm:grid-cols-8 gap-1.5">
                  {Object.entries(analytics.qWise).map(([q, v]:any)=>(
                    <div key={q} className="text-center border border-slate-200 rounded-lg py-1">
                      <p className="text-[11px] text-slate-500">Q{q}</p>
                      <p className={`text-xs font-semibold ${v>=70?"text-emerald-600":v>=40?"text-amber-600":"text-red-600"}`}>{v.toFixed(0)}%</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-slate-200 shadow-sm">
          <h2 className="font-semibold text-slate-900">Exams — View Previous (chronological)</h2>
          <p className="text-xs text-slate-500 mb-3">Newest first • View previous exams in this subject</p>
          {exams.length===0 ? <p className="text-sm text-slate-400 text-center py-8 border border-dashed border-slate-200 rounded-xl">No exams yet. Create your first exam above.</p> :
            <div className="space-y-3">
              {exams.map((e:any)=> (
                <div key={e.id} className="group relative border border-slate-200 rounded-xl p-4 hover:shadow-sm hover:border-indigo-200 bg-white transition">
                  <Link href={`/exam/${e.id}/results`} className="block pr-8">
                    <p className="font-medium text-slate-900 truncate">{e.name}</p>
                    <p className="text-xs text-slate-500 truncate">{e.questionCount} Q • {e.status} • {new Date(e.createdAt).toLocaleDateString()}</p>
                    <span className="text-xs text-indigo-600 font-medium mt-2 inline-block">Open → results</span>
                  </Link>
                  <div className="absolute top-3 right-3 flex items-center gap-1">
                    <Link href={`/exam/${e.id}/scan`} className="text-xs border border-slate-200 bg-white hover:bg-indigo-50 rounded-full px-3 py-1">Scan</Link>
                    <button onClick={()=>deleteExam(e.id, e.name)} disabled={deletingId===e.id} className="w-7 h-7 rounded-full border border-slate-200 bg-white hover:bg-red-50 hover:border-red-200 text-slate-400 hover:text-red-600 flex items-center justify-center disabled:opacity-50" title="Delete exam">
                      {deletingId===e.id ? <span className="w-3 h-3 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin"/> : "×"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          }
        </div>
      </main>
    </div>
  );
}
