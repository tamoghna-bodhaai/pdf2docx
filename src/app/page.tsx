"use client";
import { useEffect, useState } from "react";
import Link from "next/link";

interface Batch {
  id: string;
  name: string;
  academicYear?: string | null;
  description?: string | null;
  createdAt: string;
}

export default function Home() {
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState({ name: "", academicYear: "", description: "" });
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    fetch("/api/batches").then(r=>r.json()).then(d=> { setBatches(Array.isArray(d)?d:[]); setLoading(false); }).catch(()=>setLoading(false));
  };
  useEffect(()=>{ load(); }, []);

  const createBatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) { setError("Batch name required"); return; }
    setCreating(true); setError("");
    try {
      const res = await fetch("/api/batches", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(form)});
      const data = await res.json();
      if (!res.ok) throw new Error(data.error||"Failed");
      setBatches(prev=>[data, ...prev]);
      setForm({name:"", academicYear:"", description:""});
      setShowNew(false);
    } catch (err:any){ setError(err.message); } finally { setCreating(false); }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 sm:py-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-8 h-8 sm:w-9 sm:h-9 rounded-xl bg-indigo-600 flex items-center justify-center text-white font-bold text-sm shrink-0">OC</div>
            <div className="min-w-0">
              <span className="font-bold text-sm sm:text-lg leading-none">OmiCheckr</span>
              <span className="hidden sm:inline text-xs text-slate-500 ml-2">Batch → Subject → Exam</span>
              <p className="sm:hidden text-[11px] text-slate-500 leading-none">Batch / Subject / Exam</p>
            </div>
          </div>
          <button onClick={()=>setShowNew(v=>!v)} className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 sm:px-5 py-2 sm:py-2.5 rounded-full text-sm font-medium transition shrink-0 min-h-[36px] flex items-center">+ New Batch</button>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-8">
        <div className="bg-white rounded-2xl p-5 sm:p-8 shadow-sm border border-slate-200 text-center">
          <h1 className="text-xl sm:text-2xl font-bold text-slate-900">Batch / Class Manager</h1>
          <p className="text-slate-500 mt-2 text-sm max-w-lg mx-auto leading-relaxed">Create a batch/class, add subjects, then create exams per subject. System-generated student IDs (STU-001) — no roll entry needed. View previous batches & exams.</p>
          <div className="flex flex-wrap gap-2 justify-center mt-6">
            <button onClick={()=>setShowNew(true)} className="bg-indigo-600 hover:bg-indigo-700 text-white px-8 py-3 rounded-full font-medium min-h-[44px] transition">Create Batch</button>
            <Link href="/exam/create" className="border border-slate-200 bg-white hover:bg-slate-50 text-slate-700 px-8 py-3 rounded-full font-medium min-h-[44px] transition">Quick Create Exam</Link>
          </div>
          <p className="text-xs text-slate-400 mt-3">Flow: Batch → Subject → Exam • Students auto-created on scan (STU-001…)</p>
        </div>

        {showNew && (
          <form onSubmit={createBatch} className="mt-6 bg-white rounded-2xl p-4 sm:p-6 border border-slate-200 shadow-sm space-y-4">
            <h2 className="font-semibold text-slate-900">New Batch / Class</h2>
            <label className="block space-y-1.5">
              <span className="text-sm font-medium text-slate-700">Name *</span>
              <input value={form.name} onChange={e=>setForm({...form, name:e.target.value})} placeholder="12-A 2026-27" className="w-full border border-slate-200 rounded-xl px-3 py-3 sm:py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white" required />
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="block space-y-1.5">
                <span className="text-sm font-medium text-slate-700">Academic Year</span>
                <input value={form.academicYear} onChange={e=>setForm({...form, academicYear:e.target.value})} placeholder="2026-27" className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white" />
              </label>
              <label className="block space-y-1.5">
                <span className="text-sm font-medium text-slate-700">Description</span>
                <input value={form.description} onChange={e=>setForm({...form, description:e.target.value})} placeholder="Optional" className="w-full border border-slate-200 rounded-xl px-3 py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 outline-none bg-white" />
              </label>
            </div>
            {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-xl p-3">{error}</p>}
            <div className="flex gap-2 justify-end">
              <button type="button" onClick={()=>setShowNew(false)} className="px-5 py-2.5 rounded-full text-sm border border-slate-200 bg-white">Cancel</button>
              <button disabled={creating} className="px-6 py-2.5 rounded-full text-sm bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-50">{creating?"Creating…":"Create Batch"}</button>
            </div>
          </form>
        )}

        <div className="mt-6 sm:mt-8">
          <h2 className="font-semibold text-sm text-slate-700 mb-3">Batches / Classes — View Previous</h2>
          {loading ? <p className="text-sm text-slate-400">Loading…</p> :
            batches.length===0 ? <p className="text-sm text-slate-400 bg-white rounded-xl p-6 text-center border border-slate-200">No batches yet. Create your first batch above.</p> :
            <div className="space-y-3">
              {batches.map(b=> (
                <Link key={b.id} href={`/batch/${b.id}`} className="block bg-white rounded-xl p-4 border border-slate-200 hover:shadow-md hover:border-indigo-200 transition active:scale-[0.98]">
                  <div className="flex justify-between items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-slate-900 truncate">{b.name}</p>
                      <p className="text-xs text-slate-500 truncate">{b.academicYear || "No year"} • {new Date(b.createdAt).toLocaleDateString()}</p>
                      {b.description && <p className="text-xs text-slate-400 truncate mt-1">{b.description}</p>}
                    </div>
                    <span className="text-xs text-indigo-600 font-medium shrink-0">Open →</span>
                  </div>
                </Link>
              ))}
            </div>
          }
        </div>

        <div className="mt-8 bg-indigo-50 rounded-xl p-4 border border-indigo-100">
          <p className="text-xs font-semibold text-indigo-700">How it works</p>
          <ol className="text-xs text-slate-600 mt-2 space-y-1.5 list-decimal list-inside leading-relaxed">
            <li>Create Batch/Class (e.g. 12-A)</li>
            <li>Add Subject (Physics, Chemistry…)</li>
            <li>Create Exam per Subject → scan sheets → system assigns STU-001…</li>
            <li>View previous batches/exams + batch-wise & student-wise analytics</li>
          </ol>
        </div>
      </main>
    </div>
  );
}
