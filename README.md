# AHIM LazyDesk AI

AHIM LazyDesk AI is a Flask-based productivity assistant with the original dark dashboard UI, cloud AI provider settings, upload validation, a visible Reset Upload control, and exports to DOCX, PDF, and XLSX.

## Features

- Cloud AI providers: Ollama Cloud, OpenAI, and Google Gemini.
- Upload TXT, Markdown, CSV, JSON, LOG, DOCX, XLSX/XLSM, PDF, and image/screenshot files (PNG, JPG/JPEG, WEBP, BMP, TIFF).
- Maximum upload size: 100 MB per file.
- PDF upload limit: 200 pages per file. Scanned PDFs are rendered to page images and sent in batches of up to 4 pages per request; processing time and provider quotas vary with document length.
- Reset Upload clears the file selection and displayed processing result.
- Export results to Markdown, Word DOCX, PDF, or Excel XLSX.
- Provider API keys are stored through the local OS credential vault where supported; environment variables can also be used.

## Run on Windows

1. Install Python 3.11 or newer.
2. Extract or clone this repository.
3. Run `JALANKAN_AHIM.bat`.
4. Open `http://127.0.0.1:5000`.
5. In **AI settings**, select a provider, enter its API key, and test the connection.

Alternatively, create a virtual environment and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## Provider defaults

- Ollama Cloud: `gemma4:31b` at `https://ollama.com`.
- OpenAI: model configured by `OPENAI_MODEL`, default `gpt-4.1-mini`.
- Google Gemini: model configured by `GEMINI_MODEL`, default `gemini-2.5-flash`.

Never commit API keys. Do not deploy the local Flask development server as a public multi-user service without a production WSGI server, HTTPS, authentication, rate limiting, and per-user secret isolation. Uploaded documents and extracted content are sent to the selected AI provider for processing; review the provider's policies before processing sensitive data.

## Main files

- `app.py` — Flask backend, provider requests, file validation, and export routes.
- `templates/index.html` — existing interface layout.
- `static/style.css` — interface styling.
- `static/app.js` — browser-side interactions.
- `requirements.txt` — Python dependencies.
