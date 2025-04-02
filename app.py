from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from io import BytesIO
import fitz  # PyMuPDF
import textwrap
import logging
import ast
import re
import difflib
from transformers import pipeline

# Configure logging
logging.basicConfig(level=logging.INFO)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency Injection for model loading
def get_grammar_checker():
    return pipeline("text2text-generation", model="pszemraj/flan-t5-large-grammar-synthesis")

class TextRequest(BaseModel):
    text: str

@app.post("/correct")
async def correct_text(request: TextRequest, grammar_checker=Depends(get_grammar_checker)):
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text cannot be empty.")
    
    corrected_text = grammar_checker(f"grammar: {text}", max_length=512)[0]['generated_text'].lstrip("grammar: ")
    
    errors = [
        {"word": orig, "position": i, "suggestion": corr}
        for i, (orig, corr) in enumerate(zip(text.split(), corrected_text.split())) if orig.lower() != corr.lower()
    ]

    return {"original": text, "correction": corrected_text, "errors": errors}

# PDF Text Extraction
def extract_text_from_pdf(file: BytesIO):
    doc = fitz.open(stream=file.read(), filetype="pdf")
    return "\n".join(page.get_text("text") for page in doc).strip()

@app.post("/correct-document")
async def correct_document(file: UploadFile = File(...), grammar_checker=Depends(get_grammar_checker)):
    if file.content_type not in ["application/pdf", "text/plain"]:
        raise HTTPException(status_code=400, detail="Only PDF and text files are allowed.")
    
    text = extract_text_from_pdf(file.file) if file.content_type == "application/pdf" else (await file.read()).decode("utf-8")
    if not text:
        raise HTTPException(status_code=400, detail="No extractable text found.")
    
    corrected_lines = [
        " ".join(grammar_checker(f"grammar: {line[i:i+512]}", max_length=512)[0]['generated_text'].lstrip("grammar: ")
                  for i in range(0, len(line), 512))
        for line in text.split('\n') if line.strip()
    ]
    
    corrected_text = "\n".join(corrected_lines)
    return {"original_text": text, "corrected_text": corrected_text}

@app.get("/download-corrected")
def download_corrected(corrected_text: str):
    if not corrected_text:
        raise HTTPException(status_code=404, detail="Corrected text not available.")
    
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    y, margin, line_height = 750, 72, 14
    
    for line in corrected_text.split('\n'):
        for wrapped_line in textwrap.wrap(line, width=80):
            pdf.drawString(margin, y, wrapped_line)
            y -= line_height
            if y < margin:
                pdf.showPage()
                y = 750 - line_height
    
    pdf.save()
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers={"Content-Disposition": "attachment; filename=corrected_document.pdf"})

# Python Syntax Checker
class CodeRequest(BaseModel):
    code: str

@app.post("/check_python")
def check_python_syntax(code_request: CodeRequest):
    code = code_request.code
    try:
        ast.parse(code)
        return {"errors": [], "corrected_code": code}
    except SyntaxError as e:
        return {"errors": [{"line": e.lineno, "message": e.msg}], "corrected_code": fix_syntax_error(code, e.lineno)}

def fix_syntax_error(code: str, lineno: int) -> str:
    lines = code.split("\n")
    if 1 <= lineno <= len(lines):
        lines[lineno - 1] = lines[lineno - 1].strip() + ":" if lines[lineno - 1].strip().endswith(":") else lines[lineno - 1]
    return "\n".join(lines)
