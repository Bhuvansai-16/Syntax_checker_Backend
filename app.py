from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from gramformer import Gramformer
from transformers import pipeline
import fitz
import textwrap
from io import BytesIO
from reportlab.pdfgen import canvas
import logging
import os
import tempfile
import difflib
import ast
import re

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = FastAPI()

# Enable CORS for frontend compatibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update to your frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Lazy load models to reduce startup memory usage
gf = None
grammar_checker = None

def load_models():
    global gf, grammar_checker
    if gf is None:
        gf = Gramformer(models=1, use_gpu=False)
    if grammar_checker is None:
        grammar_checker = pipeline("text2text-generation", model="google/flan-t5-base")

class TextRequest(BaseModel):
    text: str

class CorrectionResponse(BaseModel):
    original_text: str
    corrected_text: str
    errors: list
    temp_file: str

@app.post("/correct")
async def correct_text(request: TextRequest):
    load_models()
    try:
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")
        corrected_sentences = list(gf.correct(request.text, max_candidates=1))
        corrected_text = corrected_sentences[0] if corrected_sentences else request.text
        original_words = request.text.split()
        corrected_words = corrected_text.split()
        errors = [
            {"word": orig, "position": i, "suggestion": corr}
            for i, (orig, corr) in enumerate(zip(original_words, corrected_words))
            if orig.lower() != corr.lower()
        ]
        return {"original": request.text, "correction": corrected_text, "errors": errors}
    except Exception as e:
        logging.error(f"Grammar check failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def extract_text_from_pdf(file):
    """Extract text from PDF using PyMuPDF."""
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = "\n".join([page.get_text("text") for page in doc])
    doc.close()  # Explicitly close to free memory
    return text.strip()

def find_grammar_errors(original, corrected):
    """Find grammar errors by comparing original and corrected texts."""
    original_words = original.split()
    corrected_words = corrected.split()
    errors = []
    matcher = difflib.SequenceMatcher(None, original_words, corrected_words)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            errors.extend(original_words[i1:i2])
    return errors

@app.post("/correct-document", response_model=CorrectionResponse)
async def correct_document(file: UploadFile = File(...)):
    load_models()
    if file.content_type not in ["application/pdf", "text/plain"]:
        raise HTTPException(status_code=400, detail="Invalid file type.")
    if file.content_type == "application/pdf":
        text = extract_text_from_pdf(file)
    else:
        text = (await file.read()).decode("utf-8")
    if not text:
        raise HTTPException(status_code=400, detail="No extractable text found.")
    original_lines = text.split('\n')
    corrected_lines = []
    all_errors = []
    for line in original_lines:
        if not line.strip():
            corrected_lines.append('')
            continue
        line_correction = []
        for i in range(0, len(line), 256):  # Smaller chunks to reduce memory
            chunk = line[i:i+256]
            corrected_chunk = grammar_checker(f"grammar: {chunk}", max_length=256)[0]['generated_text']
            corrected_chunk = corrected_chunk.lstrip("grammar: ")
            line_correction.append(corrected_chunk)
        corrected_line = " ".join(line_correction)
        corrected_lines.append(corrected_line)
        line_errors = find_grammar_errors(line, corrected_line)
        all_errors.extend(line_errors)
    corrected_text = '\n'.join(corrected_lines)
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as temp_file:
        temp_file.write(corrected_text)
        temp_file_path = temp_file.name
    return {"original_text": text, "corrected_text": corrected_text, "errors": all_errors, "temp_file": temp_file_path}

@app.get("/download-corrected")
async def download_corrected(temp_file: str):
    """Generate and return corrected text as a downloadable PDF."""
    if not os.path.exists(temp_file):
        raise HTTPException(status_code=404, detail="Corrected text not available.")
    with open(temp_file, 'r') as f:
        corrected_text = f.read()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 750
    margin = 72
    line_height = 14
    max_lines_per_page = 50  # Limit lines to manage memory
    line_count = 0
    for line in corrected_text.split('\n'):
        wrapped_lines = textwrap.wrap(line, width=80)
        for wrapped_line in wrapped_lines:
            if line_count >= max_lines_per_page:
                pdf.showPage()
                y = 750 - line_height
                line_count = 0
            pdf.drawString(margin, y, wrapped_line)
            y -= line_height
            line_count += 1
    pdf.save()
    buffer.seek(0)
    os.remove(temp_file)  # Clean up
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=corrected_document.pdf"}
    )

class CodeRequest(BaseModel):
    code: str

def fix_syntax_error(line: str) -> str:
    fixed_line = line.strip()
    if re.match(r"^(if|elif|else|for|while|def|class|try|except|finally|with)\s+[^:]+$", fixed_line):
        fixed_line += ":"
    fixed_line = re.sub(r"\bprint\s+\"(.*?)\"", r"print(\"\1\")", fixed_line)
    fixed_line = re.sub(r"\bprint\s+'(.*?)'", r"print('\1')", fixed_line)
    fixed_line = re.sub(r"\bprint\s+([^\(\n]+)", r"print(\1)", fixed_line)
    if fixed_line.count("'") % 2 != 0:
        if fixed_line.endswith(')'):
            fixed_line = fixed_line[:-1] + "')"
        else:
            fixed_line += "'"
    if fixed_line.count('"') % 2 != 0:
        if fixed_line.endswith(')'):
            fixed_line = fixed_line[:-1] + '")'
        else:
            fixed_line += '"'
    open_parens = fixed_line.count("(")
    close_parens = fixed_line.count(")")
    if open_parens > close_parens:
        fixed_line += ")" * (open_parens - close_parens)
    if "[" in fixed_line and "]" not in fixed_line:
        fixed_line += "]"
    return fixed_line

@app.post("/check_python")
async def check_python_syntax(code_request: CodeRequest):
    code = code_request.code
    errors = []
    corrected_code = code
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append({"line": e.lineno, "message": e.msg})
        lines = code.split("\n")
        if 1 <= e.lineno <= len(lines):
            lines[e.lineno - 1] = fix_syntax_error(lines[e.lineno - 1])
            corrected_code = "\n".join(lines)
    return {"errors": errors, "corrected_code": corrected_code}
