import io
import json
import csv
from typing import Optional

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

try:
    import docx
except ImportError:
    docx = None

try:
    import openpyxl
except ImportError:
    openpyxl = None


def parse_pdf(file_path: str) -> str:
    """Extract text from a PDF file."""
    if PdfReader is None:
        raise RuntimeError("pypdf is not installed.")
    
    text = []
    reader = PdfReader(file_path)
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            text.append(page_text)
    return "\n\n".join(text)


def parse_docx(file_path: str) -> str:
    """Extract text from a DOCX file."""
    if docx is None:
        raise RuntimeError("python-docx is not installed.")
        
    doc = docx.Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])


def parse_xlsx(file_path: str) -> str:
    """Extract text from an XLSX file."""
    if openpyxl is None:
        raise RuntimeError("openpyxl is not installed.")
        
    wb = openpyxl.load_workbook(file_path, data_only=True)
    lines = []
    for sheet_name in wb.sheetnames:
        sheet = wb[sheet_name]
        lines.append(f"--- Sheet: {sheet_name} ---")
        for row in sheet.iter_rows(values_only=True):
            row_str = "\t".join([str(cell) if cell is not None else "" for cell in row])
            lines.append(row_str)
    return "\n".join(lines)


def parse_csv(file_path: str) -> str:
    """Extract text from a CSV file."""
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        lines = []
        for row in reader:
            lines.append(",".join(row))
    return "\n".join(lines)


def parse_json(file_path: str) -> str:
    """Extract formatted text from a JSON file."""
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        data = json.load(f)
    return json.dumps(data, indent=2)


def parse_txt(file_path: str) -> str:
    """Extract text from a plain text file."""
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def parse_document(file_path: str) -> str:
    """
    Parse a document based on its extension.
    Supported: .pdf, .docx, .xlsx, .csv, .json, .txt, .md, .log
    """
    ext = file_path.lower().split('.')[-1]
    
    if ext == 'pdf':
        return parse_pdf(file_path)
    elif ext == 'docx':
        return parse_docx(file_path)
    elif ext == 'xlsx':
        return parse_xlsx(file_path)
    elif ext == 'csv':
        return parse_csv(file_path)
    elif ext == 'json':
        return parse_json(file_path)
    elif ext in ('txt', 'md', 'log'):
        return parse_txt(file_path)
    else:
        raise ValueError(f"Unsupported document extension: {ext}")
