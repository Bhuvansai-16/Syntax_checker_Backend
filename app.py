from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from PyPDF2 import PdfReader
from reportlab.pdfgen import canvas
from io import BytesIO
from fastapi.middleware.cors import CORSMiddleware
import logging
from fastapi.middleware.cors import CORSMiddleware
from gramformer import Gramformer
import ast
from reportlab.pdfgen import canvas
from transformers import pipeline
import re
from fastapi.responses import StreamingResponse
import difflib
import fitz  # PyMuPDF for better PDF text extraction
import textwrap
from io import BytesIO
from reportlab.pdfgen import canvas
# CORS Configuration
import spacy
import subprocess
import importlib.util

# Auto-download the model if not already installed

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = FastAPI()

# Enable CORS to allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change this to the frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Load GramFormer
gf = Gramformer(models=1, use_gpu = False)

class TextRequest(BaseModel):
    text: str

@app.post("/correct")
async def correct_text(request: TextRequest):
    try:
        if not request.text.strip():
            raise HTTPException(status_code=400, detail="Text cannot be empty.")

        # Get grammar corrections
        corrected_sentences = list(gf.correct(request.text, max_candidates=1))
        corrected_text = corrected_sentences[0] if corrected_sentences else request.text

        # Identify incorrect words
        original_words = request.text.split()
        corrected_words = corrected_text.split()
        errors = []

        for i, (orig, corr) in enumerate(zip(original_words, corrected_words)):
            if orig.lower() != corr.lower():  # Case-insensitive comparison
                errors.append({
                    "word": orig,
                    "position": i,  # Zero-based index
                    "suggestion": corr
                })

        return {
            "original": request.text,
            "correction": corrected_text,
            "errors": errors
        }
    except Exception as e:
        logging.error(f"Grammar check failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Initialize grammar correction model
grammar_checker = pipeline("text2text-generation", model="pszemraj/flan-t5-large-grammar-synthesis")

class CorrectionResponse(BaseModel):
    original_text: str
    corrected_text: str
    errors: list

def extract_text_from_pdf(file):
    """Extract text from PDF using PyMuPDF (fitz)."""
    doc = fitz.open(stream=file.read(), filetype="pdf")
    text = "\n".join([page.get_text("text") for page in doc])
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
    if file.content_type not in ["application/pdf", "text/plain"]:
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF and text files are allowed.")

    # Extract text from file
    if file.content_type == "application/pdf":
        text = extract_text_from_pdf(file)
    else:
        text = (await file.read()).decode("utf-8")

    if not text:
        raise HTTPException(status_code=400, detail="No extractable text found.")

    # Process text line by line to preserve structure
    original_lines = text.split('\n')
    corrected_lines = []
    all_errors = []

    for line in original_lines:
        if not line.strip():
            corrected_lines.append('')
            continue

        # Process line in chunks while preserving context
        line_correction = []
        for i in range(0, len(line), 512):
            chunk = line[i:i+512]
            corrected_chunk = grammar_checker(f"grammar: {chunk}", max_length=512)[0]['generated_text']
            # Remove potential "grammar:" prefix from output
            corrected_chunk = corrected_chunk.lstrip("grammar: ")
            line_correction.append(corrected_chunk)
        
        corrected_line = " ".join(line_correction)
        corrected_lines.append(corrected_line)
        
        # Find errors within this line's context
        line_errors = find_grammar_errors(line, corrected_line)
        all_errors.extend(line_errors)

    corrected_text = '\n'.join(corrected_lines)

    # Save the corrected text to a global variable for downloading
    global latest_corrected_text
    latest_corrected_text = corrected_text

    return {"original_text": text, "corrected_text": corrected_text, "errors": all_errors}

@app.get("/download-corrected")
def download_corrected():
    """Generate and return the corrected text as a downloadable PDF."""
    if "latest_corrected_text" not in globals() or not latest_corrected_text:
        raise HTTPException(status_code=404, detail="Corrected text not available.")

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y = 750
    margin = 72  # 1-inch margin
    line_height = 14

    # Preserve original line breaks from corrected text
    for line in latest_corrected_text.split('\n'):
        wrapped_lines = textwrap.wrap(line, width=80)  # Wrap long lines
        for wrapped_line in wrapped_lines:
            pdf.drawString(margin, y, wrapped_line)
            y -= line_height
            if y < margin:  # New page when reaching bottom margin
                pdf.showPage()
                y = 750 - line_height

    pdf.save()
    buffer.seek(0)

    return StreamingResponse(
        buffer, 
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=corrected_document.pdf"}
    )

#Codechecker
class CodeRequest(BaseModel):
    code: str

def fix_syntax_error(line: str) -> str:
    fixed_line = line.strip()

    # ✅ Fix missing colons in control structures (def, if, for, etc.)
    if re.match(r"^(if|elif|else|for|while|def|class|try|except|finally|with)\s+[^:]+$", fixed_line):
        fixed_line += ":"

    # ✅ Fix print statements missing parentheses
    fixed_line = re.sub(r"\bprint\s+\"(.*?)\"", r"print(\"\1\")", fixed_line)
    fixed_line = re.sub(r"\bprint\s+'(.*?)'", r"print('\1')", fixed_line)
    fixed_line = re.sub(r"\bprint\s+([^\(\n]+)", r"print(\1)", fixed_line)

    # ✅ Fix unbalanced quotes
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

    # ✅ Fix unmatched parentheses
    open_parens = fixed_line.count("(")
    close_parens = fixed_line.count(")")
    if open_parens > close_parens:
        fixed_line += ")" * (open_parens - close_parens)

    # ✅ Fix missing square brackets in lists
    if "[" in fixed_line and "]" not in fixed_line:
        fixed_line += "]"

    return fixed_line

@app.post("/check_python")
def check_python_syntax(code_request: CodeRequest):
    code = code_request.code
    errors = []
    corrected_code = code  # Start with the original

    # Try parsing the original code
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append({"line": e.lineno, "message": e.msg})

        # Fix only the erroneous line
        lines = code.split("\n")
        if 1 <= e.lineno <= len(lines):  # Ensure the line exists
            lines[e.lineno - 1] = fix_syntax_error(lines[e.lineno - 1])
            corrected_code = "\n".join(lines)

    return {"errors": errors, "corrected_code": corrected_code}
