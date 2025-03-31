from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import logging
import ast
import re
# CORS Configuration
origins = "http://localhost:5173"


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
