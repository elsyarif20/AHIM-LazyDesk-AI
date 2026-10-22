import base64
import csv
import io
import json
import os
import re
from datetime import datetime
from flask import Flask, jsonify, render_template, request, send_file
import keyring
import requests
from ollama import Client, ResponseError

app = Flask(__name__)
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_TEXT_CHARS = 60000
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
PROVIDERS = {"ollama_cloud", "openai", "gemini"}
PROVIDER_INFO = {
    "ollama_cloud": ("AHIM LazyDesk AI / Ollama Cloud", "OLLAMA_API_KEY", "gemma4:31b"),
    "openai": ("AHIM LazyDesk AI / OpenAI", "OPENAI_API_KEY", "gpt-4.1-mini"),
    "gemini": ("AHIM LazyDesk AI / Gemini", "GEMINI_API_KEY", "gemini-2.5-flash"),
}


def get_key(provider):
    service, env_name, _ = PROVIDER_INFO[provider]
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value
    try:
        return (keyring.get_password(service, env_name) or "").strip()
    except Exception:
        return ""


def save_key(provider, value):
    service, env_name, _ = PROVIDER_INFO[provider]
    keyring.set_password(service, env_name, value)


def delete_key(provider):
    service, env_name, _ = PROVIDER_INFO[provider]
    try:
        keyring.delete_password(service, env_name)
    except Exception:
        pass


def provider_chat(provider, api_key, prompt, content, model=None, images=None, mime_type="image/png"):
    model = model or os.getenv({"ollama_cloud":"OLLAMA_CLOUD_MODEL", "openai":"OPENAI_MODEL", "gemini":"GEMINI_MODEL"}[provider], PROVIDER_INFO[provider][2])
    if provider == "ollama_cloud":
        client = Client(host="https://ollama.com", headers={"Authorization": "Bearer " + api_key})
        user = {"role": "user", "content": content}
        if images:
            user["images"] = [base64.b64decode(img) for img in images]
        try:
            response = client.chat(model=model, messages=[{"role":"system","content":prompt}, user], stream=False)
        except ResponseError as exc:
            raise RuntimeError(f"Ollama Cloud gagal (HTTP {getattr(exc, 'status_code', 'unknown')}). Periksa API key, model, dan kuota.") from None
        message = getattr(response, "message", None)
        result = getattr(message, "content", "") if message else ""
        if isinstance(response, dict):
            result = (response.get("message") or {}).get("content", result)
    elif provider == "openai":
        user_content = [{"type":"text", "text":content}]
        for image in images or []:
            user_content.append({"type":"image_url", "image_url":{"url":f"data:{mime_type};base64,{image}"}})
        response = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":"Bearer " + api_key, "Content-Type":"application/json"},
            json={"model":model,"messages":[{"role":"system","content":prompt},{"role":"user","content":user_content}],"temperature":0.2},timeout=180)
        if not response.ok:
            raise RuntimeError(f"OpenAI API gagal (HTTP {response.status_code}). Periksa API key, model, dan kuota.")
        result = response.json()["choices"][0]["message"]["content"]
    elif provider == "gemini":
        parts = [{"text":prompt + "\n\n" + content}]
        for image in images or []:
            parts.append({"inline_data":{"mime_type":mime_type,"data":image}})
        response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type":"application/json"},json={"contents":[{"parts":parts}],"generationConfig":{"temperature":0.2}},timeout=180)
        if not response.ok:
            raise RuntimeError(f"Gemini API gagal (HTTP {response.status_code}). Periksa API key, model, dan kuota.")
        candidates = response.json().get("candidates", [])
        result = "".join(p.get("text", "") for p in (candidates[0].get("content", {}).get("parts", []) if candidates else []))
    else:
        raise RuntimeError("Provider tidak dikenal.")
    result = (result or "").strip()
    if not result:
        raise RuntimeError("Provider mengembalikan respons kosong.")
    return result, model


def read_file(filename, raw):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in {"txt", "md", "csv", "json", "log"}:
        return raw.decode("utf-8-sig", errors="replace"), [], "text/plain", None
    if ext in {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}:
        mime = {"png":"image/png", "jpg":"image/jpeg", "jpeg":"image/jpeg", "webp":"image/webp", "bmp":"image/bmp", "tif":"image/tiff", "tiff":"image/tiff"}[ext]
        return "", [base64.b64encode(raw).decode("ascii")], mime, None
    if ext == "docx":
        from docx import Document
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            parts.append("\n[TABEL]")
            parts.extend(" | ".join(c.text.replace("\n", " ").strip() for c in row.cells) for row in table.rows)
        return "\n".join(parts), [], "text/plain", None
    if ext in {"xlsx", "xlsm"}:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        parts = []
        for sheet in wb.worksheets:
            parts.append(f"\n[LEMBAR: {sheet.title}]")
            for row in sheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(value.strip() for value in values):
                    parts.append(" | ".join(values))
        wb.close()
        return "\n".join(parts), [], "text/plain", None
    if ext == "pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            raise ValueError("PDF terkunci kata sandi atau terenkripsi.")
        count = len(reader.pages)
        if count < 1:
            raise ValueError("PDF tidak memiliki halaman.")
        if count > MAX_PDF_PAGES:
            raise ValueError(f"PDF memiliki {count} halaman. Batas maksimum adalah {MAX_PDF_PAGES} halaman per file.")
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        if text:
            return f"[PDF · {count} halaman]\n{text}", [], "text/plain", count
        import fitz
        doc = fitz.open(stream=raw, filetype="pdf")
        images = [base64.b64encode(page.get_pixmap(matrix=fitz.Matrix(0.85, 0.85), alpha=False).tobytes("jpeg")).decode("ascii") for page in doc]
        doc.close()
        return f"[PDF scan · {count} halaman. Baca teks, tabel, dan elemen visual; pertahankan urutan halaman.]", images, "image/jpeg", count
    raise ValueError("Format file tidak didukung.")


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def config():
    provider = request.args.get("provider", "ollama_cloud")
    if provider not in PROVIDERS:
        provider = "ollama_cloud"
    configured = bool(get_key(provider))
    env_name = {"ollama_cloud":"OLLAMA_CLOUD_MODEL","openai":"OPENAI_MODEL","gemini":"GEMINI_MODEL"}[provider]
    return jsonify({"cloudConfigured":configured, "model":os.getenv(env_name, PROVIDER_INFO[provider][2])})


@app.post("/api/settings/key")
def save_key_route():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "ollama_cloud")
    api_key = (data.get("apiKey") or "").strip()
    if provider not in PROVIDERS:
        return jsonify({"error":"Provider tidak dikenal."}), 400
    if not api_key or len(api_key) > 1000 or "\n" in api_key or "\r" in api_key:
        return jsonify({"error":"API key kosong atau formatnya tidak valid."}), 400
    try:
        reply, model = provider_chat(provider, api_key, "You are a connection test. Reply briefly in Indonesian.", "Konfirmasi koneksi AHIM LazyDesk AI.")
        save_key(provider, api_key)
        return jsonify({"ok":True,"model":model,"reply":reply})
    except Exception as exc:
        return jsonify({"error":str(exc) if isinstance(exc, RuntimeError) else f"Tes koneksi gagal ({type(exc).__name__}). Periksa koneksi, model, dan key."}), 502


@app.delete("/api/settings/key")
def remove_key_route():
    provider = request.args.get("provider", "ollama_cloud")
    if provider not in PROVIDERS:
        return jsonify({"error":"Provider tidak dikenal."}), 400
    delete_key(provider)
    return jsonify({"ok":True,"message":"API key provider dihapus dari credential vault lokal."})


@app.post("/api/settings/test")
def test_key_route():
    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "ollama_cloud")
    if provider not in PROVIDERS:
        return jsonify({"error":"Provider tidak dikenal."}), 400
    key = get_key(provider)
    if not key:
        return jsonify({"error":"API key belum tersedia."}), 400
    try:
        reply, model = provider_chat(provider, key, "You are a connection test. Reply briefly in Indonesian.", "Konfirmasi koneksi AHIM LazyDesk AI.")
        return jsonify({"ok":True,"model":model,"reply":reply})
    except Exception as exc:
        return jsonify({"error":str(exc) if isinstance(exc, RuntimeError) else f"Tes koneksi gagal ({type(exc).__name__})."}), 502


@app.post("/api/summarize")
def summarize():
    # Frontend submits FormData for uploads; also accept JSON for text-only clients.
    data = request.form.to_dict() if request.form else (request.get_json(silent=True) or {})
    prompt = (data.get("prompt") or "You are AHIM LazyDesk AI. Summarize accurately, extract decisions and action items, preserve table structure, and mark unreadable content.").strip()[:12000]
    provider = data.get("backend", data.get("provider", "ollama_cloud"))
    if provider not in PROVIDERS and provider != "demo":
        return jsonify({"error":"Pilih provider AI yang valid."}), 400
    upload = request.files.get("file")
    text = (data.get("text") or "").strip()
    images, mime_type, source_name, page_count = [], "image/png", "", None
    if upload:
        source_name = (upload.filename or "").strip()
        if not source_name:
            return jsonify({"error":"Nama file kosong. Reset Upload lalu pilih file lagi."}), 400
        raw = upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            return jsonify({"error":"File melebihi batas 100 MB. Reset Upload lalu pilih file yang lebih kecil."}), 413
        if not raw:
            return jsonify({"error":"File kosong. Reset Upload lalu pilih file lain."}), 400
        try:
            text, images, mime_type, page_count = read_file(source_name, raw)
        except Exception as exc:
            return jsonify({"error":f"File tidak dapat dibaca: {exc} Tekan Reset Upload dan pilih file lain."}), 400
    if not text and not images:
        return jsonify({"error":"Tidak ada teks atau gambar yang dapat dibaca. Pilih file lain atau reset unggahan."}), 400
    if len(text) > MAX_TEXT_CHARS:
        return jsonify({"error":"Teks hasil ekstraksi melebihi 60.000 karakter. Pecah dokumen menjadi beberapa bagian."}), 413
    if provider == "demo":
        if images:
            return jsonify({"error":"Mode demo tidak membaca gambar atau PDF scan. Pilih provider AI vision online."}), 400
        result = "## Ringkasan — Mode Demo (bukan hasil AI)\n" + text[:1800] + "\n\nPeriksa kembali hasil ini. Mode demo tidak memanggil AI."
        model = "demo"
    else:
        key = get_key(provider)
        if not key:
            return jsonify({"error":f"API key {provider} belum diatur. Buka AI Settings.","needsApiKey":True}), 400
        try:
            if images and source_name.lower().endswith(".pdf"):
                chunks = []
                for start in range(0, len(images), 4):
                    batch = images[start:start+4]
                    note = f"Baca halaman PDF {start+1} sampai {start+len(batch)} secara berurutan. Ekstrak teks dan tabel, pertahankan susunan kolom, tandai bagian yang tidak terbaca, jangan mengarang."
                    result_part, model = provider_chat(provider, key, prompt, note, images=batch, mime_type=mime_type)
                    chunks.append(f"## Halaman {start+1}–{start+len(batch)}\n{result_part}")
                result = "\n\n".join(chunks)
            else:
                content = text or "Baca gambar/screenshot terlampir. Ekstrak teks, tabel dan informasi penting secara terstruktur. Tandai bagian yang tidak terbaca."
                result, model = provider_chat(provider, key, prompt, content, images=images, mime_type=mime_type)
        except Exception as exc:
            return jsonify({"error":str(exc) if isinstance(exc, RuntimeError) else f"Pemrosesan gagal ({type(exc).__name__}). Periksa provider, koneksi, model, dan kuota."}), 502
    return jsonify({"result":result,"characters":len(text),"backend":provider,"model":model,"sourceName":source_name,"pageCount":page_count})


def markdown_rows(content):
    rows=[]
    for line in content.splitlines():
        line=line.strip()
        if line.startswith("|") and line.endswith("|"):
            cells=[cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?",c.replace(" ","")) for c in cells):
                continue
            rows.append(cells)
    return rows


@app.post("/api/export")
def export_result():
    data=request.get_json(silent=True) or {}
    content=(data.get("content") or "").strip()
    fmt=(data.get("format") or "md").lower()
    if not content:
        return jsonify({"error":"Tidak ada hasil untuk diunduh."}),400
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if fmt=="docx":
            from docx import Document
            doc=Document()
            rows=markdown_rows(content)
            for line in content.splitlines():
                s=line.strip()
                if not s: continue
                if s.startswith("# "): doc.add_heading(s[2:],level=1)
                elif s.startswith("## "): doc.add_heading(s[3:],level=2)
                elif s.startswith("### "): doc.add_heading(s[4:],level=3)
                elif s.startswith("|") and s.endswith("|"):
                    if rows:
                        table=doc.add_table(rows=1,cols=len(rows[0]));table.style="Light Shading Accent 1"
                        for i,val in enumerate(rows[0]):table.rows[0].cells[i].text=val
                        for row in rows[1:]:
                            cells=table.add_row().cells
                            for i,val in enumerate(row[:len(cells)]):cells[i].text=val
                    break
                elif s.startswith(("- ","* ")): doc.add_paragraph(s[2:].replace("**",""),style="List Bullet")
                else: doc.add_paragraph(s.replace("**","").replace("`",""))
            out=io.BytesIO();doc.save(out);out.seek(0)
            return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.docx")
        if fmt=="pdf":
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet
            from reportlab.lib import colors
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
            from xml.sax.saxutils import escape
            styles=getSampleStyleSheet();story=[];rows=markdown_rows(content)
            inside_table=False
            for line in content.splitlines():
                s=line.strip()
                if not s:story.append(Spacer(1,3*mm));continue
                if s.startswith("|") and s.endswith("|"):
                    if not inside_table and rows:
                        tab=Table(rows,repeatRows=1,hAlign="LEFT")
                        tab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#e8edf4")),("GRID",(0,0),(-1,-1),0.4,colors.grey),("VALIGN",(0,0),(-1,-1),"TOP"),("FONTSIZE",(0,0),(-1,-1),8)]))
                        story.append(tab);story.append(Spacer(1,4*mm));inside_table=True
                    continue
                if s.startswith("# "):story.append(Paragraph(escape(s[2:]),styles["Title"]))
                elif s.startswith("## "):story.append(Paragraph(escape(s[3:]),styles["Heading2"]))
                elif s.startswith("### "):story.append(Paragraph(escape(s[4:]),styles["Heading3"]))
                else:story.append(Paragraph(escape(s.replace("**","").replace("`","")),styles["BodyText"]))
                story.append(Spacer(1,2*mm))
            out=io.BytesIO();SimpleDocTemplate(out,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm).build(story);out.seek(0)
            return send_file(out,mimetype="application/pdf",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.pdf")
        if fmt=="xlsx":
            from openpyxl import Workbook
            from openpyxl.styles import Font,PatternFill,Alignment
            from openpyxl.utils import get_column_letter
            rows=markdown_rows(content);wb=Workbook();ws=wb.active;ws.title="Hasil"
            if rows:
                for row in rows:ws.append(row)
                for cell in ws[1]:cell.font=Font(bold=True,color="FFFFFF");cell.fill=PatternFill("solid",fgColor="17365D")
                ws.freeze_panes="A2";ws.auto_filter.ref=ws.dimensions
                for col in ws.columns:
                    ws.column_dimensions[get_column_letter(col[0].column)].width=min(max(max(len(str(c.value or "")) for c in col)+2,12),40)
            else:
                ws.append(["Hasil AI"])
                for line in content.splitlines():
                    if line.strip():ws.append([line.strip()])
                ws["A1"].font=Font(bold=True,color="FFFFFF");ws["A1"].fill=PatternFill("solid",fgColor="17365D");ws.column_dimensions["A"].width=90
                for row in ws.iter_rows():row[0].alignment=Alignment(wrap_text=True,vertical="top")
            out=io.BytesIO();wb.save(out);out.seek(0)
            return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.xlsx")
        return send_file(io.BytesIO(content.encode("utf-8")),mimetype="text/markdown",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.md")
    except Exception as exc:
        return jsonify({"error":f"Gagal membuat ekspor {fmt.upper()} ({type(exc).__name__})."}),500


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error":"File melebihi batas maksimum 100 MB. Tekan Reset Upload dan pilih file yang lebih kecil."}),413


if __name__ == "__main__":
    app.run(host=os.getenv("HOST","127.0.0.1"),port=int(os.getenv("PORT","5000")),debug=False)
