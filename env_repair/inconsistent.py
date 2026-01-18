import re


def parse_inconsistent(output_text):
    """
    Best-effort parse for "environment is inconsistent" warnings.
    Returns (is_inconsistent: bool, packages: list[str]).
    """
    if not output_text:
        return False, []
    text = output_text.lower()
    if "environment is inconsistent" not in text and "the environment is inconsistent" not in text:
        return False, []

    pkgs = set()
    # Heuristic: some outputs list packages as bullet points or in "package name" tokens.
    for line in output_text.splitlines():
        if "inconsistent" in line.lower():
            continue
        m = re.match(r"^\s*[-*]\s*([A-Za-z0-9_.-]+)\b", line)
        if m:
            pkgs.add(m.group(1))
            continue
        # Fallback: "caused by: <pkg>" type patterns
        m2 = re.search(r"\b([A-Za-z0-9_.-]+)\b\s*(?:is|are)\s*(?:causing|inconsistent)", line, flags=re.I)
        if m2:
            pkgs.add(m2.group(1))
    return True, sorted(pkgs)
