import { NextRequest, NextResponse } from "next/server";
import { v4 as uuidv4 } from "uuid";
import path from "path";
import fs from "fs";
import { readExams, writeExams } from "@/lib/db";
import { extractAnswerKey } from "@/lib/answerKeyExtractor";
import { validateMarkingScheme, normalizeMarkingScheme } from "@/lib/markingScheme";
import { MarkingSchemeSection, SheetType } from "@/lib/types";

export async function GET() {
  const exams = readExams().sort((a,b)=> new Date(b.createdAt).getTime()-new Date(a.createdAt).getTime());
  return NextResponse.json(exams);
}

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const name = String(formData.get("name") || "").trim();
    const subject = String(formData.get("subject") || "").trim();
    const questionCount = parseInt(String(formData.get("questionCount") || "0"),10);
    let marksPerQuestion = parseFloat(String(formData.get("marksPerQuestion") || "1"));
    let negativeMarks = parseFloat(String(formData.get("negativeMarks") || "0"));
    const markingSchemeRaw = String(formData.get("markingScheme") || "").trim();
    const sheetTypeRaw = String(formData.get("sheetType") || "auto").trim().toLowerCase();
    const sheetType: SheetType = (["bubble","handwritten","auto"].includes(sheetTypeRaw) ? sheetTypeRaw : "auto") as SheetType;
    const questionPaper = formData.get("questionPaper") as File | null;
    const answerKeyFile = formData.get("answerKey") as File | null;
    const manualAnswerKey = String(formData.get("manualAnswerKey") || "").trim();

    if (!name || !subject || !questionCount || questionCount < 1 || questionCount > 200) {
      return NextResponse.json({ error: "Invalid exam fields" }, { status: 400 });
    }

    // Parse markingScheme if provided (variable scheme)
    let markingScheme: MarkingSchemeSection[];
    if (markingSchemeRaw) {
      try {
        const parsed = JSON.parse(markingSchemeRaw);
        if (!Array.isArray(parsed)) throw new Error("markingScheme must be an array");
        markingScheme = parsed.map((s: any) => ({
          from: parseInt(String(s.from), 10),
          to: parseInt(String(s.to), 10),
          marks: parseFloat(String(s.marks)),
          negativeMarks: parseFloat(String(s.negativeMarks)),
        }));
        const err = validateMarkingScheme(markingScheme, questionCount);
        if (err) return NextResponse.json({ error: err }, { status: 400 });
        markingScheme = normalizeMarkingScheme(markingScheme);
        // Sync legacy fields for backwards compat (use first section if variable? Keep computed fallback to first)
        // For variable scheme, keep marksPerQuestion/negativeMarks as weighted avg is not meaningful; keep first section values for legacy display
        // Better keep them as 0 and rely on markingScheme; but keep for compat:
        marksPerQuestion = markingScheme[0].marks;
        negativeMarks = markingScheme[0].negativeMarks;
      } catch (e: any) {
        return NextResponse.json({ error: e.message || "Invalid markingScheme" }, { status: 400 });
      }
    } else {
      // Uniform fallback
      if (isNaN(marksPerQuestion) || isNaN(negativeMarks)) {
        return NextResponse.json({ error: "Invalid marks" }, { status: 400 });
      }
      markingScheme = [{ from: 1, to: questionCount, marks: marksPerQuestion, negativeMarks }];
    }

    const id = uuidv4();
    let questionPaperUrl: string | null = null;
    let questionPaperName: string | null = null;
    let answerKeyUrl: string | null = null;
    let answerKeyName: string | null = null;
    let answerKeyJson: Record<string,string> | null = null;

    // Save question paper if provided
    if (questionPaper && questionPaper.size > 0) {
      const ext = questionPaper.name.split(".").pop() || "pdf";
      const filename = `${id}-qp.${ext}`;
      const dir = path.join(process.cwd(), "public", "uploads", "question-papers");
      fs.mkdirSync(dir, { recursive: true });
      const buffer = Buffer.from(await questionPaper.arrayBuffer());
      fs.writeFileSync(path.join(dir, filename), buffer);
      questionPaperUrl = `/uploads/question-papers/${filename}`;
      questionPaperName = questionPaper.name;
    }

    // Save answer key file if provided
    let answerKeyBuffer: Buffer | null = null;
    let answerKeyFilename: string | null = null;
    if (answerKeyFile && answerKeyFile.size > 0) {
      const ext = answerKeyFile.name.split(".").pop() || "pdf";
      const filename = `${id}-ak.${ext}`;
      const dir = path.join(process.cwd(), "public", "uploads", "answer-keys");
      fs.mkdirSync(dir, { recursive: true });
      answerKeyBuffer = Buffer.from(await answerKeyFile.arrayBuffer());
      fs.writeFileSync(path.join(dir, filename), answerKeyBuffer);
      answerKeyUrl = `/uploads/answer-keys/${filename}`;
      answerKeyName = answerKeyFile.name;
      answerKeyFilename = answerKeyFile.name;
    }

    // Try manual answer key if provided
    if (manualAnswerKey) {
      try {
        // Try parse JSON
        const parsed = JSON.parse(manualAnswerKey);
        if (typeof parsed === "object" && parsed !== null) {
          answerKeyJson = {};
          for (const [k,v] of Object.entries(parsed)) {
            if (/^[A-D]$/i.test(String(v).trim())) answerKeyJson[String(parseInt(k,10))] = String(v).trim().toUpperCase();
          }
        }
      } catch {
        // Try comma/line separated like "B,D,A,C"
        const tokens = manualAnswerKey.split(/[\n,\s;]+/).map(s=>s.trim().toUpperCase()).filter(s=>/^[A-D]$/.test(s));
        if (tokens.length >= questionCount) {
          answerKeyJson = {};
          for (let i=0;i<questionCount;i++) answerKeyJson[String(i+1)] = tokens[i];
        } else if (tokens.length > 0 && tokens.length < questionCount) {
          // partial
          answerKeyJson = {};
          for (let i=0;i<tokens.length;i++) answerKeyJson[String(i+1)] = tokens[i];
        }
      }
    }

    // If file exists and no manual json, try LLM/Regex extraction
    if (answerKeyBuffer && !answerKeyJson) {
      try {
        answerKeyJson = await extractAnswerKey(answerKeyBuffer, answerKeyFilename!, questionCount);
      } catch (e:any) {
        console.warn("Answer key extraction failed", e.message);
        // leave null, will require manual entry in verify step
      }
    }

    const exams = readExams();
    const exam = {
      id,
      name,
      subject,
      questionCount,
      marksPerQuestion,
      negativeMarks,
      markingScheme,
      sheetType,
      questionPaperUrl,
      questionPaperName,
      answerKeyUrl,
      answerKeyName,
      answerKeyJson,
      createdAt: new Date().toISOString(),
      status: answerKeyJson ? "answer_key_ready" as const : "draft" as const,
    };
    exams.push(exam);
    writeExams(exams);

    return NextResponse.json(exam, { status: 201 });
  } catch (e:any) {
    console.error(e);
    return NextResponse.json({ error: e.message || "Failed to create exam" }, { status: 500 });
  }
}
