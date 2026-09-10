"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";

interface Batch { id:string; name:string; academicYear?:string|null; createdAt:string; description?:string|null; }
interface Subject { id:string; batchId:string; name:string; code?:string|null; createdAt:string; }

export default function BatchPage() {
  const { batchId } = useParams<{ batchId:string }>();
  const [batch, setBatch] = useState<Batch|null>(null);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [exams, setExams] = useState<any[]>([]);
  const [students, setStudents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newCode, setNewCode] = useState("");
  const [csvFile, setCsvFile] = useState<File|null>(null);
  const [msg, setMsg] = useState("");
  const [creating, setCreating] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [bRes, sRes, eRes, stRes] = await Promise.all([
        fetch(`/api/batches/${batchId}`).then(r=>r.json()),
        fetch(`/api/subjects?batchId=${batchId}`).then(r=>r.json()),
        fetch(`/api/exams?batchId=${batchId}`).then(r=>r.json()),
        fetch(`/api/students?batchId=${batchId}`).then(r=>r.json()).catch(()=>[]),
      ]);
      setBatch(bRes?.id ? bRes : null);
      setSubjects(Array.isArray(sRes)?sRes:[]);
      setExams(Array.isArray(eRes)?eRes:[]);
      setStudents(Array.isArray(stRes)?stRes:[]);
    } finally { setLoading(false); }
  };
  useEffect(()=>{ load(); }, [batchId]);

  const createSubject = async (e:React.FormEvent)=>{
    e.preventDefault();
    if(!newName.trim()) return;
    setCreating(true); setMsg("");
    try {
      const res = await fetch("/api/subjects", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify({ batchId, name:newName.trim(), code:newCode.trim()||null })});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error||"Failed");
      setSubjects(prev=>[data, ...prev]);
      setNewName(""); setNewCode(""); setShowNew(false);
    } catch(err:any){ setMsg(err.message);} finally{ setCreating(false);}
  };

  const uploadCsv = async ()=>{
    if(!csvFile) return;
    const fd = new FormData();
    fd.append("batchId", batchId as string);
    fd.append("file", csvFile);
    setMsg("Uploading…");
    try {
      const res = await fetch("/api/students", { method:"POST", body: fd});
      const data = await res.json();
      if(!res.ok) throw new Error(data.error||"Failed");
      setMsg(`Added ${data.created ?? data.students?.length ?? 0} students`);
      const sRes = await fetch(`/api/students?batchId=${batchId}`).then(r=>r.json());
      setStudents(Array.isArray(sRes)?sRes:[]);
    } catch(err:any){ setMsg(err.message); }
  };

  if(loading) return <div className="min-h-screen bg-[#eef3ee] p-8 text-sm text-stone-500">Loading…</div>;
  if(!batch) return <div className="min-h-screen bg-[#eef3ee] p-8"><p className="text-sm text-red-600">Batch not found</p><Link href="/" className="text-sm text-[#9a3412]">← Home</Link></div>;

  return (
    <div className="min-h-screen bg-[#eef3ee]">
      <header className="border-b border-stone-200 bg-white sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-3 sm:px-4 py-3 flex items-center gap-2">
          <Link href="/" className="text-sm text-stone-500 hover:text-stone-800 min-h-[32px] flex items-center px-2 -ml-2 rounded-lg active:bg-stone-100">← Batches</Link>
          <span className="font-semibold text-stone-900 truncate">{batch.name}</span>
          <span className="hidden sm:inline text-xs text-stone-500 ml-auto">{batch.academicYear || ""}</span>
        </div>
      </header>
      <main className="max-w-3xl mx-auto px-3 sm:px-4 py-4 sm:py-6 space-y-6">
        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-stone-200 shadow-sm">
          <h1 className="text-lg font-bold text-stone-900">{batch.name}</h1>
          {batch.description && <p className="text-sm text-stone-500 mt-1">{batch.description}</p>}
          <p className="text-xs text-stone-400 mt-2">{subjects.length} subjects • {exams.length} exams • {students.length} students (auto STU-001…)</p>
        </div>

        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-stone-200 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <h2 className="font-semibold text-stone-900">Subjects — View Previous</h2>
            <button onClick={()=>setShowNew(v=>!v)} className="text-sm bg-[#9a3412] hover:bg-[#7c2d12] text-white px-4 py-2 rounded-full">+ Add Subject</button>
          </div>
          {showNew && (
            <form onSubmit={createSubject} className="mt-4 p-4 border border-stone-200 rounded-xl bg-[#eef3ee] space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <input value={newName} onChange={e=>setNewName(e.target.value)} placeholder="Subject name (Physics)" className="border border-stone-200 rounded-xl px-3 py-2.5 text-sm bg-white outline-none focus:ring-2 focus:ring-[#9a3412]" required />
                <input value={newCode} onChange={e=>setNewCode(e.target.value)} placeholder="Code (optional PHY101)" className="border border-stone-200 rounded-xl px-3 py-2.5 text-sm bg-white outline-none focus:ring-2 focus:ring-[#9a3412]" />
              </div>
              {msg && <p className="text-xs text-amber-700">{msg}</p>}
              <div className="flex gap-2 justify-end">
                <button type="button" onClick={()=>setShowNew(false)} className="px-4 py-2 text-sm border border-stone-200 rounded-full bg-white">Cancel</button>
                <button disabled={creating} className="px-5 py-2 text-sm bg-[#9a3412] text-white rounded-full disabled:opacity-50">{creating?"Adding…":"Add Subject"}</button>
              </div>
            </form>
          )}
          <div className="mt-4 space-y-2">
            {subjects.length===0 ? <p className="text-sm text-stone-400 text-center py-6 border border-dashed border-stone-200 rounded-xl">No subjects yet. Add your first subject.</p> :
              subjects.map(s=> (
                <Link key={s.id} href={`/batch/${batchId}/subject/${s.id}`} className="flex items-center justify-between p-4 border border-stone-200 rounded-xl hover:border-[#fecbb8] hover:shadow-sm bg-white transition">
                  <div>
                    <p className="font-medium text-stone-900">{s.name}</p>
                    <p className="text-xs text-stone-500">{s.code || ""} • {new Date(s.createdAt).toLocaleDateString()}</p>
                  </div>
                  <span className="text-xs text-[#9a3412] font-medium">Open →</span>
                </Link>
              ))
            }
          </div>
        </div>

        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-stone-200 shadow-sm">
          <h2 className="font-semibold text-stone-900">Recent Exams in this Batch</h2>
          <p className="text-xs text-stone-500 mb-3">Across all subjects — newest first</p>
          {exams.length===0 ? <p className="text-sm text-stone-400 text-center py-4">No exams yet. Open a subject → Create Exam.</p> :
            <div className="space-y-2">
              {exams.slice(0,10).map((e:any)=> (
                <Link key={e.id} href={`/exam/${e.id}/results`} className="block border border-stone-200 rounded-xl p-3 hover:border-[#fecbb8] transition">
                  <p className="text-sm font-medium text-stone-900 truncate">{e.name}</p>
                  <p className="text-xs text-stone-500 truncate">{e.subject} • {e.questionCount} Q • {e.status} • {new Date(e.createdAt).toLocaleDateString()}</p>
                </Link>
              ))}
            </div>
          }
        </div>

        <div className="bg-white rounded-2xl p-4 sm:p-6 border border-stone-200 shadow-sm">
          <h2 className="font-semibold text-stone-900">Students — System Generated (STU-001…)</h2>
          <p className="text-xs text-stone-500 mb-3">Auto-created on scan. Optional CSV: column <code className="bg-stone-100 px-1 py-0.5 rounded">name</code></p>
          <div className="flex flex-wrap gap-2 items-center">
            <input type="file" accept=".csv,.txt" onChange={e=>setCsvFile(e.target.files?.[0]||null)} className="text-sm border border-stone-200 rounded-xl px-3 py-2 bg-[#eef3ee] file:mr-2 file:bg-white file:border file:border-stone-200 file:rounded-full file:px-3 file:py-1 file:text-xs" />
            <button onClick={uploadCsv} disabled={!csvFile} className="text-sm bg-stone-900 hover:bg-stone-900 text-white px-4 py-2 rounded-full disabled:opacity-50">Upload CSV</button>
          </div>
          {msg && <p className="text-xs text-stone-600 mt-2">{msg}</p>}
          {students.length>0 ? (
            <div className="mt-4 max-h-[220px] overflow-auto border border-stone-200 rounded-xl divide-y">
              {students.map((s:any)=> (
                <div key={s.id} className="px-3 py-2 flex items-center justify-between text-sm">
                  <span className="font-mono text-xs bg-stone-100 border border-stone-200 rounded px-1.5 py-0.5">{s.studentCode}</span>
                  <span className="flex-1 ml-3 truncate text-stone-700">{s.name}</span>
                </div>
              ))}
            </div>
          ) : <p className="text-xs text-stone-400 mt-3">No students yet — first scan will auto-create STU-001.</p>}
        </div>
      </main>
    </div>
  );
}
