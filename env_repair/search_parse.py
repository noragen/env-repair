def collect_name_fields(obj, names):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "name" and isinstance(v, str):
                names.add(v)
            collect_name_fields(v, names)
    elif isinstance(obj, list):
        for item in obj:
            collect_name_fields(item, names)


def extract_search_results(data):
    if not isinstance(data, dict):
        return []
    if isinstance(data.get("result"), dict):
        result = data["result"]
        pkgs = result.get("pkgs")
        if isinstance(pkgs, list):
            names = []
            for item in pkgs:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.append(item["name"])
            return names
        if all(isinstance(v, list) for v in result.values()):
            return list(result.keys())
        names = []
        for v in result.values():
            if isinstance(v, list):
                for item in v:
                    if isinstance(item, dict) and isinstance(item.get("name"), str):
                        names.append(item["name"])
        return names
    if isinstance(data.get("pkgs"), dict):
        return list(data["pkgs"].keys())
    if isinstance(data.get("packages"), dict):
        return list(data["packages"].keys())
    if isinstance(data.get("result"), list):
        names = []
        for item in data["result"]:
            if isinstance(item, dict):
                name = item.get("name")
                if name:
                    names.append(name)
                    continue
                rec = item.get("record") if isinstance(item.get("record"), dict) else None
                if rec and isinstance(rec.get("name"), str):
                    names.append(rec["name"])
                continue
            if isinstance(item, str):
                names.append(item)
        return names
    if all(isinstance(v, list) for v in data.values()):
        return list(data.keys())
    return []


def parse_search_output(data):
    results = extract_search_results(data)
    if results:
        return results
    names = set()
    collect_name_fields(data, names)
    return sorted(names)

