import re


def normalize_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def normalize_name_simple(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def build_search_variants(name):
    variants = {name}
    if "-" in name:
        variants.add(name.replace("-", "."))
        variants.add(name.replace("-", "_"))
        variants.add(name.replace("-", ""))
    if "_" in name:
        variants.add(name.replace("_", "-"))
        variants.add(name.replace("_", "."))
        variants.add(name.replace("_", ""))
    if "." in name:
        variants.add(name.replace(".", "-"))
        variants.add(name.replace(".", "_"))
        variants.add(name.replace(".", ""))
    return [v for v in variants if v]

