import re
import sys
import os
import time
try:
    import msvcrt
except ImportError:
    msvcrt = None

# ------------------------------
# Global State
# ------------------------------
variables = {}
functions = {}
labels = {}
program_lines = []
current_line = 0
current_fg = None
current_bg = None

# ------------------------------
# Utilities
# ------------------------------

def strip_inline_comment(line: str) -> str:
    """Remove inline comments (// or #) that occur outside quotes.
    Supports single ('') and double ("") quoted strings. No escape handling.
    """
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if not in_single and not in_double:
            if ch == '/' and i + 1 < len(line) and line[i+1] == '/':
                return line[:i].rstrip()
            if ch == '#':
                return line[:i].rstrip()
        i += 1
    return line.rstrip()

def substitute_variables(text):
    """Replace <`VAR`> with its value."""
    matches = re.findall(r"<`(.*?)`>", text)
    for var in matches:
        value = variables.get(var, f"<UNDEFINED:{var}>")
        text = text.replace(f"<`{var}`>", value)
    return text

def parse_value(value):
    """Handles quoted strings or variable references."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value.strip('"')
    return variables.get(value, value)

def parse_token_value(token):
    """Parse a token that may be quoted, contain <`VAR`>, or be a variable name."""
    if token is None:
        return ""
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return substitute_variables(token.strip('"'))
    token = substitute_variables(token)
    return variables.get(token, token)

def parse_path_token(token):
    """Parse a path token that may be quoted and include variable substitutions."""
    return parse_token_value(token)

def eval_math(expr):
    """Safely evaluate a math expression containing numbers and operators."""
    import ast
    expr = expr.strip()
    expr = substitute_variables(expr)
    if expr.startswith('"') and expr.endswith('"'):
        expr = expr[1:-1]

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.UAdd, ast.USub,
        ast.Load,
    )

    def _eval(node):
        if not isinstance(node, allowed_nodes):
            raise ValueError("Invalid math expression")
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants allowed")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Invalid unary operator")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right
            raise ValueError("Invalid binary operator")
        raise ValueError("Invalid math expression")

    tree = ast.parse(expr, mode="eval")
    return _eval(tree)

def ansi_prefix():
    """Build ANSI prefix based on current colors."""
    codes = []
    if current_fg:
        codes.append(current_fg)
    if current_bg:
        codes.append(current_bg)
    if not codes:
        return ""
    return f"\033[{';'.join(codes)}m"

def ansi_reset():
    return "\033[0m"

def set_color(tag, value):
    global current_fg, current_bg
    color_map = {
        "BLACK": "30",
        "RED": "31",
        "GREEN": "32",
        "YELLOW": "33",
        "BLUE": "34",
        "MAGENTA": "35",
        "CYAN": "36",
        "WHITE": "37",
        "BRIGHTBLACK": "90",
        "BRIGHTRED": "91",
        "BRIGHTGREEN": "92",
        "BRIGHTYELLOW": "93",
        "BRIGHTBLUE": "94",
        "BRIGHTMAGENTA": "95",
        "BRIGHTCYAN": "96",
        "BRIGHTWHITE": "97",
    }
    bg_map = {k: str(int(v) + 10) for k, v in color_map.items() if v.isdigit()}
    key = value.strip().upper()
    if tag == "FG":
        if key in color_map:
            current_fg = color_map[key]
        else:
            print(f"[WARN] Unknown FG color '{value}'")
    elif tag == "BG":
        if key in bg_map:
            current_bg = bg_map[key]
        else:
            print(f"[WARN] Unknown BG color '{value}'")

def reset_color():
    global current_fg, current_bg
    current_fg = None
    current_bg = None

def clear_screen():
    """Clear screen and move cursor to home position."""
    print("\033[2J\033[H", end="")

def set_cursor(row, col):
    """Move cursor to 1-based row/col position."""
    print(f"\033[{row};{col}H", end="")

def tick_timer(ms):
    """Pause execution for the given milliseconds."""
    try:
        delay = float(ms) / 1000.0
    except ValueError:
        print("[ERROR] TickTimer requires a numeric millisecond value")
        return
    if delay < 0:
        return
    time.sleep(delay)

def draw_box(width, height, ch):
    try:
        w = int(width)
        h = int(height)
    except ValueError:
        print("[ERROR] DrawBox width/height must be integers")
        return
    if w <= 0 or h <= 0:
        return
    if len(ch) == 0:
        ch = "#"
    ch = ch[0]
    if w == 1:
        for _ in range(h):
            display_to_shell(ch)
        return
    if h == 1:
        display_to_shell(ch * w)
        return
    top = ch * w
    mid = ch + (" " * (w - 2)) + ch
    display_to_shell(top)
    for _ in range(h - 2):
        display_to_shell(mid)
    display_to_shell(top)

def normalize_input(text):
    """Lowercase and collapse whitespace for command parsing."""
    text = text.strip().lower()
    if not text:
        return ""
    return " ".join(text.split())

def set_word_vars(text):
    """Populate WORD1/WORD2/WORD3 and WORDCOUNT from normalized input."""
    words = text.split() if text else []
    variables["WORDCOUNT"] = str(len(words))
    variables["WORD1"] = words[0] if len(words) > 0 else ""
    variables["WORD2"] = words[1] if len(words) > 1 else ""
    variables["WORD3"] = words[2] if len(words) > 2 else ""

def send_to_hardware(text):
    """Simulate writing to hardware by appending to a hardware_output.log file next to the script.
    This keeps real hardware access safe while giving a place to inspect DIRECT output.
    """
    try:
        log_path = os.path.join(os.path.dirname(__file__), "hardware_output.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        print(f"[ERROR] Failed to write to hardware log: {e}")


def display_to_shell(text):
    """Send text to the interactive shell (stdout)."""
    prefix = ansi_prefix()
    if prefix:
        print(f"{prefix}{text}{ansi_reset()}")
    else:
        print(text)

# ------------------------------
# Instruction Handlers
# ------------------------------
def handle_set(line):
    # Example: Set[USER]= "Logan"
    match = re.match(r"Set\[(.*?)\]\s*=\s*(.*)", line)
    if not match:
        print(f"[ERROR] Invalid Set syntax: {line}")
        return

    var_name = match.group(1)
    raw_value = match.group(2).strip()

    # Math evaluation: Set[X]=Math(1+2*3)
    if raw_value.startswith("Math(") and raw_value.endswith(")"):
        expr = raw_value[5:-1]
        try:
            result = eval_math(expr)
            variables[var_name] = str(result)
        except Exception as e:
            print(f"[ERROR] Math evaluation failed: {e}")
        return

    # ReadFile: Set[VAR]=ReadFile["path"]
    if raw_value.startswith("ReadFile[") and raw_value.endswith("]"):
        path_token = raw_value[len("ReadFile["):-1]
        file_path = parse_path_token(path_token)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                variables[var_name] = f.read()
        except Exception as e:
            print(f"[ERROR] ReadFile failed: {e}")
        return

    # Support DisplayText(TAG)=... where TAG can be DIRECT or SHELL (case-insensitive)
    dt_match = re.match(r"DisplayText\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", raw_value)
    if dt_match:
        tag = dt_match.group(1).strip().upper()
        value = dt_match.group(3).strip()
        value = substitute_variables(value)
        # Route according to tag
        if tag == "DIRECT":
            # do not print to shell; write to simulated hardware
            send_to_hardware(value)
            # store the raw value in variable as well, in case code expects it
            variables[var_name] = value
        elif tag == "SHELL":
            display_to_shell(value)
            variables[var_name] = value
        else:
            # Unknown tag: default to shell and warn
            print(f"[WARN] Unknown DisplayText tag '{tag}', defaulting to SHELL")
            display_to_shell(value)
            variables[var_name] = value
        return

    # Fallback: normal value or variable reference
    if raw_value.startswith('"') and raw_value.endswith('"'):
        variables[var_name] = substitute_variables(raw_value.strip('"'))
    else:
        variables[var_name] = parse_value(raw_value)


def handle_display(line):
    # line format: DisplayText(TAG)=<content>
    match = re.match(r"DisplayText\((.*?)\)\s*=\s*([\"'])(.*)\2\s*$", line)
    if not match:
        print(f"[ERROR] Invalid DisplayText syntax (must be quoted): {line}")
        return

    tag = match.group(1).strip().upper()
    content = match.group(3).strip()
    content = substitute_variables(content)

    if tag == "SHELL":
        display_to_shell(content)
    elif tag == "DIRECT":
        # Do not print to shell; write to simulated hardware
        send_to_hardware(content)
    else:
        print(f"[WARN] Unknown DisplayText tag '{tag}', defaulting to SHELL")
        display_to_shell(content)

def handle_input():
    user_input = input("> ").strip()
    variables["RAWINPUT"] = user_input
    normalized = normalize_input(user_input)
    variables["INPUT"] = normalized
    set_word_vars(normalized)
    print(f"[DEBUG] INPUT received: '{user_input}'")  # You can remove this later

def handle_input_instant():
    if msvcrt is None:
        print("[ERROR] INSTANT input is only supported on Windows (msvcrt missing).")
        return
    ch = msvcrt.getwch()
    variables["RAWINPUT"] = ch
    normalized = normalize_input(ch)
    variables["INPUT"] = normalized
    set_word_vars(normalized)

def handle_input_noblock():
    if msvcrt is None:
        print("[ERROR] NOBLOCK input is only supported on Windows (msvcrt missing).")
        return
    if msvcrt.kbhit():
        ch = msvcrt.getwch()
        variables["RAWINPUT"] = ch
        normalized = normalize_input(ch)
        variables["INPUT"] = normalized
        set_word_vars(normalized)
    else:
        variables["RAWINPUT"] = ""
        variables["INPUT"] = ""
        set_word_vars("")

def handle_if(line):
    try:
        # Prefer quoted RHS: If[VAR]="value" or If[VAR]='value'
        match = re.match(r"If\[(.*?)\]\s*=\s*[\"'](.*)[\"']\s*$", line)
        if match:
            left = match.group(1).strip()
            right = match.group(2)
        else:
            # Backwards-compatible: accept unquoted RHS like If[VAR]=value (warn)
            match2 = re.match(r"If\[(.*?)\]\s*=\s*(\S+)\s*$", line)
            if not match2:
                print(f"[ERROR] Invalid If condition: '{line}'. Expected format: If[VAR]=\"value\"")
                return False
            left = match2.group(1).strip()
            right = match2.group(2)
            print(f"[WARN] If RHS not quoted: treating '{right}' as string")

        left_val = variables.get(left, "").strip()
        return left_val == right

    except Exception as e:
        print(f"[ERROR] Failed to parse If condition: {e}")
        return False

def skip_if_block(start_index):
    """Skip to matching Else or EndIf for the If at start_index.
    Supports nested If/EndIf pairs.
    Returns the index of Else or EndIf (or len(program_lines) if not found).
    """
    depth = 0
    i = start_index + 1
    while i < len(program_lines):
        l = program_lines[i].strip()
        if l.startswith("If[") and "=" in l:
            depth += 1
        elif l == "EndIf":
            if depth == 0:
                return i
            depth -= 1
        elif l == "Else" and depth == 0:
            return i
        i += 1
    return i

def skip_to_endif(start_index):
    """Skip from an Else line to its matching EndIf."""
    depth = 0
    i = start_index + 1
    while i < len(program_lines):
        l = program_lines[i].strip()
        if l.startswith("If[") and "=" in l:
            depth += 1
        elif l == "EndIf":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    return i


def handle_loop(start_index):
    global current_line
    loop_lines = []
    i = start_index + 1
    while i < len(program_lines) and not program_lines[i].strip().startswith("EndLoop"):
        loop_lines.append(program_lines[i])
        i += 1
    while True:
        for line in loop_lines:
            execute_line(line)
    current_line = i  # Will never reach this due to infinite loop

def handle_goto(label):
    global current_line
    if label in labels:
        current_line = labels[label]
    else:
        print(f"[ERROR] Label '{label}' not found.")
        sys.exit(1)

def handle_call(func_name):
    if func_name not in functions:
        print(f"[ERROR] Function '{func_name}' not found.")
        return
    for line in functions[func_name]:
        execute_line(line)

# ------------------------------
# Interpreter
# ------------------------------
def execute_line(line):
    global current_line

    line = line.strip()
    line = strip_inline_comment(line)
    if not line or line.startswith("//"):
        return  

    # Structural-only keywords: tolerate optional spaces in the bit declaration
    if line.replace(" ", "") in ("[16BIT]", "startprogram", "endprogram", "startsection", "endsection"):
        return

    if line.startswith("Set["):
        handle_set(line)

    elif line.startswith("DisplayText(DIRECT)=") or line.startswith("DisplayText(SHELL)="):
        handle_display(line)

    elif line.startswith("WriteFile"):
        match = re.match(r"WriteFile(?:\[(.*?)\])?\s*(?:=\s*(.*))?$", line)
        if not match:
            print(f"[ERROR] Invalid WriteFile syntax: {line}")
            return
        path_token = match.group(1)
        rhs = match.group(2)
        if path_token is None and rhs is not None:
            # Allow: WriteFile= "path"
            file_path = parse_path_token(rhs)
            content = ""
        else:
            file_path = parse_path_token(path_token)
            content = parse_token_value(rhs)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[ERROR] WriteFile failed: {e}")

    elif line.startswith("AppendFile"):
        match = re.match(r"AppendFile\[(.*?)\]\s*=\s*(.*)$", line)
        if not match:
            print(f"[ERROR] Invalid AppendFile syntax: {line}")
            return
        file_path = parse_path_token(match.group(1))
        content = parse_token_value(match.group(2))
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            print(f"[ERROR] AppendFile failed: {e}")

    elif line.startswith("TrackInput[KEYBOARD]"):
        if re.match(r"TrackInput\[\s*KEYBOARD\s*\]\s*=\s*INSTANT\s*$", line, flags=re.IGNORECASE):
            handle_input_instant()
        elif re.match(r"TrackInput\[\s*KEYBOARD\s*\]\s*=\s*NOBLOCK\s*$", line, flags=re.IGNORECASE):
            handle_input_noblock()
        else:
            handle_input()

    elif line.startswith("If[") and "=" in line:
        if not handle_if(line):
            current_line = skip_if_block(current_line)
            if current_line < len(program_lines):
                if program_lines[current_line].strip() in ("Else", "EndIf"):
                    return

    elif line == "Else":
        current_line = skip_to_endif(current_line)
        return

    elif line == "EndIf":
        return

    elif line.startswith("Loop[FOREVER]"):
        handle_loop(current_line)

    elif line.startswith("Goto["):
        label = line.split("Goto[", 1)[1].split("]", 1)[0]
        handle_goto(label)

    elif line.startswith("CallFunction["):
        func = line.split("CallFunction[", 1)[1].split("]", 1)[0]
        handle_call(func)

    elif line.startswith("SetColor["):
        match = re.match(r"SetColor\[(FG|BG)\]\s*=\s*(.*)$", line, re.IGNORECASE)
        if not match:
            print(f"[ERROR] Invalid SetColor syntax: {line}")
            return
        tag = match.group(1).upper()
        value = parse_token_value(match.group(2))
        set_color(tag, value)

    elif line == "ResetColor":
        reset_color()

    elif line.startswith("DrawBox["):
        match = re.match(r"DrawBox\[(\d+)\s*,\s*(\d+)\]\s*=\s*(.*)$", line)
        if not match:
            print(f"[ERROR] Invalid DrawBox syntax: {line}")
            return
        width = match.group(1)
        height = match.group(2)
        ch = parse_token_value(match.group(3))
        draw_box(width, height, ch)

    elif line == "ClearScreen":
        clear_screen()

    elif line.startswith("SetCursor["):
        match = re.match(r"SetCursor\[(.*?)\s*,\s*(.*?)\]\s*$", line)
        if not match:
            print(f"[ERROR] Invalid SetCursor syntax: {line}")
            return
        row = parse_token_value(match.group(1))
        col = parse_token_value(match.group(2))
        try:
            set_cursor(int(row), int(col))
        except ValueError:
            print("[ERROR] SetCursor requires integer row and column")

    elif line.startswith("TickTimer["):
        match = re.match(r"TickTimer\[(.*?)\]\s*$", line)
        if not match:
            print(f"[ERROR] Invalid TickTimer syntax: {line}")
            return
        ms = parse_token_value(match.group(1))
        tick_timer(ms)

    elif line.startswith("StartFunction[") or line == "EndFunction":
        return  # Already handled in preload

    elif line.startswith("Label[") and "]" in line:
        return  # Already stored
    elif line.startswith("Label:"):
        # backward compatibility: warn and ignore at runtime
        print("[WARN] Deprecated label syntax 'Label:NAME' used; prefer 'Label[NAME]'")
        return  # Already stored

    else:
        print(f"[ERROR] Unknown command: {line}")

# ------------------------------
# Program Loader (First Pass)
# ------------------------------
def load_program(file_path):
    global program_lines, labels, functions

    with open(file_path, "r") as f:
        raw_lines = f.readlines()

    in_function = False
    current_func = ""

    # We'll build a filtered program_lines that excludes function bodies so they are
    # not executed during the main run. Labels must be indexed against the filtered list.
    program_lines = []
    labels = {}
    functions = {}

    for line in raw_lines:
        stripped = line.strip()
        stripped = strip_inline_comment(stripped)
        if not stripped or stripped.startswith("//"):
            continue

        if stripped.startswith("StartFunction["):
            in_function = True
            current_func = stripped.split("StartFunction[", 1)[1].split("]", 1)[0]
            functions[current_func] = []
            continue

        if stripped == "EndFunction":
            in_function = False
            current_func = ""
            continue

        if in_function:
            # collect function body lines (already stripped)
            if stripped and not stripped.startswith("//"):
                functions[current_func].append(stripped)
            continue

        # Not inside a function: treat as part of the main program
        # New preferred syntax: Label[NAME]
        if stripped.startswith("Label[") and "]" in stripped:
            label_name = stripped.split("Label[", 1)[1].split("]", 1)[0].strip()
            labels[label_name] = len(program_lines)
        elif stripped.startswith("Label:"):
            # Backwards compatibility: accept old form but warn
            label_name = stripped[6:].strip()
            print(f"[WARN] Deprecated label syntax 'Label:NAME' used for '{label_name}'; prefer 'Label[{label_name}]'")
            labels[label_name] = len(program_lines)

        program_lines.append(stripped)

# ------------------------------
# Runner
# ------------------------------
def run_program():
    global current_line
    current_line = 0
    while current_line < len(program_lines):
        line = program_lines[current_line].strip()
        execute_line(line)
        current_line += 1

# ------------------------------
# Compiler
# ------------------------------
BOOT_SECTOR_SIZE = 512
BOOT_SIGNATURE = b"\x55\xAA"

# Instruction templates (x86 real-mode)
# mov ah, imm8  -> B4 imm8
# mov al, imm8  -> B0 imm8
# int 0x10      -> CD 10
# jmp short -2  -> EB FE  (infinite loop)

MOV_AH = b"\xB4"  # followed by imm8
MOV_AL = b"\xB0"  # followed by imm8
INT_10 = b"\xCD\x10"
JMP_LOOP = b"\xEB\xFE"


def compiler_substitute_variables(text, compiler_vars):
    """Replace <`VAR`> with values from compiler_vars."""
    matches = re.findall(r"<`(.*?)`>", text)
    for var in matches:
        value = compiler_vars.get(var, f"<UNDEFINED:{var}>")
        text = text.replace(f"<`{var}`>", value)
    return text


def compiler_parse_token_value(token, compiler_vars):
    """Parse a token that may be quoted or contain <`VAR`> in compiler mode."""
    if token is None:
        return ""
    token = token.strip()
    if token.startswith('"') and token.endswith('"'):
        return compiler_substitute_variables(token.strip('"'), compiler_vars)
    token = compiler_substitute_variables(token, compiler_vars)
    return compiler_vars.get(token, token)


def compiler_eval_math(expr, compiler_vars):
    """Evaluate Math(...) during compile-time using compiler variables."""
    import ast

    expr = expr.strip()
    expr = compiler_substitute_variables(expr, compiler_vars)
    if expr.startswith('"') and expr.endswith('"'):
        expr = expr[1:-1]

    allowed_nodes = (
        ast.Expression,
        ast.BinOp,
        ast.UnaryOp,
        ast.Constant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
        ast.UAdd, ast.USub,
        ast.Load,
    )

    def _eval(node):
        if not isinstance(node, allowed_nodes):
            raise ValueError("Invalid math expression")
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("Only numeric constants allowed")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Invalid unary operator")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right
            raise ValueError("Invalid binary operator")
        raise ValueError("Invalid math expression")

    tree = ast.parse(expr, mode="eval")
    return _eval(tree)


def extract_top_level_display_direct(lines):
    """Return list of strings to print (in order) based on top-level statements.
    Supports compile-time Set/If evaluation for DisplayText(DIRECT/SHELL).
    Enforces quoted DisplayText values; raises on unquoted occurrences to align with interpreter.
    """
    out = []
    in_function = False
    compiler_vars = {}
    condition_stack = []

    def is_active():
        return all(condition_stack) if condition_stack else True

    for raw in lines:
        line = strip_inline_comment(raw.strip())
        if not line or line.startswith("//"):
            continue
        # track function blocks and skip their contents
        if line.startswith("StartFunction["):
            in_function = True
            continue
        if line == "EndFunction":
            in_function = False
            continue
        if in_function:
            continue

        if line.startswith("If[") and "=" in line:
            match = re.match(r"If\[(.*?)\]\s*=\s*[\"'](.*)[\"']\s*$", line)
            if match:
                left = match.group(1).strip()
                right = match.group(2)
            else:
                match2 = re.match(r"If\[(.*?)\]\s*=\s*(\S+)\s*$", line)
                if not match2:
                    raise ValueError(f"Invalid If condition: '{line}'. Expected format: If[VAR]=\"value\"")
                left = match2.group(1).strip()
                right = match2.group(2)
            left_val = compiler_vars.get(left, "").strip()
            condition_stack.append(left_val == right)
            continue

        if line == "Else":
            if not condition_stack:
                raise ValueError("Else without matching If")
            condition_stack[-1] = not condition_stack[-1]
            continue

        if line == "EndIf":
            if not condition_stack:
                raise ValueError("EndIf without matching If")
            condition_stack.pop()
            continue

        if not is_active():
            continue

        if line.startswith("Set["):
            match = re.match(r"Set\[(.*?)\]\s*=\s*(.*)", line)
            if not match:
                raise ValueError(f"Invalid Set syntax: {line}")
            var_name = match.group(1)
            raw_value = match.group(2).strip()
            if raw_value.startswith("Math(") and raw_value.endswith(")"):
                expr = raw_value[5:-1]
                compiler_vars[var_name] = str(compiler_eval_math(expr, compiler_vars))
                continue
            if raw_value.startswith("ReadFile[") and raw_value.endswith("]"):
                path_token = raw_value[len("ReadFile["):-1]
                file_path = compiler_parse_token_value(path_token, compiler_vars)
                with open(file_path, "r", encoding="utf-8") as f:
                    compiler_vars[var_name] = f.read()
                continue
            if raw_value.startswith('"') and raw_value.endswith('"'):
                compiler_vars[var_name] = compiler_substitute_variables(raw_value.strip('"'), compiler_vars)
            else:
                compiler_vars[var_name] = compiler_parse_token_value(raw_value, compiler_vars)
            continue

        # match DisplayText(DIRECT/SHELL)="..." (require quotes)
        m = re.match(r"DisplayText\(\s*(DIRECT|SHELL)\s*\)\s*=\s*([\"'])(.*)\2\s*$", line, flags=re.IGNORECASE)
        if m:
            val = compiler_substitute_variables(m.group(3), compiler_vars)
            out.append(val)
        else:
            if re.match(r"DisplayText\(\s*(DIRECT|SHELL)\s*\)\s*=\s*\S", line, flags=re.IGNORECASE):
                raise ValueError(f"DisplayText must be quoted: {line}")
    if condition_stack:
        raise ValueError("Unclosed If block at end of file")
    return out


def generate_boot_with_input_asm(strings, asm_path):
    """Write a NASM boot sector that prints the provided strings (DIRECT) then implements
    a simple line-editor and command dispatcher (systemstat, help, quit).
    """
    # Escape double quotes in strings for NASM literal
    safe_lines = [s.replace('"', '\\"') for s in strings]

    asm_lines = [
        "bits 16",
        "org 0x7C00",
        "",
        "start:",
        "    xor ax, ax",
        "    mov ds, ax",
        "    mov es, ax",
        "    mov ss, ax",
        "    mov sp, 0x7C00",
        "",
        "    ; Print each top-level DIRECT string using BIOS teletype (int 0x10 AH=0x0E)",
    ]

    for idx, s in enumerate(safe_lines):
        lbl = f"msg_{idx}"
        asm_lines += [
            f"    mov si, {lbl}",
            f"print_{lbl}:",
            "    lodsb",
            "    cmp al, 0",
            f"    je done_{lbl}",
            "    mov ah, 0x0E",
            "    int 0x10",
            f"    jmp print_{lbl}",
            f"done_{lbl}:",
            "    mov al, 0x0D",
            "    mov ah, 0x0E",
            "    int 0x10",
            "    mov al, 0x0A",
            "    mov ah, 0x0E",
            "    int 0x10",
            ""
        ]

    # Prompt label and main loop
    asm_lines += [
        "    ; Simple prompt and command loop",
        "main_loop:",
        "    mov si, prompt",
        "    call print_string",
        "    call read_line    ; reads into 'inbuf' and returns with SI pointing to it",
        "",
        "    ; Compare input against supported commands",
        "    mov si, inbuf",
        "    mov di, cmd_systemstat",
        "    call strcmp",
        "    cmp al, 1",
        "    je do_systemstat",
        "",
        "    mov si, inbuf",
        "    mov di, cmd_help",
        "    call strcmp",
        "    cmp al, 1",
        "    je do_help",
        "",
        "    mov si, inbuf",
        "    mov di, cmd_quit",
        "    call strcmp",
        "    cmp al, 1",
        "    je do_quit",
        "",
        "    ; Unknown command",
        "    mov si, unknown_msg",
        "    call print_string",
        "    jmp main_loop",
        "",
        "do_systemstat:",
        "    mov si, systemstat_msg",
        "    call print_string",
        "    jmp main_loop",
        "",
        "do_help:",
        "    mov si, help_msg",
        "    call print_string",
        "    jmp main_loop",
        "",
        "do_quit:",
        "    mov si, quit_msg",
        "    call print_string",
        "    cli",
        "    hlt",
        "",
    ]

    # read_line: fills inbuf, returns with SI pointing to inbuf (for convenience)
    asm_lines += [
        "; ---------------- read_line ----------------",
        "; Returns with SI=inbuf (pointer to start of buffer)",
        "read_line:",
        "    mov di, inbuf",
        "    mov bx, di    ; save start in BX for backspace checks",
        "read_char_loop:",
        "    mov ah, 0x00",
        "    int 0x16       ; wait for key, ASCII in AL",
        "    cmp al, 0x0D   ; Enter?",
        "    je read_done",
        "    cmp al, 0x08   ; Backspace?",
        "    je do_backspace",
        "    ; Echo character",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    stosb           ; store char into [di] and inc di",
        "    jmp read_char_loop",
        "",
        "do_backspace:",
        "    cmp di, bx",
        "    je read_char_loop    ; nothing to delete",
        "    dec di",
        "    ; Erase on screen: backspace, space, backspace",
        "    mov al, 0x08",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    mov al, 0x20",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    mov al, 0x08",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    jmp read_char_loop",
        "",
        "read_done:",
        "    mov byte [di], 0x00    ; null-terminate",
        "    ; print CR LF",
        "    mov al, 0x0D",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    mov al, 0x0A",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    mov si, inbuf",
        "    ret",
        "",
    ]

    # strcmp: compares strings at SI and DI, returns AL=1 if equal else AL=0
    asm_lines += [
        "; ---------------- strcmp ----------------",
        "; Inputs: SI->str1, DI->str2. Returns AL=1 if equal, AL=0 otherwise",
        "strcmp:",
        "    .cmp_loop:",
        "    mov al, [si]",
        "    mov bl, [di]",
        "    cmp al, bl",
        "    jne .not_equal",
        "    cmp al, 0",
        "    je .equal",
        "    inc si",
        "    inc di",
        "    jmp .cmp_loop",
        "    .not_equal:",
        "    mov al, 0",
        "    ret",
        "    .equal:",
        "    mov al, 1",
        "    ret",
        "",
    ]

    # print_string: SI->zero-terminated string
    asm_lines += [
        "; ---------------- print_string ----------------",
        "print_string:",
        "    .print_loop:",
        "    lodsb",
        "    cmp al, 0",
        "    je .ret_ps",
        "    mov ah, 0x0E",
        "    int 0x10",
        "    jmp .print_loop",
        "    .ret_ps:",
        "    ret",
        "",
    ]

    # Data: inbuf and command strings and canned responses
    asm_lines += [
        "; ---------------- data ----------------",
        "inbuf: times 80 db 0",
        "prompt db '>', 0",
        "cmd_systemstat db 'systemstat',0",
        "cmd_help db 'help',0",
        "cmd_quit db 'quit',0",
        "unknown_msg db 'Unknown command',0",
        "systemstat_msg db 'System Status: OK',0",
        "help_msg db 'Available commands: systemstat, help, quit',0",
        "quit_msg db 'Goodbye! Halting...',0",
    ]

    # Preserve any top-level messages as well (append original messages as data labels)
    for idx, s in enumerate(safe_lines):
        lbl = f"msg_{idx}"
        asm_lines.append(f"{lbl} db \"{s}\", 0")

    asm_lines += [
        "",
        "times 510 - ($ - $$) db 0",
        "dw 0xAA55",
    ]

    with open(asm_path, "w", encoding="utf-8") as f:
        f.write("\n".join(asm_lines))


def compile_to_boot_sector(source_file, output_file):
    if not os.path.isfile(source_file):
        print(f"Source file not found: {source_file}")
        sys.exit(1)

    with open(source_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    strings = extract_top_level_display_direct(lines)

    if not strings:
        print("No top-level DisplayText(DIRECT)=... entries found to compile.")
        print("(This compiler only emits DIRECT output into an early boot sector for now.)")
        sys.exit(0)

    # If the caller asked for a .bin output, emit a small NASM asm that prints the strings
    # then echoes keyboard input, assemble it with nasm, and write the binary.
    asm_path = os.path.splitext(output_file)[0] + ".asm"
    try:
        generate_boot_with_input_asm(strings, asm_path)
    except Exception as e:
        print(f"[ERROR] Failed to generate asm: {e}")
        sys.exit(1)

    # Assemble with nasm if available
    try:
        import subprocess
        res = subprocess.run(["nasm", "-f", "bin", asm_path, "-o", output_file], check=False)
        if res.returncode != 0:
            print("[ERROR] nasm failed to assemble the boot asm. Ensure nasm is installed and on PATH.")
            sys.exit(1)
    except FileNotFoundError:
        print("[ERROR] nasm not found. Install nasm or assemble the generated .asm manually.")
        sys.exit(1)

    # Report success
    size = os.path.getsize(output_file)
    print(f"Wrote bootable image: {output_file} ({size} bytes)")
    print("You can boot it in QEMU: qemu-system-i386 -drive format=raw,file=%s" % output_file)

# ------------------------------
# Entry Point
# ------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python longc.py <program.long> [output.bin]")
        sys.exit(1)

    if len(sys.argv) == 3 and sys.argv[2].endswith(".bin"):
        compile_to_boot_sector(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2 and sys.argv[1].endswith(".long"):
        compile_to_boot_sector(sys.argv[1], "boot.bin")
    else:
        load_program(sys.argv[1])
        run_program()
