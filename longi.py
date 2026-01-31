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
        # Toggle quote states
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if not in_single and not in_double:
            # Check for //
            if ch == '/' and i + 1 < len(line) and line[i+1] == '/':
                return line[:i].rstrip()
            # Check for #
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
    # Remove inline comments outside quotes first
    line = strip_inline_comment(line)
    # Treat lines starting with '//' as comments
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
            # If we landed on Else/EndIf, skip its line too.
            if current_line < len(program_lines):
                if program_lines[current_line].strip() in ("Else", "EndIf"):
                    # run_program will increment after this returns
                    return

    elif line == "Else":
        # Skip Else block if we reached it (meaning the If was true)
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

    # Accept new Label[...] syntax; also accept old Label:NAME for compatibility
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
        # Remove inline comments outside quotes first
        stripped = strip_inline_comment(stripped)
        # Skip full-line or empty lines after stripping
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
# Entry Point
# ------------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python longc.py <program.long>")
        sys.exit(1)

    load_program(sys.argv[1])
    run_program()
