# AHIM LazyDesk AI

A document assistant for processing text and tabular content from images, screenshots, PDFs, Word documents, and Excel workbooks.

## Current project goals

- Preserve the existing application UI.
- Upload validation, clear/reset uploaded files, and handle unsupported inputs with warnings.
- PDF upload limit target: up to 200 pages per file (actual AI processing may be provider- and context-limited).
- Hybrid online AI provider settings.
- Export textual results to PDF and DOCX and structured tables to XLSX.

## Security

- Do not commit API keys, secrets, user uploads, local virtual environments, or cache files.
- Configure provider keys via application settings or environment variables.
- Review provider data handling policies before uploading sensitive documents.

## Status

Repository initialized. Application source files should be added from the verified project ZIP after excluding secrets and generated files.
