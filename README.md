# LONG
An easy programming language based on English.

## Compiler notes
The boot-sector compiler currently emits assembly that prints the evaluated results of
top-level `DisplayText(DIRECT|SHELL)=...` statements. During compilation, it can process
top-level `Set[...]` assignments and `If/Else/EndIf` blocks (including `Math(...)` and
`ReadFile[...]`) so those values can be substituted into the emitted output.
