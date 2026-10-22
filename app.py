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


def parse_markdown_table(lines, start):
    rows, i = [], start
    while i < len(lines):
        s = lines[i].strip()
        if not (s.startswith("|") and s.endswith("|")):
            break
        cells = [x.strip() for x in s[1:-1].split("|")]
        if not all(re.fullmatch(r":?-{3,}:?", x.replace(" ", "")) for x in cells):
            rows.append(cells)
        i += 1
    if rows:
        width = max(map(len, rows))
        rows = [row + [""] * (width-len(row)) for row in rows]
    return rows, i


def markdown_rows(content):
    lines = content.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            return parse_markdown_table(lines, i)[0]
    return []


def clean_inline_markdown(value):
    value = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", value)
    value = re.sub(r"\x60([^\x60]+)\x60", r"\1", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"__(.+?)__", r"\1", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", value)
    value = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"\1", value)
    return value.strip()


def markdown_blocks(content):
    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks, paragraph, i = [], [], 0
    def flush():
        if paragraph:
            blocks.append({"type":"paragraph","text":clean_inline_markdown(" ".join(x.strip() for x in paragraph))})
            paragraph.clear()
    while i < len(lines):
        raw, s = lines[i], lines[i].strip()
        if not s:
            flush(); i += 1; continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", s)
        if heading:
            flush(); blocks.append({"type":"heading","level":min(len(heading.group(1)),4),"text":clean_inline_markdown(heading.group(2))}); i += 1; continue
        if s.startswith("|") and s.endswith("|"):
            flush(); rows, next_i = parse_markdown_table(lines, i)
            if rows:
                blocks.append({"type":"table","rows":rows}); i = next_i; continue
        if s.startswith(">"):
            flush(); quote=[]
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(clean_inline_markdown(re.sub(r"^\s*>\s?","",lines[i]))); i += 1
            blocks.append({"type":"quote","text":" ".join(quote)}); continue
        if s.startswith(chr(96)*3):
            flush(); i += 1; code=[]
            while i < len(lines) and not lines[i].strip().startswith(chr(96)*3):
                code.append(lines[i]); i += 1
            if i < len(lines): i += 1
            blocks.append({"type":"code","text":"\n".join(code)}); continue
        bullet = re.match(r"^\s*[-*+]\s+(.+)$", raw)
        ordered = re.match(r"^\s*\d+[.)]\s+(.+)$", raw)
        if bullet or ordered:
            flush(); is_ordered=bool(ordered); items=[]
            while i < len(lines):
                match = re.match(r"^\s*\d+[.)]\s+(.+)$",lines[i]) if is_ordered else re.match(r"^\s*[-*+]\s+(.+)$",lines[i])
                if not match: break
                items.append(clean_inline_markdown(match.group(1))); i += 1
            blocks.append({"type":"list","ordered":is_ordered,"items":items}); continue
        if re.fullmatch(r"[-*_]{3,}",s):
            flush(); blocks.append({"type":"rule"}); i += 1; continue
        paragraph.append(s); i += 1
    flush()
    return blocks


def _reportlab_markup(text):
    from xml.sax.saxutils import escape
    safe = escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    safe = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", safe)
    safe = re.sub(r"\x60([^\x60]+)\x60", r'<font name="Courier">\1</font>', safe)
    return safe.replace("\n","<br/>")


@app.post("/api/export")
def export_result():
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    fmt = (data.get("format") or "md").lower()
    fmt = {"word":"docx","excel":"xlsx"}.get(fmt,fmt)
    if not content:
        return jsonify({"error":"Tidak ada hasil untuk diekspor."}),400
    if fmt not in {"md","docx","pdf","xlsx"}:
        return jsonify({"error":"Format ekspor tidak didukung."}),400
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    blocks = markdown_blocks(content)
    first_heading = next((b["text"] for b in blocks if b["type"]=="heading"),None)
    try:
        if fmt == "md":
            normalized = content.replace("\r\n","\n").replace("\r","\n").strip() + "\n"
            return send_file(io.BytesIO(normalized.encode("utf-8")),mimetype="text/markdown; charset=utf-8",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.md")

        if fmt == "docx":
            from docx import Document
            from docx.shared import Inches, Pt, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            doc=Document(); sec=doc.sections[0]
            sec.top_margin=Inches(.7); sec.bottom_margin=Inches(.7); sec.left_margin=Inches(.8); sec.right_margin=Inches(.8)
            normal=doc.styles["Normal"]; normal.font.name="Aptos"; normal.font.size=Pt(10); normal.font.color.rgb=RGBColor(45,55,72)
            normal.paragraph_format.space_after=Pt(6)
            for name,size,color in [("Title",22,(20,50,85)),("Heading 1",16,(20,50,85)),("Heading 2",13,(28,90,130)),("Heading 3",11,(45,65,85))]:
                st=doc.styles[name]; st.font.name="Aptos Display"; st.font.size=Pt(size); st.font.bold=True; st.font.color.rgb=RGBColor(*color)
            p=doc.add_paragraph(style="Title"); p.alignment=WD_ALIGN_PARAGRAPH.LEFT; p.add_run(first_heading or "Hasil AHIM LazyDesk AI")
            skip_title=bool(first_heading)
            for b in blocks:
                kind=b["type"]
                if kind=="heading":
                    if skip_title and b["text"]==first_heading: skip_title=False; continue
                    doc.add_heading(b["text"],level=min(b["level"],3))
                elif kind=="paragraph": doc.add_paragraph(b["text"])
                elif kind=="list":
                    for item in b["items"]: doc.add_paragraph(item,style="List Number" if b["ordered"] else "List Bullet")
                elif kind=="quote": doc.add_paragraph(b["text"],style="Quote")
                elif kind=="code":
                    p=doc.add_paragraph(); run=p.add_run(b["text"]); run.font.name="Consolas"; run.font.size=Pt(9)
                elif kind=="table":
                    rows=b["rows"]
                    if not rows: continue
                    table=doc.add_table(rows=1,cols=len(rows[0])); table.style="Light Shading Accent 1"; table.alignment=WD_TABLE_ALIGNMENT.CENTER
                    for ri,row in enumerate(rows):
                        cells=table.rows[0].cells if ri==0 else table.add_row().cells
                        for ci,value in enumerate(row):
                            cells[ci].text=value; cells[ci].vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
                            if ri==0:
                                shade=OxmlElement("w:shd"); shade.set(qn("w:fill"),"17365D"); cells[ci]._tc.get_or_add_tcPr().append(shade)
                                for run in cells[ci].paragraphs[0].runs:
                                    run.font.bold=True; run.font.color.rgb=RGBColor(255,255,255)
                    doc.add_paragraph()
                elif kind=="rule": doc.add_paragraph("─"*32)
            out=io.BytesIO(); doc.save(out); out.seek(0)
            return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.docx")

        if fmt == "pdf":
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_LEFT
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import mm
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem, Preformatted
            styles=getSampleStyleSheet()
            styles.add(ParagraphStyle(name="AHIMTitle",parent=styles["Title"],fontName="Helvetica-Bold",fontSize=20,leading=25,textColor=colors.HexColor("#143255"),alignment=TA_LEFT,spaceAfter=12))
            styles.add(ParagraphStyle(name="AHIMH1",parent=styles["Heading1"],fontSize=15,leading=19,textColor=colors.HexColor("#143255"),spaceBefore=10,spaceAfter=6,keepWithNext=True))
            styles.add(ParagraphStyle(name="AHIMH2",parent=styles["Heading2"],fontSize=12,leading=16,textColor=colors.HexColor("#1C5A82"),spaceBefore=8,spaceAfter=4,keepWithNext=True))
            styles.add(ParagraphStyle(name="AHIMH3",parent=styles["Heading3"],fontSize=10.5,leading=14,textColor=colors.HexColor("#2D4155"),spaceBefore=6,spaceAfter=3,keepWithNext=True))
            styles.add(ParagraphStyle(name="AHIMBody",parent=styles["BodyText"],fontSize=9.5,leading=14,textColor=colors.HexColor("#2D3748"),spaceAfter=6))
            styles.add(ParagraphStyle(name="AHIMQuote",parent=styles["BodyText"],leftIndent=12,borderColor=colors.HexColor("#A0AEC0"),borderWidth=1,borderPadding=6,fontSize=9,textColor=colors.HexColor("#4A5568"),spaceBefore=4,spaceAfter=8))
            styles.add(ParagraphStyle(name="AHIMCode",parent=styles["Code"],fontName="Courier",fontSize=8,leading=10,backColor=colors.HexColor("#F3F5F7"),borderPadding=5,spaceAfter=7))
            story=[Paragraph(_reportlab_markup(first_heading or "Hasil AHIM LazyDesk AI"),styles["AHIMTitle"])]
            skip_title=bool(first_heading)
            for b in blocks:
                kind=b["type"]
                if kind=="heading":
                    if skip_title and b["text"]==first_heading: skip_title=False; continue
                    st=styles["AHIMH1"] if b["level"]==1 else styles["AHIMH2"] if b["level"]==2 else styles["AHIMH3"]
                    story.append(Paragraph(_reportlab_markup(b["text"]),st))
                elif kind=="paragraph": story.append(Paragraph(_reportlab_markup(b["text"]),styles["AHIMBody"]))
                elif kind=="list":
                    items=[ListItem(Paragraph(_reportlab_markup(item),styles["AHIMBody"]),leftIndent=12) for item in b["items"]]
                    story.append(ListFlowable(items,bulletType="1" if b["ordered"] else "bullet",leftIndent=18,bulletFontName="Helvetica",bulletFontSize=8,spaceAfter=7))
                elif kind=="quote": story.append(Paragraph(_reportlab_markup(b["text"]),styles["AHIMQuote"]))
                elif kind=="code": story.append(Preformatted(b["text"],styles["AHIMCode"]))
                elif kind=="table":
                    table_data=[[Paragraph(_reportlab_markup(cell),styles["AHIMBody"]) for cell in row] for row in b["rows"]]
                    tab=Table(table_data,repeatRows=1,hAlign="LEFT")
                    tab.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),colors.HexColor("#17365D")),("TEXTCOLOR",(0,0),(-1,0),colors.white),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#CBD5E0")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
                    story.extend([tab,Spacer(1,5*mm)])
                elif kind=="rule": story.append(Spacer(1,3*mm))
            def page_footer(canvas,document):
                canvas.saveState(); canvas.setFont("Helvetica",8); canvas.setFillColor(colors.HexColor("#718096"))
                canvas.drawString(18*mm,10*mm,"AHIM LazyDesk AI"); canvas.drawRightString(A4[0]-18*mm,10*mm,f"Halaman {document.page}"); canvas.restoreState()
            out=io.BytesIO()
            SimpleDocTemplate(out,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=18*mm,bottomMargin=18*mm,title=first_heading or "Hasil AHIM LazyDesk AI",author="AHIM LazyDesk AI").build(story,onFirstPage=page_footer,onLaterPages=page_footer)
            out.seek(0)
            return send_file(out,mimetype="application/pdf",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.pdf")

        if fmt == "xlsx":
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.table import Table as XLTable, TableStyleInfo
            wb=Workbook(); ws=wb.active; ws.title="Hasil"; ws.sheet_view.showGridLines=False
            navy="17365D"; pale="D9EAF7"; thin=Side(style="thin",color="D5DEE8")
            tables=[b["rows"] for b in blocks if b["type"]=="table"]
            if tables:
                rows=tables[0]
                for row in rows: ws.append(row)
                max_col=len(rows[0])
                for cell in ws[1]:
                    cell.font=Font(bold=True,color="FFFFFF"); cell.fill=PatternFill("solid",fgColor=navy); cell.alignment=Alignment(wrap_text=True,vertical="center")
                ws.row_dimensions[1].height=30
                if ws.max_row>=2:
                    tab=XLTable(displayName="AHIMData",ref=f"A1:{get_column_letter(max_col)}{ws.max_row}")
                    tab.tableStyleInfo=TableStyleInfo(name="TableStyleMedium2",showRowStripes=True,showColumnStripes=False); ws.add_table(tab)
                ws.freeze_panes="A2"
                for col in range(1,max_col+1):
                    letter=get_column_letter(col); values=[str(ws.cell(row=r,column=col).value or "") for r in range(1,ws.max_row+1)]
                    ws.column_dimensions[letter].width=min(max(max(map(len,values),default=10)+2,14),42)
                    for row in range(2,ws.max_row+1):
                        ws.cell(row=row,column=col).alignment=Alignment(wrap_text=True,vertical="top")
                        ws.cell(row=row,column=col).border=Border(bottom=thin)
            else:
                ws.merge_cells("A1:F1"); ws["A1"]=first_heading or "Hasil AHIM LazyDesk AI"
                ws["A1"].font=Font(name="Aptos Display",size=18,bold=True,color="FFFFFF"); ws["A1"].fill=PatternFill("solid",fgColor=navy); ws["A1"].alignment=Alignment(vertical="center")
                ws.row_dimensions[1].height=34; row_num=3
                for b in blocks:
                    kind=b["type"]
                    if kind=="heading":
                        if row_num==3 and ws["A1"].value==b["text"]: continue
                        ws.merge_cells(start_row=row_num,start_column=1,end_row=row_num,end_column=6)
                        c=ws.cell(row=row_num,column=1,value=b["text"]); c.font=Font(bold=True,size=14 if b["level"]<=2 else 12,color=navy); c.fill=PatternFill("solid",fgColor=pale); c.alignment=Alignment(wrap_text=True,vertical="center"); row_num+=1
                    elif kind=="paragraph":
                        ws.merge_cells(start_row=row_num,start_column=1,end_row=row_num,end_column=6)
                        c=ws.cell(row=row_num,column=1,value=b["text"]); c.alignment=Alignment(wrap_text=True,vertical="top"); row_num+=1
                    elif kind=="list":
                        for n,item in enumerate(b["items"],1):
                            ws.merge_cells(start_row=row_num,start_column=1,end_row=row_num,end_column=6)
                            c=ws.cell(row=row_num,column=1,value=(f"{n}. " if b["ordered"] else "• ")+item); c.alignment=Alignment(wrap_text=True,vertical="top",indent=1); row_num+=1
                    elif kind=="quote":
                        ws.merge_cells(start_row=row_num,start_column=1,end_row=row_num,end_column=6)
                        c=ws.cell(row=row_num,column=1,value=b["text"]); c.font=Font(italic=True,color="4A5568"); c.alignment=Alignment(wrap_text=True,vertical="top",indent=1); row_num+=1
                    elif kind=="code":
                        ws.merge_cells(start_row=row_num,start_column=1,end_row=row_num,end_column=6)
                        c=ws.cell(row=row_num,column=1,value=b["text"]); c.font=Font(name="Consolas",size=9,color="334155"); c.alignment=Alignment(wrap_text=True,vertical="top"); row_num+=1
                    elif kind=="table":
                        for row in b["rows"]: ws.append(row)
                        header=row_num
                        for c in ws[header]:
                            if c.column<=len(b["rows"][0]):
                                c.font=Font(bold=True,color="FFFFFF"); c.fill=PatternFill("solid",fgColor=navy); c.alignment=Alignment(wrap_text=True,vertical="center")
                        row_num=ws.max_row+2
                    elif kind=="rule": row_num+=1
                for col in range(1,7): ws.column_dimensions[get_column_letter(col)].width=20 if col>1 else 34
                ws.freeze_panes="A3"
            out=io.BytesIO(); wb.save(out); out.seek(0)
            return send_file(out,mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",as_attachment=True,download_name=f"ahim_lazydesk_{stamp}.xlsx")
    except Exception as exc:
        app.logger.exception("Export %s failed",fmt)
        return jsonify({"error":f"Gagal membuat ekspor {fmt.upper()} ({type(exc).__name__})."}),500


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error":"File melebihi batas maksimum 100 MB. Tekan Reset Upload dan pilih file yang lebih kecil."}),413


if __name__ == "__main__":
    app.run(host=os.getenv("HOST","127.0.0.1"),port=int(os.getenv("PORT","5000")),debug=False)
