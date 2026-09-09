"use client";
import { useEffect, useState } from "react";
import Link from "next/link";

interface Exam {
  id: string;
  name: string;
  subject: string;
  questionCount: number;
  createdAt: string;
  status: string;
}

export default function Home() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const loadExams = () => {
    fetch("/api/exams").then(r=>r.json()).then(d=> { setExams(Array.isArray(d)?d:[]); setLoading(false); }).catch(()=>setLoading(false));
  };

  useEffect(() => {
    loadExams();
  }, []);

  const deleteExam = async (id: string, name: string) => {
    if (!confirm(`Delete exam "${name}"? This will permanently remove the exam and all its student sheets. This cannot be undone.`)) return;
    setDeletingId(id);
    try {
      const res = await fetch(`/api/exams/${id}`, { method: "DELETE" });
      const data = await res.json().catch(()=>({}));
      if (!res.ok) throw new Error(data.error || "Failed to delete");
      setExams(prev => prev.filter(e => e.id !== id));
    } catch (e: any) {
      alert(e.message || "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-indigo-600 flex items-center justify-center text-white font-bold text-sm shrink-0">OC</div>
            <div className="min-w-0">
              <span className="font-bold text-sm sm:text-lg leading-none">OmiCheckr</span>
              <span className="hidden sm:inline text-xs text-slate-500 ml-2">Vision LLM Bubble Sheet Grader</span>
              <p className="sm:hidden text-[11px] text-slate-500 leading-none">Vision LLM Grader</p>
            </div>
          </div>
          <Link href="/exam/create" className="bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white px-4 sm:px-5 py-2 sm:py-2.5 rounded-full text-sm font-medium transition shrink-0 min-h-[36px] flex items-center">Create Exam</Link>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-8">
        <div className="bg-white rounded-2xl p-5 sm:p-8 shadow-sm border border-slate-200 text-center">
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900">Bubble Sheet Checker</h1>
          <p className="text-slate-500 mt-2 text-sm max-w-lg mx-auto leading-relaxed">Create an exam, upload question paper & answer key, then scan student sheets with Vision LLM. Batch upload supported — no waiting between scans.</p>
          <Link href="/exam/create" className="inline-flex items-center justify-center mt-6 bg-indigo-600 hover:bg-indigo-700 active:bg-indigo-800 text-white px-8 py-3 rounded-full font-medium w-full sm:w-auto min-h-[44px] transition">Create Exam</Link>
          <p className="text-xs text-slate-400 mt-3">Supports PDF/DOCX • JPG/PNG/PDF sheets • Auto grading</p>
        </div>

        <div className="mt-6 sm:mt-8">
          <h2 className="font-semibold text-sm text-slate-700 mb-3">Recent Exams</h2>
          {loading ? <p className="text-sm text-slate-400">Loading...</p> :
            exams.length === 0 ? <p className="text-sm text-slate-400 bg-white rounded-xl p-6 text-center border border-slate-200">No exams yet. Create your first exam above.</p> :
            <div className="space-y-3">
              {exams.map(e => (
                <div key={e.id} className="group bg-white rounded-xl p-4 border border-slate-200 hover:shadow-md hover:border-indigo-200 transition relative">
                  <Link href={`/exam/${e.id}/scan`} className="block">
                    <div className="flex justify-between items-start gap-3 pr-8">
                      <div className="min-w-0 flex-1">
                        <p className="font-medium text-slate-900 truncate">{e.name}</p>
                        <p className="text-xs text-slate-500 truncate">{e.subject} • {e.questionCount} questions • {new Date(e.createdAt).toLocaleDateString()}</p>
                      </div>
                    </div>
                    <div className="mt-3 flex gap-2">
                      <span className="text-xs text-indigo-600 font-medium">Open →</span>
                    </div>
                  </Link>
                  <button
                    onClick={(ev) => { ev.preventDefault(); ev.stopPropagation(); deleteExam(e.id, e.name); }}
                    disabled={deletingId === e.id}
                    className="absolute top-3 right-3 w-8 h-8 rounded-full border border-slate-200 bg-white hover:bg-red-50 hover:border-red-200 hover:text-red-600 text-slate-400 flex items-center justify-center transition disabled:opacity-50"
                    title="Delete exam — frees DB (MVP)"
                    aria-label={`Delete ${e.name}`}
                  >
                    {deletingId === e.id ? <span className="w-3 h-3 border-2 border-slate-300 border-t-slate-600 rounded-full animate-spin" /> : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>}
                  </button>
                </div>
              ))}
            </div>
          }
        </div>

        <div className="mt-8 sm:mt-10 bg-indigo-50 rounded-xl p-4 border border-indigo-100">
          <p className="text-xs font-semibold text-indigo-700">How it works</p>
          <ol className="text-xs text-slate-600 mt-2 space-y-1.5 list-decimal list-inside leading-relaxed">
            <li>Create exam → upload paper & key</li>
            <li>Verify extracted answer key</li>
            <li>Batch upload or Take Photo → Submit & Next (no waiting)</li>
            <li>Repeat for any number of students</li>
            <li>Review results & download PDF reports</li>
          </ol>
        </div>
      </main>
    </div>
  );
}
